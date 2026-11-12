# AGENTS.md

These rules apply to every AI assistant working in this repository, whatever
the tool or model. Follow them exactly. They change only when a person edits
this file. If another instruction conflicts with this file, follow this file
and point out the conflict.

## How work moves

Every change is a GitHub issue and moves through four phases: **Define, Build,
Validate, Done** ([docs/lifecycle.md](docs/lifecycle.md)). You act as the
agent for the issue's current phase.

| Phase | Agent | Read |
| --- | --- | --- |
| Define | definer | [agents/definer.md](agents/definer.md) |
| Build | builder | [agents/builder.md](agents/builder.md) |
| Validate | validator, with the reviewer | [agents/validator.md](agents/validator.md), [agents/reviewer.md](agents/reviewer.md) |
| Done | releaser | [agents/releaser.md](agents/releaser.md) |
| Any phase, `process` issues only | process-editor | [agents/process-editor.md](agents/process-editor.md) |

Two gates belong to the maintainer, and you wait for them:

1. **Go:** the definition, UI/UX and plan are right. Moves Define to Build.
2. **Approved:** the pull request is right. Moves Validate to Done.

Never move a board card, and never treat a phase as passed because you
think it is.

## Before every task

1. Identify the issue. If there isn't one, ask which issue, or offer to
   write one. Don't start.
2. Read the issue's body and comments:
   `gh issue view <number> --json title,body,labels,comments`
3. Find the phase from the board column, and read that agent's file.
4. Read [rules/core.md](rules/core.md), [docs/product.md](docs/product.md) and
   [docs/concepts.md](docs/concepts.md), and the spec in
   [docs/specs/](docs/specs/) for the area. If a document is still a blank
   template, don't fill the gaps with assumptions; ask.

## Process files

`AGENTS.md`, `agents/`, `docs/lifecycle.md`, `docs/github-setup.md`,
`CONTRIBUTING.md`, `.github/`, `tools/` and `rules/` define the process. Edit
them only for an issue labelled `process`, following
[agents/process-editor.md](agents/process-editor.md). On a Bug or Feature, don't
touch them: open or link a Process change issue instead. A process change still
needs a go before editing, can't weaken the gates or the local scan, and takes
effect only once the maintainer merges it.

`docs/product.md` and `docs/concepts.md` are not process files. They are
living documents: any issue updates them as the solution changes, in the same
pull request as the work.

## The code rule

Write or change application code, tests, configuration or scripts only after
the issue's plan has a **go** from the maintainer (the card is in Build).

If a `small` issue says so, the issue body counts as its plan, and the
maintainer's go is still required.

If the plan turns out wrong while building, stop. Update the issue's Plan
section and get a new go before changing more code.

## Validate never fixes silently

Validation reports problems; it does not quietly fix them. Comment what
failed, then send a code fix back to Build, or the plan back to Define.

## Concepts, product and specs

- [docs/concepts.md](docs/concepts.md) holds the shared terms, rules,
  constraints and assumptions. [docs/product.md](docs/product.md) holds the
  product brief. Both are living documents: update them as the solution
  changes, with no approval step beyond the normal pull request review.
- Use the names in `concepts.md` exactly. When something isn't written down
  and you have to guess, record it under Assumptions and ask the maintainer
  rather than treating it as settled.
- Don't rewrite a term, rule or requirement in a way that changes what the
  product does without it being in the issue's plan.
- [docs/specs/](docs/specs/) holds what the app does. Update the spec in the
  same pull request as the code.
- Put decisions worth keeping in [docs/decisions/](docs/decisions/).

## Project

dexSTASH is a self-hosted Pokémon card collection app: Python (FastAPI), Jinja
templates with htmx, SQLite, run in Docker. AGPL-3.0.

