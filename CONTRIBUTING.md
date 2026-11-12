# Contributing

Everyone, including AI assistants, follows the same path. See
[docs/lifecycle.md](docs/lifecycle.md) for detail.

1. **Open an issue** with the Bug, Feature or Process change form. There are no
   other options. A Process change is for changing the workflow, agents,
   concepts or lifecycle themselves; only the maintainer approves those.
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
- Open the issue and wait for a go before sending a pull request for a feature;
  small fixes can go straight to a pull request.
- Run `git config core.hooksPath tools/hooks` once. Commits are scanned locally
  for secrets and personal information (`tools/scan.sh`), and a push also runs
  ruff, the rules check and the tests (`git push --no-verify` skips it once).
  Install gitleaks (`brew install gitleaks`). Copy
  `rules/private-words.example.txt` to `rules/private-words.txt` and add your
  own words; that file is never committed.

## Running it

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest
DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --reload
```

The tests need no network: every outside request is replaced with a fake one.
`tools/demo_data.py` makes a demo collection to click around in. On Windows, set
`PYTHONUTF8=1` first. The code is mapped in
[docs/architecture.md](docs/architecture.md), and the project's code rules are in
[AGENTS.md](AGENTS.md#project).

## What must never be committed

- Card data, card pictures, sprites, Pokédex text, Bulbapedia text, Chinese
  dataset files, prices or models. They're downloaded into `data/` while the app
  runs, under their owners' terms (see [NOTICE.md](NOTICE.md)).
- `.env`, `data/`, or anything with a real domain, server name, password or key
  in it. Examples use `misty` and `cards.example.com`.

Using an AI assistant? Point it at [AGENTS.md](AGENTS.md). For a security
problem, see [SECURITY.md](SECURITY.md). By contributing you agree that your
contribution is licensed under the [AGPL-3.0](LICENSE).
