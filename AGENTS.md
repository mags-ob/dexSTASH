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
2. **Plan.** Once the issue is marked ready, read the issue, the spec and the code it touches. Write the
   change list on the issue: what the app does now, what will change, and anything that contradicts an earlier
   decision. Add a wireframe or mockup of each screen the change adds or changes, at phone width: a published
   page linked from the Plan section, or an image attached to the issue. A linked page must open for anyone
   with the link. A change with no screen changes says "Wireframe: no screen changes". No code is written
   until the plan, with its wireframe or mockup, has its go.
3. **Build.** Start only after the go on the plan. Work on a branch named `feature/<short-name>`,
   `fix/<short-name>`, `docs/<short-name>`, `chore/<short-name>`, `refactor/<short-name>` or
   `test/<short-name>`, never on `main`. Code, tests and the
   spec update go in the same pull request, which says `Closes #<issue>`. One issue per pull request unless a
   maintainer asks to group related issues; a grouped pull request has one `Closes #<issue>` line for each.
   Commits and pull requests are short and say only what changed; the why is on the issue. No attribution
   lines, sign-offs or links to anyone's assistant sessions ([rules/project.md](rules/project.md)).
4. **Validate.** GitHub Actions runs the checks on every pull request. While Actions is switched off, a
   maintainer runs `python tools/local_checks.py <number>` from `main`, which reports the required checks (lint,
   rules check, gitleaks) on the pull request ([CONTRIBUTING.md](CONTRIBUTING.md)); whoever makes a code change
   runs the whole test suite before pushing. Each acceptance criterion should have a test. Changes to pages get
   a look at phone width. When a check or feedback finds a problem, the work goes back to step 2: update the
   Plan section and the wireframe or mockup and get a new go before changing any more code.
5. **Learn.** Anything non-obvious you found goes into the spec or the docs, so the next change starts
   from it.

## Threads and phases

An assistant works on one issue in one thread, ending in one pull request (several only when a maintainer
groups them, see step 3). It suggests work but never starts any a maintainer hasn't asked for. A bug becomes a
bug issue first; an urgent fix gets a patch release.

**Three gates.** The maintainer decides at each one; the assistant waits.

1. **Ready:** the issue's story and criteria are right. The maintainer moves the card to Ready.
2. **Go:** the plan and its wireframe or mockup are right. The plan goes in a "Plan" section of the issue
   body, never in a comment. The maintainer moves the card to In progress. Going back to Plan after a problem
   in Validate needs a new go.
3. **Approved:** the pull request is right. The maintainer says so or merges it.

A small issue (one bug, one screen) merges gates 1 and 2: the issue and its plan come together for one go.
The assistant never moves board cards; the board's workflows move the rest.

**Four kinds of thread.**

- **Intake:** the project chat, where requests arrive and become issues.
- **Issue:** one issue through Define, Plan, Build, Validate and Learn.
- **Spec:** Define only, writing or updating one spec.
- **Cycle:** one goal (a parent issue). It orders the sub-issues and suggests groupings; at the end it bumps
  the minor version, updates `CHANGELOG.md`, drafts the release for the maintainer to publish, and writes the
  cycle's notes in the parent issue's body.

**Tools.** Skills help where they fit: brainstorming for a vague idea, a written plan for a big change, a
code review before asking for gate 3. No subagents or multi-agent workflows unless the maintainer asks.

## Version and changelog

A pull request that changes the app's code (`app/`, `Dockerfile`, `requirements.lock`, `deploy/`) checks both
before it's pushed ([rules/project.md](rules/project.md) › Versions):

- `CHANGELOG.md` has its line under `## Unreleased`, for every change people will notice.
- `__version__` in `app/__init__.py` is newer than the last release tag. The first such pull request after a
  release raises it: the minor version for a new feature, the patch version when there are only fixes. A later
  one raises the minor version if it adds a feature and the version was only raised for fixes.

A release renames `## Unreleased` to the version, with a one-line summary and any "After updating" steps, and
the version is the tag (`v0.29.0` for 0.29.0). While GitHub Actions is off, the maintainer builds and publishes
the release image with `python tools/release_image.py <tag>` once the Release is published.

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
