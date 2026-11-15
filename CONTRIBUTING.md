# Contributing

Everyone, including AI assistants, follows the same path. See
[docs/lifecycle.md](docs/lifecycle.md) for detail.

1. **Open an issue** with the Bug, Feature or Process change form. There are no
   other options. A Process change is for changing the workflow, agents,
   lifecycle or rules themselves; only the maintainer approves those.
2. **Define:** the issue gets acceptance criteria, UI/UX and a plan. The
   maintainer says **go** before any code is written.
3. **Build:** work on a branch (`feature/`, `fix/`, `docs/`, `chore/`,
   `refactor/` or `test/` plus a short name) and open a pull request that
   says `Closes #<issue>`, with tests and the spec updated.
4. **Validate:** review and checks. The maintainer approves and merges.
5. **Done:** merged work is bundled into a release.

Contributors may propose and discuss at any stage. Only the maintainer passes
the Go and Approved gates, merges, and publishes releases. Nothing else blocks
a merge: there are no required status checks, and the Validate checklist is
advisory.

- Write access is by invitation. Everyone else forks and opens a pull request.
- Open the issue and wait for a go before sending a pull request. A small fix
  (labelled `small`) can use the issue body as its plan, but still needs a go.
- Run `git config core.hooksPath tools/hooks` once. Commits are scanned locally
  for secrets and personal information (`tools/scan.sh`), and a push also runs
  ruff, the rules check, gitleaks and pip-audit (when installed) and the tests
  (`git push --no-verify` skips it once, for people only and still running
  `tools/scan.sh --branch` first. Assistants never skip it.)
  Install gitleaks (`brew install gitleaks`). Copy
  `rules/private-words.example.txt` to `rules/private-words.txt` and add your
  own words; that file is never committed.

## Running it

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock pytest
python -m pytest
DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --reload
```

The tests need no network: every outside request is replaced with a fake one.
`tools/demo_data.py` makes a demo collection to click around in. On Windows, set
`PYTHONUTF8=1` first. The project's code rules are in
[AGENTS.md](AGENTS.md#project).

## How the code is laid out

[docs/architecture.md](docs/architecture.md) has the diagrams and a map from each page to its route, logic and tables. In short:

- `app/routes/` has one module of pages per topic; `app/web.py` holds what they share (templates, sign-in checks, background workers).
- Logic lives in plain modules in `app/` and takes a database connection, so it can be tested without the web layer.
- Templates are Jinja with htmx in `app/templates/`. There's no JavaScript build step.
- A database change is a new numbered `_step_N` in `app/db.py`, raising `LATEST`. Never change an existing step.
- Your own data's tables are in `app/schema.sql`; downloaded data that can be fetched again (card lists, price quotes, Find's card catalog) goes in `app/catalog.sql`, which lives in `dexstash-catalog.db` and is never backed up.
- A shown price comes only from `prices.resolve`: a new price source writes quotes to `price_quotes`, and the main price is the average of a printing's sources.
- Every request to another site goes through `app/sites.py`, which sets the User-Agent, refuses sites that are switched off, and paces background requests per site.

## What must never be committed

- Card data, card pictures, sprites, Pokédex text, Bulbapedia text, Chinese
  dataset files, prices or models. They're downloaded into `data/` while the app
  runs, under their owners' terms (see [NOTICE.md](NOTICE.md)).
- `.env`, `data/`, or anything with a real domain, server name, password or key
  in it. Examples use `misty` and `cards.example.com`.

Using an AI assistant? Point it at [AGENTS.md](AGENTS.md). For a security
problem, see [SECURITY.md](SECURITY.md). By contributing you agree that your
contribution is licensed under the [AGPL-3.0](LICENSE).
