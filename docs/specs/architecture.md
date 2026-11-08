# Architecture

## Purpose

How dexSTASH is put together, so a change lands in the right layer and keeps pages fast on a small server.
[docs/architecture.md](../architecture.md) is the detailed code tour with diagrams; this spec holds the
statements a change must keep true.

## Behaviour

### Processes and containers

1. The app runs in the `dexstash` container as one Uvicorn worker on port 8000, on purpose: the login lockout,
   the background threads and the job runner live in that process's memory.
2. Photo reading, Enhanced Search, picture lookups and fingerprinting run where `DEXSTASH_SCANNER` says:
   `separate` (the `dexstash-scanner` container, Compose profile `scanner`), `inside` (a child process of the
   app at the lowest priority, refused under 1.5 GB of memory) or `off`. Unset, the Scanning switch in Data
   sources decides between `off` (the default) and `inside`.
3. Both containers share the `data/` folder. The app container has a memory cap (`DEXSTASH_APP_MEMORY`,
   2 GB unless changed) and gives way to other containers when the CPU is busy; the scanner gives way to the app.

### Web layer

4. `app/main.py` builds the app: the Origin check on forms, sessions, the proxy's visitor address and https
   (trusted only from `DEXSTASH_PROXY_IPS`), the static files and the routers.
5. Pages live in `app/routes/`, one module and `APIRouter` per topic. What several modules share is in
   `app/web.py`. Logic lives in plain modules in `app/` that take a database connection.
6. Pages are Jinja templates with htmx. There is no front-end framework and no JavaScript build step. Each
   template's CSS and JavaScript are their own files under `app/static/`, linked through `asset()`.
7. Every HTML page is sent with a strict Content-Security-Policy: scripts only from the app's own files, no
   inline scripts or `on…=` handlers.
8. `tests/routes.txt` lists every route; a test fails when a route is added, moved or removed without
   updating it.

### Data

9. Two SQLite files in WAL mode: `dexstash.db` holds the household's own data (`app/schema.sql`), and
   `dexstash-catalog.db` holds what can be downloaded again (`app/catalog.sql`), attached to every connection
   as `catalog`. Backups hold only the first; Data sources can rebuild the second.
10. A database change is a new numbered `_step_N` in `app/db.py` that raises `LATEST`. Steps run once, in
    order, each all or nothing, after a backup. An older version refuses to open a database a newer one has
    changed.
11. Quick after-upgrade tasks are numbered in `app/upgrades.py` and run once each.
12. Times are stored one way, UTC as `2026-10-28T19:56:42Z`, through `app/clock.py`; pages show them in the
    install's time zone.

### Outside sites and prices

13. Every request to another site, from either container, goes through `app/sites.py`, with one honest
    User-Agent. A site switched off in Data sources is refused, redirects included.
14. A shown price comes only from `prices.resolve`: each source writes quotes to `price_quotes`, and
    `resolve` stores each printing's average, range and sources in `prices`, in US dollars and in the
    install's currency with the rate used. Pages read stored prices and never convert while drawing.

### Background work

15. Every long piece of work is a row in `jobs` and runs on its process's one runner, one job at a time:
    jobs a person started first, then scheduled ones, oldest first.
16. Pages come first: while a page is being answered, a job waits before its next step or download, a few
    seconds at most each time. Status polls don't count as pages.
17. A restart stops every job that was running or waiting ("Stopped by a restart"); scans carry on. A night
    missed while the server was off isn't caught up.
18. Startup (`app/startup.py`) runs settings, the backup, the database steps and the quick upgrade tasks
    before the first page, then the slow tasks in the background. If the part before the first page fails,
    every address shows a page naming the error and the backup to put back.

## Rules and limits

- Never add a second web worker without moving the in-memory state (lockout, runner, threads) somewhere
  shared first.
- Never change an existing database step.
- No new package unless it works on amd64 and arm64 on a small home server, is pinned in `requirements.lock`
  and passes `pip-audit`.
- Tests never touch the network.

## Interfaces

- Code: `app/main.py`, `app/web.py`, `app/routes/`, `app/db.py`, `app/schema.sql`, `app/catalog.sql`,
  `app/jobs.py`, `app/startup.py`, `app/sites.py`, `app/prices.py`, `app/scan_worker.py`, `app/scanning.py`.
- Containers and settings: `docker-compose.yml`, `Dockerfile`, `.env.example`.
- Diagrams: `docs/architecture-overview.svg` and `docs/architecture-detailed.svg`, drawn by
  `tools/make_diagrams.py`.
- Related specs: [overview](overview.md); the data model, data sources, prices, scanning and deploy specs
  once written.

## Open questions

- None yet.
