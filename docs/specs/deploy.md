# Deploy and configuration

## Purpose

How a host installs, configures, updates and backs up dexSTASH, so every setting has one documented meaning
and an install on any small server behaves the same way. [DEPLOY.md](../../DEPLOY.md) is the step-by-step
guide; this spec holds the statements a change must keep true.

## Behaviour

### Images and installing

1. A release is one image, `ghcr.io/obiehq/dexstash`, built on GitHub for amd64 and arm64 when a Release is
   published. It's pushed as `<version>`, `<major.minor>` and `latest` (not `latest` for a pre-release). The
   release tag must match the app's version (`v0.28.0` for 0.28.0), or the build stops.
2. The same build attaches one image file per architecture to the GitHub Release
   (`dexstash-<version>-amd64.tar.gz` and `-arm64`, each with a `.sha256`), for a server that can't reach the
   registry. `docker load -i` then `DEXSTASH_VERSION=<version>` runs it without the registry.
3. Actions › Image › Run workflow builds an existing release tag again.
4. Every pull request from this repository gets a test image tagged `pr-<number>`, rebuilt on each push and
   removed when the pull request closes.
5. An install needs only two files from `main`: `docker-compose.yml` and, optionally, `.env` (started from
   `.env.example`). `docker compose up -d` fetches the image named in the compose file.
6. `DEXSTASH_VERSION` picks the image: unset is `latest`, `0.28` follows the newest 0.28.x, `0.28.0` stays on
   one release, `pr-<number>` runs a test image.
7. Building from source is the same compose file in a clone (or an unpacked release source) with
   `docker compose up -d --build`, from the `Dockerfile`.
8. The image installs the exact versions in `requirements.lock`, runs as the user `PUID`/`PGID` (1000 unless
   set) after making `data/` theirs, and reports healthy once `/healthz` answers.

### Containers and profiles

9. `docker-compose.yml` defines four services; only `app` (container `dexstash`) starts by default.
   - `scanner` (`dexstash-scanner`, profile `scanner`): the same image running `python -m app.scan_worker`. It
     starts after the app is healthy and is never sent the CardTrader token.
   - `caddy` (`dexstash-caddy`, profile `caddy`): a bundled Caddy using `deploy/Caddyfile` and
     `DEXSTASH_DOMAIN`, on ports 80 and 443.
   - `duckdns` (profile `duckdns`): a DuckDNS updater.
10. The app isn't published on a port. A reverse proxy on the same Docker network reaches it at
    `dexstash:8000`. `DEXSTASH_NETWORK` names that network (`dexstash` by default) and
    `DEXSTASH_NETWORK_EXTERNAL=true` joins one that already exists.
11. Limits: the app has a memory cap of `DEXSTASH_APP_MEMORY` (2 GB unless set), a 192 MB reservation, one
    CPU and `cpu_shares` 512. The scanner has a 1408 MB cap, one CPU and `cpu_shares` 256. Other containers
    at Docker's default shares win when the CPU is busy; the app wins over the scanner.
12. Scanning inside the app container is refused when the app's memory cap is under 1.5 GB.
13. Docker's own log for each container stops at three files of 10 MB. The app's activity log is in
    `data/logs/` (2 MB × 3 files per container, passwords and keys left out).

### Settings

14. Every setting is optional; the app starts with no `.env` at all. Settings the app reads start with
    `DEXSTASH_`, plus `APP_NAME`, `TZ`, `DISPLAY_TZ`, `PUID` and `PGID`.
15. A setting with another prefix whose name matches a known one (`OBIE_SCANNER`, say) is reported in the log
    and `docker logs` with the name to use. It isn't read.
16. An on/off setting is off for `0`, `false`, `no` or `off` (any case), its default when empty, and on
    otherwise.
17. The settings and their defaults:

    | Setting | Default | What it does |
    |---|---|---|
    | `TZ`, `DISPLAY_TZ` | `Etc/UTC` | Time zone, read on the first start only; then Data sources › Settings. `DISPLAY_TZ` wins. |
    | `PUID`, `PGID` | 1000 | Owner of `data/` and the user the app runs as. |
    | `APP_NAME` | `dexSTASH` | The name shown everywhere (40 characters at most). A restart picks it up. |
    | `DEXSTASH_ADMIN_USER`, `DEXSTASH_ADMIN_PASSWORD` | unset | The first admin, made only while there are no accounts. Unset: a one-time setup code in the log and `data/setup-code.txt`. |
    | `DEXSTASH_SECRET_KEY` | made in `data/secret_key` | Signs sign-ins and share codes and encrypts API keys. Refused if it's the example value or under 32 characters. |
    | `DEXSTASH_HTTPS_ONLY` | on | Secure cookies. Off only for a plain-http test on a home network. |
    | `DEXSTASH_PROXY_IPS` | private ranges | Who may pass on the visitor's address and https (see 19). |
    | `DEXSTASH_TCGCSV` | on | TCGplayer prices and products from TCGCSV. |
    | `DEXSTASH_EURO_RATE` | on | The ECB's exchange rates (other currencies, CardTrader's euro prices). |
    | `DEXSTASH_SCANNER` | unset | `off`, `inside` or `separate`. Unset: the Scanning switch in Data sources (off, or inside). |
    | `COMPOSE_PROFILES` | unset | `scanner` starts the scanner container (with `DEXSTASH_SCANNER=separate`). |
    | `DEXSTASH_PICTURE_MODEL_URL` | built in | Where Enhanced Search downloads its picture model. |
    | `DEXSTASH_APP_MEMORY` | `2g` | The app container's memory cap. |
    | `DEXSTASH_CARD_INDEX` | on | Find searches its own card catalog; off asks TCGdex every time. |
    | `DEXSTASH_PICTURE_CHECK` | on | The background check for missing card pictures. |
    | `DEXSTASH_TCGDEX_CACHE` | on | Keep TCGdex replies for a week. |
    | `DEXSTASH_VERSION` | `latest` | Which image to run (see 6). |
    | `DEXSTASH_NETWORK`, `DEXSTASH_NETWORK_EXTERNAL` | `dexstash`, `false` | The Docker network the proxy reaches the app on. |
    | `DEXSTASH_DOMAIN`, `HTTP_PORT`, `HTTPS_PORT` | `cards.example.com`, 80, 443 | The bundled Caddy only. |
    | `DUCKDNS_SUBDOMAIN`, `DUCKDNS_TOKEN` | unset | The bundled DuckDNS updater only. |
    | `DEXSTASH_DB` | `/srv/data/dexstash.db` | Set by the image; the database's path. |
    | `DEXSTASH_CARDTRADER_TOKEN` | unset | Before 0.28 only: moved once into Data sources › Prices › API keys, and read from `.env` until then. |

