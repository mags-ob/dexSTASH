# Project rules

How dexSTASH is built. They may change by a reviewed pull request, but they never loosen a
[core rule](core.md).

## Stack

- Python with FastAPI, Jinja templates and htmx, SQLite, and Docker. Keep it this way: no React or other
  front-end framework, and no JavaScript build step.
- CSS and JavaScript live in `app/static/css` and `app/static/js`, one file per template, and are linked
  through `asset()` so browsers pick up changes.
- New packages are pinned in `requirements.lock`, need a reason in the pull request, and must work on a small
  home server (amd64 and arm64).

## Code

- Pages live in `app/routes/`, one module per topic. Logic lives in plain modules in `app/` that take a
  database connection, so it can be tested without the web layer.
- **Database changes** are a new numbered `_step_N` in `app/db.py` that raises `LATEST`. Never change an
  existing step. A newer database refuses to start on older code, so a pull request with a step says so at the
  top.
- Your own data's tables go in `app/schema.sql`. Data that can be downloaded again goes in `app/catalog.sql`,
  which lives in the separate catalog database and is never backed up.
- **Prices:** a shown price comes only from `prices.resolve`. Each source writes its quotes to `price_quotes`.
  The main price is the average of the sources a printing has, and each source keeps its own history.
- **Speed:** searches and page loads come first. Slow work (downloads, scans, price runs) goes to a background
  worker at low priority.
- Match the code around you: short functions, plain names, and a docstring saying what a function gives back.
- `ruff check .` must pass (settings in `ruff.toml`). Code is not auto-formatted, so don't reformat lines you
  aren't changing.
- New package versions must pass `pip-audit` (the Checks workflow runs it on `requirements.lock`).

## Words

- Everything the app shows people is plain English: full sentences, no jargon, no internal names.
- Comments explain why, in plain English.

## Tests

- Every change comes with tests, next to the tests for its area in `tests/`. Each acceptance criterion on the
  issue should map to at least one test.
- Tests never touch the network: every outside request is replaced with a fake one.
- Linux-only behaviour (file permissions, process priority) is skipped on other systems, not removed.
- Never skip, disable or loosen a test to get a green run.

## Specs

- Each part of the app has a spec in [docs/specs/](../docs/specs/README.md). A pull request that changes what a
  part does updates its spec in the same pull request, or says why the spec doesn't change.
- A spec is written from the current code the first time an issue touches that part.

## Branches, commits and pull requests

- Branches: `feature/`, `fix/`, `docs/`, `chore/`, `refactor/` or `test/`, then a short name for the change in
  lower case with dashes (`feature/release-image-files`), never a tool's own name for its session. Nothing is
  committed straight to `main`. The Checks workflow refuses other names.
- **Commits:** one short summary line saying what changed (`Add image files to releases`), then at most a
  few short lines of what changed if the summary isn't enough, and the issue (`#12`). The why lives in the
  issue, not the commit.
- **Pull requests:** a short title like a commit summary. The description is the template: what changed in a
  few bullets, `Closes #<issue>`, the `Spec:` line, and `Host:` only when a host must act. No testing
  narrative (the checks show it) and no reasoning (the issue holds it).
- **One team.** Commits, pull requests, issues and comments carry no attribution lines (`Co-Authored-By`,
  "Generated with ..."), no sign-offs, no links to anyone's assistant sessions or projects, and no "requested
  by". Who wrote a commit is in its author field.
- One issue per pull request by default. Several related issues may share one pull request when a maintainer
  asks for that, each with its own `Closes #<issue>` line. Every issue in a pull request comes from a
  maintainer's request.
- `CHANGELOG.md` gets a line for every change people will notice.

## Versions

- Versions stay at 0.x until the first public release, then 1.0.0. Each cycle of work raises the minor
  version, and fixes raise the patch version.
- `main` never carries a released version number with unreleased app changes: the first pull request that
  changes the app's code after a release raises `__version__` (minor for a feature, patch for fixes only), and
  every one adds its `CHANGELOG.md` line under `## Unreleased`.
- A release's tag is its version with a `v` (`v0.29.0`); the image build stops when they differ.
