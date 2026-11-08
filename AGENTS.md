# Working on dexSTASH with an AI assistant

This file is for any AI coding assistant (Claude Code, Cursor, Copilot, Codex and others) and for the people
who drive them. Read it before you change anything. People new to the code should start with
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).

dexSTASH is a self-hosted Pokémon card collection app: Python (FastAPI), Jinja templates with htmx, and SQLite,
run in Docker. It is licensed under the AGPL-3.0.

## The rules, in three tiers

| Tier | File | What it holds | Who changes it |
|---|---|---|---|
| 1. Core | [rules/core.md](rules/core.md) | Rules no one may loosen: privacy, data-source terms, secrets, licence | Maintainers only, by pull request |
| 2. Project | [rules/project.md](rules/project.md) | How this codebase is built and tested | Anyone, by a reviewed pull request |
| 3. Personal | `rules/personal.md` (not committed; start from [rules/personal.example.md](rules/personal.example.md)) | One contributor's own setup and habits | That contributor |

A lower tier can add rules but never loosen a higher one. When two rules disagree, the higher tier wins. When
a task would break a core rule, stop and say so instead of finding a way around it.

## How a change is made

Every change starts from a GitHub issue and follows the same five steps.

1. **Define.** The issue holds a user story and acceptance criteria. The spec for the part of the app it
   touches, in [docs/specs/](docs/specs/README.md), is written or updated first.
2. **Plan.** Read the issue, the spec and the code it touches. Write the change list on the issue: what the
   app does now, what will change, and anything that contradicts an earlier decision. Wait until the issue is
   marked ready.
3. **Build.** Work on a branch named `feature/<short-name>`, `fix/<short-name>`, `docs/<short-name>`,
   `chore/<short-name>`, `refactor/<short-name>` or `test/<short-name>`, never on `main`. Code, tests and the
   spec update go in the same pull request, which says `Closes #<issue>`.
   Commits and pull requests are short and say only what changed; the why is on the issue. No attribution
   lines, sign-offs or links to anyone's assistant sessions ([rules/project.md](rules/project.md)).
4. **Validate.** GitHub Actions runs the checks on every pull request. Each acceptance criterion should have a
   test. Changes to pages get a look at phone width.
5. **Learn.** Anything non-obvious you found goes into the spec or the docs, so the next change starts
   from it.

## Commands

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock pytest
python -m pytest -q                              # the whole suite; no network needed
DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --reload
python tools/demo_data.py                        # a demo collection to click around in
```

On Windows, set `PYTHONUTF8=1` first.

## Where things are

- `app/routes/` holds the pages, one module per topic; `app/web.py` is what they share.
- `app/db.py` holds the numbered database steps; `app/schema.sql` your own data; `app/catalog.sql` data that
  can be downloaded again.
- `app/prices.py` (`resolve`) is the only place a shown price is worked out.
- `app/sites.py` is the only way to reach another site.
- `docs/specs/` holds the specs, `tests/` the tests, and `tools/` the helper scripts.
