# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome. If you work with an AI coding assistant, point it at [AGENTS.md](AGENTS.md), which lists the rules every change follows. For a security problem, see [SECURITY.md](SECURITY.md) instead.

## Issues, labels and the board

Every change starts as an issue, opened from one of the three forms: **Feature**, **Bug** or **Spec**. Each
form adds its type label. Other labels:

- `area: ...` says which part of the app it touches, matching the specs in [docs/specs/](docs/specs/README.md).
- `db-step` marks a change with a database step: older versions won't start on the upgraded database.
- `needs-decision` and `blocked` say why an issue can't move yet.

Where an issue is in its life lives on the project board, not in labels: **Backlog** (written, not planned),
**Ready** (planned and approved), **In progress**, **In review** (a pull request is open) and **Done**.

## Running it

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest
DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --reload
```

The tests need no network: every outside request is replaced with a fake one. `tools/demo_data.py` makes a demo collection to click around in. Every pull request and every push to `main` runs the same tests on GitHub Actions, and a release image is only built after they pass. The Checks workflow also runs `ruff check .`, `python tools/check_rules.py` (personal details, files that must never be committed, and the spec rule), gitleaks and pip-audit.

Run the quick ones before you push. `git config core.hooksPath tools/hooks` turns on a hook that runs ruff, the rules check and the tests on every `git push` (and gitleaks, if it's installed), so a failure shows up on your machine instead of on GitHub. `git push --no-verify` skips it once.

When GitHub Actions is switched off for the repository, a maintainer runs the checks for a pull request on their own machine instead: check out its branch and run `python tools/local_checks.py`. It runs each check and reports the result on the pull request under the same name as the Actions job, so the checks `main` requires still pass or fail. It needs the GitHub CLI signed in, gitleaks, `pip install ruff`, and `DEXSTASH_PRIVATE_WORDS` set the same as the repository secret. The tests and pip-audit run directly when the project's packages are installed, and in Docker when they can't be (onnxruntime has nothing for Intel Macs). Where neither works, those two are left out: `main` requires only lint, the rules check and gitleaks, so whoever makes a change runs the tests before pushing.

On Windows, set `PYTHONUTF8=1` first (PowerShell: `$env:PYTHONUTF8 = "1"`). The few Linux-only tests (file permissions, process priority, open files) are skipped there.

## How the code is laid out

[docs/architecture.md](docs/architecture.md) has the diagrams and a map from each page to its route, logic and tables. In short:

- `app/routes/` has one module of pages per topic; `app/web.py` holds what they share (templates, sign-in checks, background workers).
- Logic lives in plain modules in `app/` and takes a database connection, so it can be tested without the web layer.
- Templates are Jinja with htmx in `app/templates/`. There's no JavaScript build step.
- A database change is a new numbered `_step_N` in `app/db.py`, raising `LATEST`. Never change an existing step.
- Your own data's tables are in `app/schema.sql`; downloaded data that can be fetched again (card lists, price quotes, Find's card catalog) goes in `app/catalog.sql`, which lives in `dexstash-catalog.db` and is never backed up.
- A shown price comes only from `prices.resolve`: a new price source writes quotes to `price_quotes`, and the main price is the average of a printing's sources.
- Every request to another site goes through `app/sites.py`, which sets the User-Agent and refuses sites that are switched off.

## Style

- Match the code around you: short functions, plain names, and a docstring saying what a function gives back.
- Comments and anything the app shows people are plain English: full sentences, no jargon.
- Add a test for each change, next to the tests for that area in `tests/`.
- Searches and page loads come first. Slow work goes to a background worker.

## What must never be committed

- Card data, card pictures, sprites, Pokédex text, Bulbapedia text, Chinese dataset files, prices or models. They're downloaded into `data/` while the app runs, under their owners' terms (see [NOTICE.md](NOTICE.md)).
- `.env`, `data/`, or anything with a real domain, server name, password or key in it. Examples use `misty` and `cards.example.com`.

By contributing you agree that your contribution is licensed under the [AGPL-3.0](LICENSE), like the rest of dexSTASH.
