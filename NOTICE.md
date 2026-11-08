# Credits and third-party notices

dexSTASH is free software under the GNU Affero General Public License, version 3 (see [LICENSE](LICENSE)). That licence covers this app's own code. It does not cover the data, pictures and models below, which belong to their owners and keep their own terms.

dexSTASH is a fan project. It is not affiliated with, endorsed by or sponsored by Nintendo, Creatures Inc., GAME FREAK inc. or The Pokémon Company. Pokémon and the names of Pokémon cards are trademarks of their respective owners.

## Included in this repository

| What | Licence |
|---|---|
| Nunito font (`app/static/fonts`) | SIL Open Font License 1.1 (`app/static/fonts/OFL.txt`) |
| IBM Plex Mono font (`app/static/fonts/ibm-plex-mono-latin.woff2`) | SIL Open Font License 1.1 (`app/static/fonts/OFL-IBM-Plex.txt`) |
| htmx 2.0.4 (`app/static/htmx-2.0.4.min.js`) | Zero-Clause BSD (`app/static/htmx-LICENSE.txt`) |
| Printed set codes the scanner reads (`app/set_codes.json`), made from [TCGdex's cards database](https://github.com/tcgdex/cards-database) | That project's licence |

The Python packages in `requirements.txt` (FastAPI, Uvicorn, Jinja, httpx, Pillow, RapidOCR, ONNX Runtime and others) are installed when the image is built, each under its own licence.

## Downloaded while the app runs (never included)

Every request uses the User-Agent `dexSTASH/<version> (+<this repository>)`. The sites marked *switch* are only contacted while they're on under Data sources › Price and picture sites; a new install starts with them off.

| What | From | Terms |
|---|---|---|
| Card lists, card details and card pictures | [TCGdex](https://tcgdex.dev) | TCGdex's terms; card pictures are the card makers' artwork |
| Pokémon names in every language | [PokéAPI](https://pokeapi.co) | PokéAPI's terms |
| Pokémon sprites (downloaded by an admin from Data sources) | PokéAPI's sprite repository | Nintendo / GAME FREAK artwork: for personal use, never commit them |
| Mainland Chinese sets and pictures (*switch*) | [PTCG-CHS-Datasets](https://github.com/duanxr/PTCG-CHS-Datasets) | Personal use; never commit or redistribute |
| English names for mainland Chinese cards (*switch*, with the Chinese datasets) | [PTCG-card-data](https://github.com/zsjy947/PTCG-card-data) | CC0 |
| Japanese card pictures (*switch*) | Limitless TCG | Limitless's terms |
| Pokémon descriptions (*switch*) | [Bulbapedia](https://bulbapedia.bulbagarden.net) | CC BY-NC-SA 2.5, personal use |
| Picture matching model (CLIP ViT-B/32 as ONNX) | OpenAI CLIP, via Jina's clip-as-service | MIT |
| Prices | TCGCSV (TCGplayer's prices) and Cardmarket via TCGdex; CardTrader's official API with the host's own token | Each source's terms; only for your own collection |
| Exchange rates | [European Central Bank](https://www.ecb.europa.eu) reference rates | Free reuse with the source named |
