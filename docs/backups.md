# Backups and moving servers

Everything dexSTASH knows is in `data/`: the database (`dexstash.db`), its backups, your card photos, downloaded pictures and models. Card data and prices downloaded from TCGdex, TCGCSV and CardTrader (and, since 0.15.3, the card catalog Find searches) are in a file of their own, `dexstash-catalog.db`, so backups hold only what's yours; if that file is lost or damaged, Data sources › Prices › ⋯ › **Rebuild downloaded data** fetches it again. The app copies the database into `data/backups/` before every upgrade, every night, and when an admin presses **Back up now**; Account › Backups restores or downloads them. Your own card photos aren't in those copies: **Download photos** saves them as one zip.

To move to another server, stop the app and copy the whole folder, `data/` and `.env` included. `data/secret_key` keeps everyone signed in and shared-binder codes working. It's also worth having your server back up that folder to another drive.
