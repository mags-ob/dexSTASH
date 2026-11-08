"""Running the scanner inside the app's own container (DEXSTASH_SCANNER=inside), and the light picture
work the scanner does, run here when scanning is off.

inside: the app starts `python -m app.scan_worker` as a child process at the lowest priority, so pages and searches
always come first. If the system runs short of memory it stops the child before the app, the app starts it again
(waiting longer after each failure), and the child stops with the app. Under about 1.5 GB of memory the child
isn't started at all: reading photos needs that much, and the app would be the one to suffer.

off: reading photos and Enhanced Search don't run. Looking up pictures for followed Pokémon's cards (without
comparing artwork) runs on one background thread of the app at the lowest priority.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import card_pictures, db

log = logging.getLogger("dexstash.scanning")

MIN_MEMORY = 1536 * 1024 * 1024  # bytes: below this the photo reader isn't started inside the app's container
FIRST_WAIT, LONGEST_WAIT = 5, 300  # seconds before starting a child that stopped, doubling each time
# Only the app asks CardTrader for prices or signs anyone in, so the reader never gets these (like the scanner container).
KEPT_FROM_THE_READER = ("DEXSTASH_CARDTRADER_TOKEN", "DEXSTASH_ADMIN_PASSWORD")
_CGROUP_FILES = (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"))


def memory_limit(files=_CGROUP_FILES) -> int | None:
    """The container's memory limit in bytes, or None when there's none (or it can't be read)."""
    for path in files:
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if text.isdigit() and int(text) < 1 << 60:  # cgroup v1 writes a huge number for "no limit"
            return int(text)
        return None
    return None


def too_little_memory(limit=None) -> str | None:
    """Why the photo reader can't run inside this container (its memory cap is under 1.5 GB), or None."""
    cap = (limit or memory_limit)()
    if cap is None or cap >= MIN_MEMORY:
        return None
    return (f"This container has {cap / 1024 ** 3:.1f} GB of memory and reading photos inside it needs about 1.5 GB. "
            "Raise DEXSTASH_APP_MEMORY in .env (2g is the default) and run docker compose up -d, "
            "or use the separate scanner container.")


def _lowest_priority_and_die_with_parent() -> None:
    """In the child, before it runs: the lowest priority, and stopped by the system if the app goes away."""
    try:
        os.nice(19)
    except OSError:
        pass
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


class Child:
    """The photo reader as a child process of the app (inside mode)."""

    def __init__(self, command=None, limit=memory_limit, popen=subprocess.Popen):
        self.command = command or [sys.executable, "-m", "app.scan_worker"]
        self._limit, self._popen = limit, popen
        self.process = None
        self.refused: str | None = None  # why it isn't started, for Data sources
        self.restarts = 0
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        limit = self._limit()
        if limit is not None and limit < MIN_MEMORY:
            self.refused = (f"Photos aren't read: this container has {limit / 1024 ** 3:.1f} GB of memory and reading "
                            "photos needs about 1.5 GB. Give it more, or use the separate scanner container.")
            log.warning(self.refused)
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._keep_running, daemon=True, name="scanner-child")
        self._thread.start()

    def _keep_running(self) -> None:
        wait = FIRST_WAIT
        while not self._stopping.is_set():
            started = time.monotonic()
            env = {**os.environ, "OMP_NUM_THREADS": "1"}  # the reader uses one thread (two at most, while it loads)
            for key in KEPT_FROM_THE_READER:
                env.pop(key, None)
            try:
                self.process = self._popen(self.command, env=env, preexec_fn=_lowest_priority_and_die_with_parent)
            except OSError as e:
                log.error("photo reader couldn't start: %s", e)
                self.process = None
            if self.process is not None:
                log.info("photo reader started inside the app (pid %s)", self.process.pid)
                code = self.process.wait()
                if self._stopping.is_set():
                    return
                log.warning("photo reader stopped (exit %s); starting it again in %s s", code, wait)
            self.restarts += 1
            if time.monotonic() - started > LONGEST_WAIT:  # it ran a good while: a fresh failure, not a loop
                wait = FIRST_WAIT
            if self._stopping.wait(wait):
                return
            wait = min(wait * 2, LONGEST_WAIT)

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 10) -> None:
        self._stopping.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout)


class LightWork:
    """Off mode: followed Pokémon's card pictures, a few at a time, on one lowest-priority thread."""

    def __init__(self, every: float = 30, pause: float | None = None, enabled: bool = True):
        self.every, self.enabled = every, enabled
        self.pause = card_pictures.PAUSE if pause is None else pause
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.native_id: int | None = None

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="light-picture-work")
        self._thread.start()

    def _run(self) -> None:
        self.native_id = threading.get_native_id()
        try:
            os.setpriority(os.PRIO_PROCESS, self.native_id, 19)  # this thread only: pages keep their priority
        except (OSError, AttributeError):
            pass
        while not self._stopping.wait(self.every):
            self.round()

    def round(self) -> int:
        """One small batch; returns how many cards were looked at."""
        conn = db.connect()
        done = 0
        try:
            for card_id in card_pictures.followed_due(conn):
                if self._stopping.is_set():
                    break
                try:
                    card_pictures.check_followed(conn, card_id)
                except Exception:
                    log.exception("followed picture for %s failed", card_id)
                done += 1
                time.sleep(self.pause)
        finally:
            conn.close()
        return done

    def stop(self) -> None:
        self._stopping.set()
