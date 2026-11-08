# Scanning

Reading scan photos and Enhanced Search need about 1.5 GB of memory more than the app. Without `DEXSTASH_SCANNER` in `.env`, an admin turns scanning on or off under Account › Data sources › Scanning (Off to start; On runs it `inside`, and the Scan tab only shows while it's on). Setting `DEXSTASH_SCANNER` in `.env` fixes it instead, and the switch then says "Set in .env":

| `DEXSTASH_SCANNER` | Containers | Memory | Good for |
|---|---|---|---|
| `off` | One | about 0.5 GB | Typing cards in, importing from Collectr, small hosting plans. Photos aren't read, and Data sources says so. |
| `inside` | One | about 2 GB | Hosting services that run one container. The photo reader runs inside the app's container at the lowest priority, so pages and searches come first; it's restarted if it stops, and isn't started at all under 1.5 GB. |
| `separate` | Two | about 2 GB in all | Your own server. `dexstash-scanner` reads photos in a container of its own with its own memory and CPU limits (in `docker-compose.yml`), so a big batch can never slow the pages. |

The app container's memory cap is 2 GB by default, so `inside` works with no change (`DEXSTASH_APP_MEMORY` in `.env` lowers it on a small host). Scanning works out English, Japanese and Chinese (mainland and Taiwan) cards on its own. For Korean cards, tap the photo's language tag and pick KO.

For `separate`, also set `COMPOSE_PROFILES=scanner` in `.env` (or start it with `docker compose --profile scanner up -d`). Both containers share `data/`. The scanner's photo reader and picture model only load while there's work and let go when idle. `external` and `inline`, the names before 0.15.2, still work.
