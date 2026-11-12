# GitHub setup

What to configure on a new repository so the process in
[lifecycle.md](lifecycle.md) works. All of it is free and none of it needs
GitHub Actions. Do it once, in this order.

## 1. Repository

- The repository is `obiehq/dexSTASH`. `.github/CODEOWNERS` names the maintainer.
- **Settings > General > Features:** turn on Issues, Projects and (optional)
  Discussions. Turn off Wikis.
- **Pull Requests:** allow squash merging only, default to the pull request
  title, and turn on "Automatically delete head branches".
- **Settings > Actions:** keep Actions off. The process doesn't use them. `tests.yml`, `image.yml` and
  `pr-image.yml` only matter if Actions is switched on; while it is off, the maintainer builds a release image
  with `python tools/release_image.py <tag>`.

## 2. Access and branch protection

- **Collaborators:** add only people you allow, with the Write role. Everyone
  else forks and opens a pull request.
- **Protect `main`** (Settings > Branches, or Rulesets):
  - Require a pull request before merging.
  - Block force pushes and deletion.
  - Required approvals: 0 for solo work, 1 once you have contributors.
  - Don't require status checks (remove the old required checks: lint, rules check and gitleaks).
  - Don't enforce for administrators, so the maintainer can merge.
- **Security:** turn on private vulnerability reporting and secret
  scanning with push protection (free on public repositories). Add a
  `SECURITY.md` if the repository is public.

## 3. Issue forms

- Three forms exist: [Bug](../.github/ISSUE_TEMPLATE/bug.yml),
  [Feature](../.github/ISSUE_TEMPLATE/feature.yml) and
  [Process change](../.github/ISSUE_TEMPLATE/process.yml).
- `config.yml` turns off blank issues, so everything starts as one of the three.
- Not sure which? Discussions is the place to ask first.

## 4. Labels

Run `tools/setup-labels.sh` once (needs `gh`). It creates the labels in
[.github/labels.yml](../.github/labels.yml). GitHub's default labels can be
deleted. Phase is not a label: it is the board column.

| Label | Kind | Set by | Meaning |
| --- | --- | --- | --- |
| `bug` | Type | Bug form | Something doesn't work as expected |
| `feature` | Type | Feature form | A new capability or change. Docs, chores, refactors and tests also use this |
| `process` | Type | Process change form | A change to the workflow, agents, lifecycle or other process files (not product.md or concepts.md). The only type that may edit them |
| `small` | Size | Maintainer | Short Define: the issue body is the plan (see [Small changes](lifecycle.md#small-changes)) |
| `docs` | Scope | Maintainer | Documentation only; no code changes |
| `priority:high` | Priority | Maintainer | Do before other work. No label means normal |
| `needs-info` | Status | Anyone | Waiting on an answer before it can move on |
| `blocked` | Status | Maintainer | Waiting on something outside the issue |
| `duplicate` | Close reason | Maintainer | Already covered by another issue |
| `wontfix` | Close reason | Maintainer | Decided not to do |

Every issue has exactly one of `bug`, `feature` or `process`. The rest are optional.
Assistants only use existing labels and say why when they change one.

### Project labels

A project may add its own labels (for example `area:api`) for things the
template doesn't cover. Add them to `.github/labels.yml` and list each one in
the table below with its meaning and who sets it, through a `process` issue.
They follow the same rules as the template labels: assistants only suggest
them, and they never replace the type labels or describe the phase.

| Label | Kind | Set by | Meaning |
| --- | --- | --- | --- |
| `db-step` | Scope | Maintainer | The change adds a database step: older versions won't start on the upgraded database |
| `accessibility` | Scope | Maintainer | About keyboard use, labels, contrast or screen readers |
| `help wanted` | Community | Maintainer | Open for a contributor to take |
| `good first issue` | Community | Maintainer | A good start for a new contributor |

Remove the old labels `spec`, `chore`, `documentation`, `enhancement`, `invalid`, `question`, `already-built`,
`test image` and `needs-decision` (use `needs-info`) after relabelling any issue that has them. Spec work is a
Feature with the `docs` label.

## 5. Project board

Use one Project (Board layout) with a **Status** field and four columns. An older board is renamed in place:
Backlog becomes Define, Ready and In progress both become Build, In review becomes Validate.

| Column | Phase | Moved by |
| --- | --- | --- |
| Define | Define | New issues land here |
| Build | Build | Maintainer, at the **Go** gate |
| Validate | Validate | Maintainer, when the pull request is ready for review (a manual move; GitHub's built-in workflows don't do this) |
| Done | Done | Maintainer, at the **Approved** gate; closing the issue does it automatically |

Turn on the built-in Project workflows (free): **Item added to project** sets
Status to Define, **Item closed** sets Status to Done, and **Auto-add to
project** pulls in new issues from the repository.

Merged and Released are not columns. Releases are tags on `main`.

## 6. Local tools (each contributor)

- `git config core.hooksPath tools/hooks` (the commit hook runs the scan; the push hook runs ruff, the rules
  check, gitleaks and the tests)
- Install gitleaks (`brew install gitleaks`).
- Copy `rules/private-words.example.txt` to `rules/private-words.txt` and add
  your own words. This file is git-ignored.

## 7. Releases

- Tag `main` with the version (`v0.2.0`) and publish a GitHub Release with the
  notes. Releases are made by the maintainer ([releaser](../agents/releaser.md)).
- Milestones are optional, to group issues for a release.

## Checklist

- [ ] Squash-only merging, branch auto-delete, Actions off
- [ ] Collaborators added; `main` protected as above, old required checks removed
- [ ] Private vulnerability reporting and secret scanning turned on
- [ ] `tools/setup-labels.sh` run
- [ ] Project board with Define, Build, Validate, Done and its workflows
- [ ] Local tools set up
- [ ] Repository secret `DEXSTASH_PRIVATE_WORDS` deleted once the words are in `rules/private-words.txt`
