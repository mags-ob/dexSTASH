"""Background work, one job at a time (round 23).

Every long piece of work is a job: a row in the jobs table, shown in the "Running now" box on Data sources.
Each process has one runner, which takes the next waiting job of its own (runs_in 'app' for the web app, 'scanner'
for the scanner container when the job needs the picture model): jobs you asked for first, then scheduled and
automatic ones, oldest first. A job started in this process runs the function it was given; a row another process
added (the app asking the scanner to fingerprint sets) runs by its kind.

Pages come first: while a page is being answered, background work waits between its steps and downloads
(pause_for_pages), a few seconds at most each time, so it still gets done.

A restart stops everything that was running or waiting ("Stopped by a restart"); you start it again yourself.
Only scans carry on (scans.resume)."""

import logging
import os
import threading
import time
from datetime import timedelta

from . import clock, db

log = logging.getLogger("dexstash.jobs")
KEEP_FINISHED = 600  # finished jobs stay in the box this many seconds
YOURS, SCHEDULED = 0, 1  # priority: what you asked for goes first
RESTARTED = "Stopped by a restart"
NICE = 15  # the runner's thread: below pages (0) and the scanner reading photos (10)


# --- pages first (J5) ---------------------------------------------------------------------------------------

PAGE_WAIT = 3.0    # seconds background work waits for pages at most, each time
AFTER_PAGE = 0.3   # and a moment after the last page was answered
_pages = {"now": 0, "ended": 0.0}
_pages_lock = threading.Lock()
_local = threading.local()


def page_started() -> None:
    with _pages_lock:
        _pages["now"] += 1


def page_ended() -> None:
    with _pages_lock:
        _pages["now"] -= 1
        _pages["ended"] = time.monotonic()


def pages_now() -> int:
    return _pages["now"]


def mark_background() -> None:
    """This thread does background work: it waits for pages (also a download pool's threads inside a job)."""
    _local.background = True


def in_background() -> bool:
    return getattr(_local, "background", False)


def pause_for_pages(longest: float = PAGE_WAIT, sleep=time.sleep) -> float:
    """On a background thread: wait while pages are being answered, and a moment after, up to longest seconds.
    A page's own thread never waits. Returns the seconds waited."""
    if not in_background():
        return 0.0
    waited = 0.0
    while waited < longest and (_pages["now"] > 0 or time.monotonic() - _pages["ended"] < AFTER_PAGE):
        sleep(0.05)
        waited += 0.05
    return waited


# --- one job ------------------------------------------------------------------------------------------------

class Job:
    """A job this process started. The pages read it while it runs; its progress is also written to its row."""

    def __init__(self, key: str, label: str, job_id: int | None = None, priority: int = YOURS):
        self.key, self.label, self.id, self.priority = key, label, job_id, priority
        self.done, self.total = 0, 0
        self.step = ""
        self.error: str | None = None
        self.result: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.waiting = True
        self._ended = threading.Event()
        self._write = None  # (done, total, step) -> None, while the runner runs it
        self._written = 0.0
        self._between = None  # the runner's quick jobs, run between this job's steps

    def progress(self, done: int, total: int | None = None, step: str | None = None) -> None:
        self.done = done
        if total is not None:
            self.total = total
        if step is not None:
            self.step = step
        if self._write is not None and (time.time() - self._written >= 1 or total is not None or step is not None):
            self._written = time.time()
            try:
                self._write(self.done, self.total, self.step)
            except Exception as e:  # the box can show old progress for a moment
                log.debug("progress not saved: %s", e)
        if self._between is not None:
            self._between()
        pause_for_pages()

    @property
    def running(self) -> bool:
        return self.finished is None

    def wait(self, timeout: float | None = None) -> bool:
        return self._ended.wait(timeout)

    def describe(self) -> str:
        text = self.label + (f": {self.step}" if self.step and not self.waiting else "")
        if self.waiting:
            return text + " (waiting its turn)"
        if self.running and self.total:
            text += f" {self.done} of {self.total}"
        return text


class StoredJob:
    """A job read from its row (another process's, or from before this process started); looks like a Job."""

    def __init__(self, row):
        self.id, self.key, self.label, self.priority = row["id"], row["key"], row["label"], row["priority"]
        self.done, self.total, self.step = row["done"], row["total"], row["step"] or ""
        self.error, self.result = row["error"], row["result"]
        self.started, self.finished = clock.to_unix(row["created_at"]), clock.to_unix(row["finished_at"])
        self.waiting = row["status"] == "waiting"

    running = Job.running
    describe = Job.describe


RemoteJob = StoredJob  # its name before round 23


