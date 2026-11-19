# 34: Run the scanner on a computer's graphics card

**Issue:** [#34](https://github.com/mags-ob/dexSTASH/issues/34). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning, deploy. Specs: [scanning](../specs/scanning.md), [deploy](../specs/deploy.md).
- The scanner runs `off`, `inside` (a child process of the app) or `separate` (the `dexstash-scanner`
  container), set by `DEXSTASH_SCANNER` or the Scanning switch (`app/settings.py`, `scanner_mode`).
- The app only queues the work; the scanner and the app share the database (`jobs` table) and the scanner
  reports a heartbeat in `app_meta`. A helper on another computer couldn't share the SQLite file, so it would
  need a way to take work over the network.
- The scanner never gets the CardTrader token or the admin password (scanning spec, Rules). A helper key made by
  the admin would be a new kind of secret: keep it out of the database and logs, like API keys
  (`data/api_keys.json`).
- Reading is on the CPU today (RapidOCR, CLIP ViT-B/32 as ONNX).

## Open questions

- How the helper connects (polls the server over HTTPS, or the server pushes), and what it sends back.
- Which graphics cards and systems the helper supports.