18. `app/settings.py` (`KNOWN`) lists every `DEXSTASH_` setting; a test checks the code and
    `docker-compose.yml` against it, so a new setting is added there, to `.env.example` and to this table.
19. API keys (CardTrader, PriceCharting) are entered in the app, never in `.env`. They're kept in
    `data/api_keys.json`, encrypted with the secret key, outside the database, so backups never hold them.

### Reverse proxy

20. The app trusts the forwarded visitor address and https only from `DEXSTASH_PROXY_IPS` (a comma list of
    addresses or ranges). Unset, that is `127.0.0.1`, `10.0.0.0/8`, `172.16.0.0/12` and `192.168.0.0/16`,
    which only containers can use because the app isn't published.
21. The proxy must pass the browser's `Host` header on; a form whose Origin doesn't match is refused.
22. The proxy must allow request bodies of 25 MB, so scan photos upload.
23. The Home Screen app needs https (or `localhost`).

### Data and backups

24. Everything the install keeps is in `data/`: `dexstash.db` (the household's data), `dexstash-catalog.db`
    (downloaded data that can be fetched again), `backups/`, `logs/`, `card-photos/`, scan photos,
    `secret_key`, `api_keys.json` and downloaded models and lists. Backing up `data/` and `.env` backs up the
    install.
25. The app copies `dexstash.db` into `data/backups/` before an upgrade (5 kept), each night after the price
    update (7 kept), by hand (10 kept), before a restore (3 kept) and before starting over (3 kept).
26. Backups hold `dexstash.db` only. The catalog is rebuilt from Data sources; card photos are downloaded as
    a zip from Account › Backups; scan photos are deleted once their card is added.
27. Restoring a backup first saves the current database, then signs everyone out.
28. `python -m app.reset` lists what it would erase and changes nothing; `--yes` erases the household's data,
    keeps downloaded data, and saves the old database as a backup first.

### Updating and going back

29. Updating is `docker compose pull && docker compose up -d` (or `--build` from source). `.env` and `data/`
    are kept.
30. On start with new code the app backs up the database, then runs its numbered database steps in order,
    each all or nothing, then the quick upgrade tasks, before the first page.
31. If that part fails, every address shows a page naming the error and the backup to put back.
32. A restart stops every job that was running or waiting ("Stopped by a restart"); scans carry on. A nightly
    price update missed while the server was off isn't caught up.
33. An older version refuses to start on a database a newer one has changed, and says so in the log. Going
    back means putting the `-upgrade.db` backup back by hand, then running the older image. Restoring a
    newer version's backup in an older version is refused the same way.

## Rules and limits

- One Uvicorn worker, on purpose (see [architecture](architecture.md)). Never scale the app container.
- Nothing in the image, compose file or `.env.example` names a real host, network, domain or person; examples
  use `cards.example.com`, `172.18.0.5` and `misty`.
- `.env` and `data/` are never committed or put in an image.
- Each new setting is optional with a safe default, so an install that doesn't set it keeps working.
- A release that adds a database step says so in `CHANGELOG.md`, because going back then needs the backup.

## Interfaces

- Files: `docker-compose.yml`, `Dockerfile`, `.env.example`, `deploy/entrypoint.sh`, `deploy/Caddyfile`.
- Workflows: `.github/workflows/image.yml` (release images and files), `pr-image.yml` (test images).
- Code: `app/settings.py`, `app/main.py` (proxy, https, Origin check), `app/startup.py`, `app/db.py`,
  `app/backup.py`, `app/reset.py`, `app/keys.py`, `app/activity.py`.
- Pages: Account › Backups, Account › Data sources (Settings, Scanning, Prices › API keys, Activity log,
  Running now), `/healthz`.
- Guides: [DEPLOY.md](../../DEPLOY.md), [README.md](../../README.md) › Configuration,
  [CHANGELOG.md](../../CHANGELOG.md).
- Related specs: [overview](overview.md), [architecture](architecture.md), [prices](prices.md).

## Open questions

- DEPLOY.md's opening still gives the app 1 CPU and 1 GB; the compose file now caps it at 2 GB. Which one the
  guide should lead with (the 1 GB advice for hosts that don't scan inside) needs settling.
- `.env.example` says `DEXSTASH_EURO_RATE=0` turns off "Cardmarket prices"; Cardmarket is no longer a source.