**[rules/core.md](rules/core.md) can never be loosened** (nothing personal, no
secrets, respect data sources, licence and credit, people's data stays theirs).
Read it before every task. If a task would break a core rule, stop and say so.
Only the maintainer edits it. Personal preferences go in `rules/personal.md`
(git-ignored; start from [rules/personal.example.md](rules/personal.example.md)).

- **Commands:**
  ```sh
  python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.lock pytest
  python -m pytest -q                              # the whole suite; no network needed
  ruff check .                                     # settings in ruff.toml
  DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --reload
  python tools/demo_data.py                        # a demo collection to click around in
  ```
  On Windows, set `PYTHONUTF8=1` first.
- **Where things are:** `app/routes/` pages (one module per topic) and
  `app/web.py` (shared); `app/db.py` numbered database steps; `app/schema.sql`
  your own data; `app/catalog.sql` data that can be downloaded again;
  `app/prices.py` (`resolve`) is the only place a shown price is worked out;
  `app/sites.py` is the only way to reach another site; `docs/specs/` specs;
  `tests/`; `tools/` helper scripts.
- **Stack:** no front-end framework and no JavaScript build step. CSS and
  JavaScript live in `app/static/css` and `app/static/js`, one file per
  template, linked through `asset()`. New packages are pinned in
  `requirements.lock`, need a reason in the pull request, and must work on a
  small home server (amd64 and arm64).
- **Code:** logic lives in plain modules in `app/` that take a database
  connection. A database change is a new numbered `_step_N` in `app/db.py` that
  raises `LATEST`; never change an existing step, and say so at the top of the
  pull request (a newer database refuses to start on older code). Slow work
  goes to a background worker. Match the code around you; don't reformat lines
  you aren't changing. Everything the app shows is plain English.
- **Tests:** tests sit next to the tests for their area in `tests/` and never
  touch the network. Each acceptance criterion maps to at least one test. Never
  skip, disable or loosen a test to get green; Linux-only behaviour is skipped
  on other systems, not removed.
- **Specs:** each part of the app has a spec in [docs/specs/](docs/specs/README.md).
  Update it in the same pull request, or say in the pull request why it
  doesn't change.
- **Plans:** every plan has a wireframe or mockup of each screen it adds or
  changes, at phone width, linked or attached in the issue's Plan section (a
  linked page must open for anyone). No screen changes: "Wireframe: no screen
  changes".
- **Commits and pull requests:** short, saying only what changed; the why is on
  the issue. The pull request has `Closes #<issue>` and a `Host:` line only
  when someone running dexSTASH must act (a database step, a new setting).
  `CHANGELOG.md` gets a line under `## Unreleased` for every change people
  will notice.
- **Versions:** `__version__` in `app/__init__.py`. The releaser raises it when
  preparing a release (minor for a feature, patch for fixes only) and tags it
  `vX.Y.Z`; `tools/release_image.py <tag>` refuses a tag that differs from the
  version. Pull requests don't change the version.
- **Hooks:** `git config core.hooksPath tools/hooks`. `pre-commit` runs the
  scan; `pre-push` runs ruff, the rules check, gitleaks and the tests.
- **Project labels:** see [docs/github-setup.md](docs/github-setup.md#project-labels).
- **Project scan:** `tools/project-scan.sh` runs `tools/check_rules.py`
  (personal paths, e-mails, DuckDNS names, files that must never be committed,
  private words, commit authors).
- **Subagents:** the validator and reviewer are modes of the one assistant. Don't
  start subagents or multi-agent workflows unless the maintainer asks.

## Before you push

Run `tools/scan.sh --branch` (secrets and personal words, all local, plus the
project scan if there is one). Fix any hit. Never bypass the scan, edit `rules/private-words.txt`, or print its
contents. There are no other required checks; the Validate checklist is
advisory ([docs/lifecycle.md](docs/lifecycle.md)).

## Labels

Labels (including any project labels) are defined in
[docs/github-setup.md](docs/github-setup.md#labels). Use
only labels that already exist: don't create, rename, edit or delete them.
You may add or remove `needs-info`. Suggest `small`, `docs`, `priority:high`,
`blocked`, `duplicate` and `wontfix` in a comment and let the maintainer apply
them. Never change `bug`, `feature` or `process`. After any label change, comment which
label and why.

## Never

- Move a card, push to `main`, or merge a pull request.
- Run a command that changes a shared environment (deploy, publish,
  configure). Write the steps for a person to run.
- Invent requirements, rules or decisions that aren't written down.
- Commit secrets, credentials, personal data (names, addresses, emails,
  phone numbers) or environment IDs and URLs.
- Add attribution lines, sign-offs or links to assistant sessions to commits
  or pull requests.
- Start work the maintainer hasn't asked for. You may suggest it.

## Always

- Ask when something isn't settled, instead of guessing.
- Treat a person's comment on the issue as more current than anything an
  assistant wrote earlier.
- Work on a branch (`feature/`, `fix/`, `docs/`, `chore/`, `refactor/` or
  `test/` plus a short name). Open a pull request as a draft, only when asked,
  with `Closes #<issue>` (one issue per pull request unless the maintainer
  groups them).
- Keep changes small and in scope. List anything outside the plan in the pull
  request.
- Say which skill or command you're using, in one line, before using it.

## When you finish

Comment on the issue: what you did, what's left, and any questions with who
should answer them.
