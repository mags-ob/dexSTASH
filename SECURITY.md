# Security

## Reporting a problem

Please report security problems privately, not in a public issue. On GitHub, open this repository's **Security** tab and choose **Report a vulnerability**. Say what you found, how to reproduce it and which version you ran (Account › About shows it).

You'll get a reply as soon as the maintainer can look at it. Once a fix is released, the report can be published with credit to you if you'd like.

## Supported versions

Only the latest release gets security fixes. Update with the steps under "Updating" in the README.

## Running it safely

- Serve it over HTTPS behind a reverse proxy, and keep `DEXSTASH_HTTPS_ONLY=1`.
- Don't publish port 8000 to the internet; the default `docker-compose.yml` doesn't.
- Keep `data/` and `.env` private and backed up. `data/secret_key` signs every sign-in and shared-binder code: anyone with it could sign in as any account.
- API keys (CardTrader, PriceCharting) are entered in Data sources › Prices › API keys, by the admin only. Each is stored encrypted with `data/secret_key` in `data/api_keys.json`, outside the database, so backups, restores and exports never hold one; only its last 4 characters are shown again. dexSTASH sends a key to that site's API and nowhere else, never logs it or puts it in an error, and the photo reader doesn't read it. A CardTrader token is a full key to that account: if one leaks, delete it on the site and make a new one. A token still in `.env` from before 0.28 (`DEXSTASH_CARDTRADER_TOKEN`) is moved into the app at the first start; remove the line from `.env` afterwards.
- Set `DEXSTASH_PROXY_IPS` to your reverse proxy's address. The app believes the visitor's address and https only from there, so wrong-password lockouts block the right visitor.
- `data/setup-code.txt` holds the one-time setup code until the first account is made, readable only by the app's user. If you set `DEXSTASH_ADMIN_PASSWORD` to skip it, remove it from your settings once you've signed in.
