# Deploying dexSTASH with Docker

These steps assume a Linux server or NAS with Docker, and Caddy as the reverse proxy that serves HTTPS; any reverse proxy works the same way. On your own server, run dexSTASH as two containers built from one image:

- `dexstash`, the web app. Your Caddy proxies to `dexstash:8000`. The port isn't opened to the LAN, so only Caddy can reach it. Limits: 1 CPU, 1 GB.
- `dexstash-scanner` reads scan photos, runs Enhanced Search and fingerprints, and looks up pictures. It runs at low priority (1 CPU, 1.4 GB), so pages and searches stay quick while a batch is being read. Nothing reaches into it. It starts only with the `scanner` profile (step 3).

Both share `data/`: your database (`dexstash.db`), the downloaded card data, prices and Find's card catalog (`dexstash-catalog.db`), backups, scan photos and the Pokémon name list. Without scanning, or on a host that runs one container, see the README's Scanning section.


## 1. A domain

Give the app a domain name. A free DuckDNS subdomain works: at duckdns.org, add a subdomain pointing at your home IP, and have your router forward ports 80 and 443 to Caddy.

## 2. Copy the app to your server

Make a folder on the server, for example `/opt/dexstash` (on a NAS, a folder in its Docker share), and fetch the compose file and the example settings into it:

```sh
cd /opt/dexstash
sudo curl -fsSLO https://raw.githubusercontent.com/obiehq/dexSTASH/main/docker-compose.yml
sudo curl -fsSL -o .env https://raw.githubusercontent.com/obiehq/dexSTASH/main/.env.example
```

That's all the app needs: the image comes ready-built (see Using a release image). To build it on your server instead, clone the repository (or unpack a release's source) into the folder and start it with `--build`.

## 3. Settings

Edit `.env` (`sudo nano .env`). Without one the app starts with its defaults.

In `.env` (everything here is optional):
- `DEXSTASH_SECRET_KEY`: leave it empty and the app makes one on the first start, in `data/secret_key`. It signs sign-ins and shared-binder codes, so when you move the app to another server, copy the whole `data/` folder. If you set one yourself, make it with `python3 -c "import secrets; print(secrets.token_hex(32))"`; the app **won't start** with the example value or anything shorter than 32 characters.
- `DEXSTASH_SCANNER=separate` and `COMPOSE_PROFILES=scanner`: read scan photos in the scanner container. Left out, only the app starts and an admin turns scanning on or off in Account › Data sources › Scanning (Off to start; On reads photos inside the app container, about 2 GB).
- `DEXSTASH_ADMIN_USER` and `DEXSTASH_ADMIN_PASSWORD`: the first admin account, made on the first start. Left out, the first visit asks for a setup code instead (see step 4). They're only read while there are no accounts, so they never change an account later; you can remove them once you've signed in.
- `APP_NAME` (optional): the name shown everywhere. Default `dexSTASH`.
- `DEXSTASH_PROXY_IPS` (optional). Who may pass on a visitor's address and https: your Caddy. Left out, any private address is trusted (127.0.0.1, 172.16.0.0/12, 192.168.0.0/16, 10.0.0.0/8), which only containers can use because the app isn't published on a port. Wrong passwords lock out a username only from the address that typed them, so this is what stops a stranger locking you out. To narrow it, run `sudo docker inspect caddy | grep IPAddress` and set e.g. `DEXSTASH_PROXY_IPS=172.18.0.5`.
- `PUID`/`PGID`: run `id` over SSH on the server and use your numbers, so you can open `data/` from your own account.
- `TZ` (or `DISPLAY_TZ`, which wins): your time zone, like `Europe/London`, for dates, times and the nightly price update (04:00). It's only read on the first start; after that change it under Account › Data sources › Settings. Left out, it's UTC.


## 4. Start the app

Over SSH in the folder:

```sh
sudo docker compose up -d         # fetches the release image; add --build to build it here instead
sudo docker compose ps            # dexstash should say (healthy) after ~20 s, then dexstash-scanner starts (with the scanner profile)
```

