# Test copy of dexSTASH

A second dexSTASH next to the live one, for trying a pull request with a copy of your real data. It has its
own container (`dexstash-test`), image and data folder, so nothing it does can touch the live app or its data.
The test image is built on your server and never pushed anywhere.

You can pick the pull request from your phone: tick the `test` label on it in the GitHub app, and a small
watcher container builds it within a few minutes.

## Set it up once

You need a shell on the server (SSH, or your NAS's own terminal) for these steps; after that, a phone is enough.

1. Next to the folder that holds your live dexSTASH, make a folder `dexstash-test` and copy everything in
   this folder (`deploy/test-copy/`) into it.
2. In that folder:
   ```
   chmod +x *.sh
   sudo ./try-pr.sh main          # builds the newest main (the first build takes a few minutes)
   sudo ./reset-test-data.sh      # copies your live data in
   sudo docker compose up -d      # starts the test copy and the watcher
   ```
   If your live container isn't called `dexstash`, or your proxy's network isn't `docker_proxy`, put
   `LIVE_CONTAINER=...` and `DEXSTASH_NETWORK=...` in a `.env` file in this folder first.
3. Add the test address to your Caddyfile, next to the live entry, then reload Caddy:
   ```
   test.cards.example.com {
       request_body {
           max_size 25MB
       }
       reverse_proxy dexstash-test:8000
   }
   ```
   A DuckDNS name answers for any name in front of it (`test.<name>.duckdns.org`), so no new DuckDNS name is
   needed. Caddy fetches the certificate the first time you open the address.
4. In your GitHub repository, make a label called `test` (Issues › Labels › New label).
5. Open the test address and sign in with your usual account. The app's name shows what it runs
   ("Test PR 70 1a2b3c4").

## Try a pull request from your phone

1. In the GitHub app, open the pull request › Labels › tick `test`.
2. Wait about five minutes (longer if the build is big).
3. Open the test address. The app's name shows the pull request and its commit.

Leave the label on while you test: a new commit on that pull request is built by itself. Untick it when
you're done; the test copy keeps running until the next one. If more than one pull request has the label,
the one you labelled last wins.

When the watcher switches to a different pull request it also copies fresh data from live, so an earlier
pull request's database changes never get in the way. It only builds branches in your own repository, never a
pull request from a fork. A build that fails isn't tried again until the pull request gets a new commit.

To see what it's doing, open the `dexstash-test-watcher` container's log (your NAS's Docker app shows it).

### Watcher settings

Put these in `.env` in this folder, then `sudo docker compose up -d` again.

| Setting | Default | What it does |
|---|---|---|
| `DEXSTASH_REPO` | `mags-ob/dexSTASH` | The repository to watch (your fork, say). |
| `TEST_LABEL` | `test` | The label that picks the pull request. |
| `CHECK_MINUTES` | 5 | How often it asks GitHub. It needs no token: about 2 requests a check. |
| `LIVE_CONTAINER` | `dexstash` | The live container to copy data from. |

The watcher can control Docker on the server (it mounts the Docker socket), the same as running the scripts
with `sudo`. Stop it with `sudo docker compose stop watcher` if you only want to test by hand.

## By hand

```
sudo ./try-pr.sh 70            # pull request 70, its newest commit
sudo ./try-pr.sh 70 1367e55    # at an earlier commit
sudo ./try-pr.sh main          # the newest main
sudo ./reset-test-data.sh      # fresh data from live
```

Run `reset-test-data.sh` when you want today's live data, or when the test copy won't start because an earlier
pull request changed its database ("this database is newer"). Live keeps running while it copies.

## What's kept apart

- The test copy never writes to the live data folder; the live databases are copied with SQLite's own backup
  inside the live container.
- Not copied: backups, logs, scan photos, API keys and the secret key. Enter a CardTrader key in the test copy
  (Data sources › Prices › API keys) if you need its prices there; resets keep it.
- The nightly price update is off in the copy, so it doesn't send a second round of price requests.
- Scanning runs inside the test container (no second scanner container).
- Stop it any time with `sudo docker compose down` in this folder; delete the folder to remove it.