class Progress:
    """Progress for a row only (the scanner's jobs, before round 23): job.progress() writes to it."""

    def __init__(self, conn, job_id: int):
        self.conn, self.id, self._at = conn, job_id, 0.0

    def progress(self, done: int, total: int | None = None, step: str | None = None) -> None:
        if time.time() - self._at < 1 and total is None and step is None:
            return
        self._at = time.time()
        self.conn.execute("UPDATE jobs SET done = ?, total = COALESCE(?, total), step = COALESCE(?, step) WHERE id = ?",
                          (done, total, step, self.id))
        self.conn.commit()


# --- the runner -----------------------------------------------------------------------------------------------

def stop_left_over(conn, runs_in: str) -> int:
    """At start: jobs of this process that were running or waiting stop (J6). Returns how many."""
    stopped = conn.execute("UPDATE jobs SET status = 'stopped', error = ?, finished_at = ? "
                           "WHERE runs_in = ? AND status IN ('waiting', 'running')", (RESTARTED, clock.now(), runs_in))
    conn.commit()
    if stopped.rowcount:
        log.info("%d job%s stopped by the restart", stopped.rowcount, "" if stopped.rowcount == 1 else "s")
    return stopped.rowcount


def tidy(conn, days: int = 30) -> None:
    """Nightly: finished jobs older than a month go."""
    conn.execute("DELETE FROM jobs WHERE status IN ('done', 'failed', 'stopped') AND finished_at < ?",
                 (clock.since(timedelta(days=days)),))
    conn.commit()