The first visit asks for the admin account, with a one-time **setup code** so nobody else who finds a new install can take it over. The code is in the app's log, and in `data/setup-code.txt` until the first account exists:

```sh
sudo docker logs dexstash
```

Wrong codes count like wrong passwords. Once the first account exists there's no code any more. (With `DEXSTASH_ADMIN_USER` and `DEXSTASH_ADMIN_PASSWORD` set, the account is already made: just sign in.)

Then the first-run wizard opens: change your password first, then the time zone and currency, the languages you collect, the extra sites and the nightly price update, and what to download (each shows its estimated size). When you press Finish, Getting your stash ready downloads the basics one at a time (exchange rates and TCGplayer's set list, Pokémon names and sprites, Find's card catalog, mainland Chinese sets if picked); Continue opens once they're done, and the first price update, Bulbapedia descriptions and Enhanced Search carry on in the background. Until Continue no other page opens, and anyone else who signs in sees that the admin is still setting up. An install that already has cards or more than one login skips it.

## 5. Add it to your Caddy

Put this in your Caddyfile next to your other sites (with your own domain):

```
<your-domain> {
    reverse_proxy dexstash:8000
    encode gzip

    request_body {
        max_size 25MB
    }

    header {
        Strict-Transport-Security "max-age=31536000;"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

`request_body` lets scan photos upload.

If Caddy runs in Docker, it finds other containers by name when they share a Docker network. Put dexSTASH on the same network as your Caddy; in `.env` (use your network's name, from `sudo docker network ls`):

```
DEXSTASH_NETWORK=<your Caddy network>
DEXSTASH_NETWORK_EXTERNAL=true
```

Nothing in the Caddy compose file changes.

If Caddy runs straight on the server rather than in Docker, use `reverse_proxy 127.0.0.1:8000` instead, and in `docker-compose.yml` uncomment `ports` with `"127.0.0.1:8000:8000"`.

Then reload Caddy (`sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile`) and open your domain.

### Home Screen app

On a phone: open it in Safari or Chrome and use **Add to Home Screen** (Account › Add to Home Screen has the steps for each). It opens full screen with the dexSTASH icon and a launch screen.

- **It needs https.** The offline page and the app's own window only work on an https address (or `localhost`); over plain http the phone saves a bookmark.
- **When the server can't be reached** (the server is off, or the phone has no connection), the app shows "Can't reach dexSTASH" and tries again every 10 seconds, instead of the browser's error page. Nothing else is kept offline: every page comes from your server.
- **A new icon or name** shows after removing the Home Screen icon and adding it again; phones keep the first one.

## Backups

The app backs itself up into `data/backups/`:
- **Before upgrades:** each time it starts with new code, it copies the database before changing anything. The last 5 are kept.
- **Every night:** after the nightly price update. The last 7 are kept.
- **By hand:** Account > Backups > Back up now. The last 10 are kept.
- **Restore:** Account > Backups > Use > Restore (type RESTORE). It first saves what's there now, so a restore can be undone. Everyone is logged out afterwards.
- **Download:** to keep a copy off the server.

Only the admin who set up the app sees Backups. Backups hold your database only: the downloaded card data and prices (`dexstash-catalog.db`) are fetched again with Data sources › Prices › ⋯ › Rebuild downloaded data. Scan photos aren't in backups, since they're deleted once a card is added. Your own card and chase photos (`data/card-photos/`) aren't in them either: **Download photos** under Account > Backups saves them all in one zip.

It's still worth having your NAS or server back up the whole `dexstash` folder (including `.env`) to another drive or the cloud. That covers a failed disk too.

If an upgrade doesn't finish, every page says "The upgrade didn't finish", with the error and the backup to put back (since 0.22.0; before, the app stopped and only its log said why). Put the last `-upgrade.db` back by hand:

```sh
sudo docker compose stop app scanner
sudo cp data/dexstash.db data/dexstash-broken.db
sudo cp data/backups/dexstash-<date>-upgrade.db data/dexstash.db
sudo rm -f data/dexstash.db-wal data/dexstash.db-shm   # dexstash-catalog.db can stay
sudo docker compose up -d   # with the previous app/ folder if the new code is the problem
```

## Starting over

To erase everything in the app (cards, chases, spending, notes, imports, scans and logins) and start empty, run this on the server, in the dexSTASH folder:

```sh
sudo cp -a data data-before-reset                          # your own copy, just in case
sudo docker compose stop app scanner
sudo docker compose run --rm app python -m app.reset       # lists what it will erase and keep; changes nothing
sudo docker compose run --rm app python -m app.reset --yes # does it
sudo docker compose up -d
```

The old database is also saved in `data/backups` as "Before starting over", so it can be restored from Account > Backups. Kept, so nothing big is downloaded again: Pokémon sprites, the downloaded card data and prices (`dexstash-catalog.db`), the Enhanced Search model, English and Japanese fingerprints, and the Pokémon name list. Afterwards, open the app and create the admin login again, then on Data sources: press Download sprites (quick, it re-reads the files), load the Chinese sets again if you want them, and turn Enhanced Search on again. Once it all works, `sudo rm -rf data-before-reset`.

## Updating

`.env` and `data/` are kept. Fetch the new image and start it:

```sh
sudo docker compose pull && sudo docker compose up -d
```

If you build on your server, unpack the new release over the old files (`sudo tar xzf <release>.tar.gz`) or `git pull` in a clone, then `sudo docker compose up -d --build`.

On start the app backs up the database (`data/backups/…-upgrade.db`) and then makes its numbered database changes in order, each all or nothing. [CHANGELOG.md](CHANGELOG.md) says what a version changes and whether there's anything to do.

A restart (an update, or `docker compose restart`) stops background jobs: anything that was running or waiting shows "Stopped by a restart" under Account › Data sources › Running now, and you start it again yourself. Scans pick up where they were. A nightly price update missed while the server was off isn't caught up; it runs the next night, or press Update prices.

With an image file (below), updating is loading the new release's file and changing the version in `.env`. Before a big update, press Account › Backups › Back up now and Download photos and keep both somewhere else.

**Going back:** an older version refuses to start on a database a newer one has changed, and says so in the log. To go back, put the `-upgrade.db` backup made before the upgrade back by hand (as under Backups above), then unpack the older release and start it. Restoring that backup from Account › Backups in the newer version doesn't work for this, because the newer version updates it again on start. Restoring a backup from a newer version is refused the same way. After going back, press Update prices once: older versions keep some downloaded prices in other tables.

### Using a release image

Each release is built on GitHub, so your server doesn't have to build it. `docker-compose.yml` names it, `ghcr.io/obiehq/dexstash`, and `docker compose up -d` fetches it.

**Pick a version** (optional) in `.env`: `DEXSTASH_VERSION=0.28` follows the newest 0.28.x, `0.28.0` stays on one release, and left out it's `latest`, which follows every release. To update, run `sudo docker compose pull && sudo docker compose up -d`.

**Or load the file** attached to the release (the repository's Releases page), for a server that can't reach the registry: `dexstash-<version>-amd64.tar.gz` for most servers and NAS boxes, or `-arm64` for ARM ones (a Raspberry Pi, say). Copy it into the app's folder and load it:

```sh
sudo docker load -i dexstash-0.28.0-amd64.tar.gz      # prints: Loaded image: ghcr.io/obiehq/dexstash:0.28.0
```

Then set `DEXSTASH_VERSION=0.28.0` in `.env` and run `sudo docker compose up -d`: it uses the loaded image without reaching the registry. To update, load the next release's file and change the version. You still need `docker-compose.yml` and `.env` in the folder (and `deploy/Caddyfile` for the bundled Caddy). `sudo docker image prune` removes old versions once you no longer need to go back to them.

### Trying a pull request

Every pull request from this repository gets an image tagged `pr-<number>` (Actions › Test image shows it), rebuilt on each push and removed when the pull request closes. To try one, back up first (Account › Backups › Back up now), then set `DEXSTASH_VERSION=pr-<number>` in `.env` and run `sudo docker compose pull && sudo docker compose up -d`. To go back, set `DEXSTASH_VERSION` back to the release (or remove it) and run the same again.

A pull request that adds a database step says so at the top. Its image upgrades your database, and the release you go back to won't start on it: put the `-upgrade.db` backup back by hand first, as under Going back above.


## Without your own Caddy

`docker compose --profile caddy up -d` also starts a Caddy from this folder (`deploy/Caddyfile`, domain from `DEXSTASH_DOMAIN` in `.env`). It needs ports 80 and 443, so don't use it next to your existing Caddy. `--profile duckdns` adds a DuckDNS updater (`DUCKDNS_TOKEN` in `.env`).

## Testing on the LAN without Caddy

Uncomment `ports` under `app` in `docker-compose.yml`, set `DEXSTASH_HTTPS_ONLY=0` in `.env`, run `docker compose up -d`, and open `http://<server-ip>:8000`. Put both back afterwards.

