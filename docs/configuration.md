# Configuration

Everything in `.env` is optional; `.env.example` has each one with a note.

| Setting | Default | What it does |
|---|---|---|
| `APP_NAME` | `dexSTASH` | The name shown in the app, the browser tab and shared binders. |
| `DEXSTASH_VERSION` | `latest` | Which release image to run: `0.28` follows the newest 0.28.x, `0.28.0` stays on one release. |
| `DEXSTASH_SECRET_KEY` | made on first start | Signs sign-ins and shared-binder codes. Left empty, the app makes one in `data/secret_key`. If you set one, it must be at least 32 characters. |
| `DEXSTASH_ADMIN_USER`, `DEXSTASH_ADMIN_PASSWORD` | unset | Creates the first admin on the first start instead of asking for the setup code. Only read while there are no accounts. |
| `DEXSTASH_HTTPS_ONLY` | `1` | `1` sends the sign-in cookie only over HTTPS. Set `0` only to try it over plain http. |
| `DEXSTASH_PROXY_IPS` | private addresses | The addresses of your reverse proxy. The app believes the visitor's address and https from these only, so wrong-password lockouts block the right visitor and the sign-in cookie is marked secure. |
| `DEXSTASH_APP_MEMORY` | `2g` | The app container's memory cap. A host that doesn't scan inside the app can lower it, for example to `1g`. |
| `DEXSTASH_SCANNER` | unset | `off`, `inside` or `separate`: see [Scanning](scanning.md). Unset, the Scanning switch in Data sources decides. |
| `COMPOSE_PROFILES` | unset | `scanner` starts the separate scanner container with every `docker compose` command. |
| `DEXSTASH_NETWORK`, `DEXSTASH_NETWORK_EXTERNAL` | `dexstash`, `false` | The Docker network the app joins, so a Caddy in Docker can reach `dexstash:8000`. |
| `PUID`, `PGID` | `1000` | The owner of the files in `data/` (run `id` to see yours). |
| `TZ` or `DISPLAY_TZ` | `Etc/UTC` | Your time zone. Only read on the first start; after that, change it in the app. |
| `DEXSTASH_TCGCSV` | `1` | `0` turns off TCGCSV's daily TCGplayer prices. |
| `DEXSTASH_CARD_INDEX` | `1` | `0` turns off the local card catalog: Find then asks TCGdex for every search, as before 0.15.3. |
| `DEXSTASH_EURO_RATE` | `1` | `0` turns off the ECB exchange rates (prices in other currencies, CardTrader's and typed ones, then aren't converted). |
| `DEXSTASH_PICTURE_CHECK` | `1` | `0` stops the background picture check altogether. |
| `DEXSTASH_TCGDEX_CACHE` | `1` | `0` stops keeping TCGdex replies for a week. |
| `DEXSTASH_PICTURE_MODEL_URL` | Jina's copy | Where Enhanced Search downloads its model from. |
| `DEXSTASH_DOMAIN`, `HTTP_PORT`, `HTTPS_PORT` | | Only for the bundled Caddy (`--profile caddy`). |
| `DUCKDNS_SUBDOMAIN`, `DUCKDNS_TOKEN` | | Only for the bundled DuckDNS updater (`--profile duckdns`). |

A setting written with another prefix (for example a typo) is named in the log at start, with the name it needs.

**In the app** (Account › Data sources, admins only): time zone, currency, the nightly price update's time, how long price history is kept, the price and picture site switches, Enhanced Search, and the downloads (sprites, Chinese sets, Pokémon descriptions). Each login picks its own theme and light or dark mode under Account.