class Jobs:
    """This process's jobs and its one runner.

    start(): a job with a function, run here. queue(): only a row, for another process (the scanner) to run by its
    kind. Both: one job per key at a time; again=True asks for one more run when the job with that key is already
    going (picture checks, lookups), so nothing asked for meanwhile is missed. quick=True: may run between another
    job's steps (looking up a picture on the screen shouldn't wait for an hour of fingerprinting)."""

    def __init__(self, conn, runs_in: str = "app", run_kind=None, idle: float = 5.0, after=None):
        self.conn = conn  # for the pages' reads and the rows they add
        self.runs_in = runs_in
        self.run_kind = run_kind  # (conn, kind, arg, job) -> result, for rows another process added
        self.idle = idle  # seconds between looks for rows another process added
        self.after = after  # called when any job ends
        self._live: dict[int, Job] = {}
        self._fns: dict[int, object] = {}
        self._quick: set[int] = set()
        self._again: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # --- adding jobs

    def _busy(self, key: str):
        return self.conn.execute("SELECT id, status FROM jobs WHERE key = ? AND status IN ('waiting', 'running') "
                                 "ORDER BY id DESC LIMIT 1", (key,)).fetchone()

    def _insert(self, key, label, kind, arg, runs_in, priority) -> int:
        cur = self.conn.execute("INSERT INTO jobs (key, label, kind, arg, runs_in, priority, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)", (key, label, kind, arg, runs_in, priority, clock.now()))
        self.conn.commit()
        return cur.lastrowid

    def start(self, key: str, label: str, fn, wait: bool = False, priority: int = YOURS, kind: str | None = None,
              again: bool = False, quick: bool = False) -> bool:
        """Run fn(conn, job) when its turn comes, unless a job with this key is waiting or running. Returns
        whether it was added. wait: also wait for it to end (on the runner's own thread, it runs at once)."""
        with self._lock:
            busy = self._busy(key)
            if busy is not None:
                if again and busy["status"] == "running":
                    self._again[key] = (label, fn, priority, kind, quick)
                return False
            job_id = self._insert(key, label, kind or key.split(":")[0], None, self.runs_in, priority)
            job = self._live[job_id] = Job(key, label, job_id, priority)
            self._fns[job_id] = fn
            if quick:
                self._quick.add(job_id)
        if wait and threading.current_thread() is self._thread:
            self._run_now(job_id)
            return True
        self._ensure_runner()
        self._wake.set()
        if wait:
            job.wait()
        return True

    def queue(self, key: str, label: str, kind: str, arg: str | None = None, runs_in: str = "scanner",
              priority: int = YOURS) -> bool:
        """A row only, run by kind in the process it's for. False if a job with this key is waiting or running."""
        with self._lock:
            if self._busy(key) is not None:
                return False
            self._insert(key, label, kind, arg, runs_in, priority)
        if runs_in == self.runs_in:
            self._ensure_runner()
            self._wake.set()
        return True

    # --- reading them

    def running(self, key: str) -> bool:
        """Waiting or running."""
        return self._busy(key) is not None

    def last(self, key: str) -> Job | StoredJob | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if row is None:
            return None
        return self._live.get(row["id"]) or StoredJob(row)

    def current(self) -> list:
        """Running, then waiting (yours first), then ones that finished in the last few minutes, newest first."""
        now = time.time()
        with self._lock:
            for job_id, job in list(self._live.items()):
                if job.finished and now - job.finished > KEEP_FINISHED:
                    del self._live[job_id]
        rows = self.conn.execute("SELECT * FROM jobs WHERE status IN ('waiting', 'running') OR finished_at > ? "
                                 "ORDER BY id DESC", (clock.since(timedelta(seconds=KEEP_FINISHED)),)).fetchall()
        jobs = [self._live.get(r["id"]) or StoredJob(r) for r in rows]
        return sorted(jobs, key=lambda j: (0, 0, -j.started) if j.running and not j.waiting else
                      (1, j.priority, j.id) if j.running else (2, 0, -(j.finished or 0)))

    # --- the runner

    def start_runner(self) -> None:
        """Start looking for rows another process added (the scanner's runner, at its start)."""
        self._ensure_runner()

    def _ensure_runner(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stopping.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True, name=f"jobs-{self.runs_in}")
                self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def _next(self, conn, quick_only: bool = False):
        rows = conn.execute("SELECT * FROM jobs WHERE runs_in = ? AND status = 'waiting' ORDER BY priority, id",
                            (self.runs_in,)).fetchall()
        for row in rows:
            if not quick_only or row["id"] in self._quick:
                return row
        return None

    def _loop(self) -> None:
        mark_background()
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)  # this thread only
        except (OSError, AttributeError):
            pass
        conn = db.connect()
        try:
            while not self._stopping.is_set():
                self._wake.clear()
                row = self._next(conn)
                if row is None:
                    self._wake.wait(self.idle)
                    continue
                pause_for_pages()
                self._run(conn, row)
        except Exception:
            log.exception("the job runner stopped")
        finally:
            conn.close()

    def _run_now(self, job_id: int) -> None:
        conn = db.connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self._run(conn, row)
        finally:
            conn.close()

    def _between(self) -> None:
        """Between a job's steps: quick jobs that are waiting run now (on the runner's thread only)."""
        if not self._quick or threading.current_thread() is not self._thread:
            return
        conn = db.connect()
        try:
            while (row := self._next(conn, quick_only=True)) is not None:
                self._run(conn, row)
        finally:
            conn.close()

    def _run(self, conn, row) -> None:
        job_id = row["id"]
        claimed = conn.execute("UPDATE jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'waiting'",
                               (clock.now(), job_id))
        conn.commit()
        if not claimed.rowcount:
            return
        with self._lock:
            fn = self._fns.pop(job_id, None)
            self._quick.discard(job_id)
            job = self._live.get(job_id)
        if fn is None and self.run_kind is not None:
            kind, arg = row["kind"], row["arg"]
            fn = lambda c, j: self.run_kind(c, kind, arg, j)  # noqa: E731
        if job is None:
            job = self._live[job_id] = Job(row["key"], row["label"], job_id, row["priority"])
        job.waiting = False
        job.started = time.time()
        work = db.connect()

        def write(done, total, step):
            conn.execute("UPDATE jobs SET done = ?, total = ?, step = ? WHERE id = ?", (done, total, step, job_id))
            conn.commit()
        job._write = write
        job._between = self._between if job_id not in self._quick else None
        status = "done"
        try:
            if fn is None:  # a row this process can't run (it was added for a version that could)
                raise RuntimeError(RESTARTED)
            job.result = fn(work, job)
        except Exception as e:
            log.exception("%s failed", job.label)
            job.error, status = str(e) or e.__class__.__name__, "failed"
        finally:
            try:
                if work.in_transaction:
                    work.rollback()
            finally:
                work.close()
            job._write = job._between = None
            conn.execute("UPDATE jobs SET status = ?, result = ?, error = ?, done = ?, total = ?, step = ?, "
                         "finished_at = ? WHERE id = ?",
                         (status, job.result if isinstance(job.result, str) else None, job.error, job.done, job.total,
                          job.step, clock.now(), job_id))
            conn.commit()
            job.finished = time.time()
            job._ended.set()
        again = self._again.pop(job.key, None)
        if again is not None:
            label, fn, priority, kind, quick = again
            self.start(job.key, label, fn, priority=priority, kind=kind, quick=quick)
        if self.after:
            try:
                self.after()
            except Exception:
                log.exception("after %s", job.label)


def pool(workers: int):
    """A download pool. Inside a background job its threads wait for pages too; a page's own pool doesn't."""
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(max_workers=workers, initializer=mark_background if in_background() else None)