## Good to know

- **Outgoing traffic.** If your firewall limits what the server may reach, allow `api.tcgdex.net` and `assets.tcgdex.net`, `tcgcsv.com`, `www.ecb.europa.eu` and `raw.githubusercontent.com`. `tcgplayer-cdn.tcgplayer.com` for TCGplayer's product pictures, and `api.cardtrader.com` with a CardTrader token. Each site you switch on under Data sources › Price and picture sites needs its own host too. The README's "Where your data comes from" lists them all.
- **Prices update once a night** (04:00 in your time zone by default). Turn it off or change the time under Account › Data sources › Prices. If the server was off at that time, the update runs shortly after the app starts again. The same update fetches new sets for Find's card catalog, makes the nightly backup and thins old price history as set under Prices › Price history.
- **Activity log.** Both containers write their log to `data/logs/` (2 MB × 3 files each, passwords and keys left out). Admins can read it on a phone under Account › Data sources › Activity log, and **Copy last hour** copies it for a bug report. Docker's own log stops at 3 files of 10 MB per container.
- **One web worker on purpose:** the login lockout and the job runner live in one process. Photos are read in `dexstash-scanner` (or, with `DEXSTASH_SCANNER=inside`, a lowest-priority child of the app), which shares the database with the app (SQLite WAL; a writer waits its turn).
- **Scanner memory.** Reading photos needs about 500 MB while photos are queued, and the Enhanced Search model another 400-600 MB while loaded; both let go when idle. A batch that mixes languages loads each language's picture index too (roughly 100 to 250 MB each). If the Activity log shows the scanner restarting, raise its `mem_limit` in `docker-compose.yml`.
- **App memory.** The app container's cap is 2 GB (`DEXSTASH_APP_MEMORY` in `.env`), room for scanning `inside` it. It's only a ceiling: without scanning inside, the app idles around 100-150 MB. On a small host that doesn't scan inside, set `DEXSTASH_APP_MEMORY=1g`; under 1.5 GB, Data sources shows scanning inside as unavailable.
- **Sign-ins** last 14 days. Account › Sign out everywhere signs out every phone and computer. 5 wrong passwords for one username within 10 minutes lock that username from that address for a while, and 20 from one address within 15 minutes, over any usernames, lock that address.
- **Forms sent from another website are refused.** Your reverse proxy must pass the browser's `Host` header on to the app; Caddy does this by default.
- **Shared binders.** A binder's **Share** turns on a link (`https://<your domain>/b/<random>`) and codes, one per person. Revoke a code, or make a new link, to stop a visitor at once. Shared pages never show your own photos or scans.
- The image installs the exact versions in `requirements.lock`. `requirements.txt` is the looser list for running it on your own computer.
