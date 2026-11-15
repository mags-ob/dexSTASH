# docs/lifecycle.md

Every change moves through four phases: **Define, Build, Validate, Done**.
It works for one developer and stays consistent when others contribute.
Rules for AI assistants are in [AGENTS.md](../AGENTS.md); each phase's
playbook is in [agents/](../agents/).

Two gates need a maintainer's decision. An assistant never passes them.

1. **Go** (Define to Build): the definition and plan are right.
2. **Approved** (Validate to Done): the pull request is right, and the
   maintainer decides to merge it.

Nothing else blocks a merge. There are no required status checks. The
checklists below are advisory: they keep work consistent, and the maintainer's
judgment decides. The one check that always runs is the local
[secrets and personal-words scan](#local-scan).

## Small changes

A typo, a doc fix or a tiny bug can skip a long Define (label it `small`): the
issue is one sentence, the plan is "fix X in Y", and the maintainer's go can
be a thumbs-up.
It still gets a branch, a pull request and the local scan.

## Where state lives

- The **issue** is the single source of truth: definition, acceptance
  criteria, wireframe and plan all live in its body (the plan in a "Plan"
  section, never in a comment).
- The **project board column** is the phase (Define, Build, Validate, Done).
  Moving a card is the gate decision. Assistants never move cards.
- **Labels** describe the issue, not the phase. See
  [github-setup.md](github-setup.md#labels).
- Behaviour that outlives the issue goes in [specs/](specs/); decisions worth
  keeping go in [decisions/](decisions/).

## Phases

### 1. Define

**Question:** What are we changing, and how will we know it works?

Scale this to the size of the change.

| Kind | Define needs |
| --- | --- |
| Bug | Steps to reproduce, expected vs actual, a one-line plan |
| Feature | User story, acceptance criteria, UI/UX, development plan |

There are three issue types: Bug, Feature and Process change. Docs, chores,
refactors and tests are filed as a Feature (usually with the `small` and `docs`
labels) or as a Bug if something is broken. A change to how the process itself
works is a Process change. See [github-setup.md](github-setup.md#labels).

Write down:

- **Definition:** the issue, feature or bug in plain words.
- **Changes:** what the app does now, what will change, and anything that
  contradicts an earlier decision.
- **Acceptance criteria:** each one testable.
- **UI/UX:** a wireframe or mockup at the smallest screen width, or
  "UI/UX: no screen changes".
- **Development plan:** files and areas touched, tests to add, and a
  **Docs impact** list: which of `README.md`, `docs/product.md`,
  `docs/concepts.md`, the specs and other docs the change touches, or "none".
  It must match the UI/UX.

**Exit (Go):**
- [ ] Definition and acceptance criteria are written
- [ ] UI/UX is attached, or says no screen changes
- [ ] A development plan exists and matches the UI/UX
- [ ] Open questions are answered or listed with who decides
- [ ] A maintainer has said go (moved the card to Build)

### 2. Build

**Question:** Is it built to the plan?

- Review the plan, the UI/UX and the affected code before editing.
- Work on a branch named `feature/`, `fix/`, `docs/`, `chore/`, `refactor/` or
  `test/` plus a short name. Never work on `main`.
- Change the code, add unit tests (one per acceptance criterion where
  practical), and update the spec in the same change.
- Open a draft pull request that says `Closes #<issue>`. Keep commits and the
  pull request short: what changed; the why is on the issue.

**If the plan turns out wrong:** stop, update the issue's Plan section (and
the wireframe if it changes), and get a new go before changing more code.

**Exit:**
- [ ] Code, tests and the docs named in the plan's Docs impact are in the
      pull request
- [ ] The tests and lint pass locally
- [ ] Anything outside the plan is listed in the pull request
- [ ] The pull request is marked Ready for review

### 3. Validate

**Question:** Is it right, safe and no bigger than it needs to be?

Work through the checklist. It is advisory, not a merge blocker. Mark an item
N/A with a reason. For a small change, one honest self-review of the diff is
enough.

- [ ] Local scan is clean (`tools/scan.sh --branch`)
- [ ] Tests pass and lint is clean (see [Tests](#tests)), and each acceptance
      criterion is covered
- [ ] Code review: correctness, readability, consistency with the codebase
- [ ] Security review: input handling, secrets, permissions, dependencies
- [ ] AI-slop reduction: dead code, needless abstraction, noisy comments,
      duplicated logic, invented requirements
- [ ] Efficiency review: needless work, queries, loops, bundle or image size
- [ ] Walk through the acceptance criteria against the running change
- [ ] UI checked at the smallest width (if screens changed)
- [ ] Docs aligned: `README.md`, `docs/product.md`, `docs/concepts.md`, the
      specs and other docs still match what the pull request does, including
      anything the plan's Docs impact missed. Changelog entry added

The pull request template carries this same list. Keep the two in step.

#### Tests

Tests are suggested, scaled to the change. Say which ones you ran, and mark
the rest N/A with a reason.

| Test | When | Who |
| --- | --- | --- |
| Unit | Every code change: one per acceptance criterion where practical | Builder |
| Regression | Every bug fix: a test that fails without the fix | Builder |
| Integration | Changes that cross modules, storage or an external service (use fakes, not the network) | Builder |
| Lint and format | Every code change | Builder |
| Acceptance walkthrough | Every feature: step through each criterion on the running change | Validator |
| UI / responsive | Any screen change: smallest width and the usual one | Validator |
| Accessibility | Any screen change: keyboard use, labels, contrast | Validator |
| Security | Input, auth, files or dependencies change: see the review pass | Reviewer |
| Performance | Hot paths, large data, startup or image size | Reviewer |
| Manual exploratory | Anything the automated tests can't judge | Validator |

The project's actual commands live in `AGENTS.md` once the stack is chosen.

**If it fails:** comment what failed and which criterion it breaks. A code
fix goes back to Build on the same pull request. If the plan was wrong, go back
to Define and get a new go.

**Exit (Approved):** a maintainer reviews the pull request and decides to
merge it. Unticked items are fine if the maintainer accepts the risk.

### 4. Done

**Question:** Is it in `main`, and is it released?

Done has two states.

- **Merged:** the pull request is merged and the issue closes. `main` always
  holds code that could be packaged.
- **Released:** merged work is bundled into a release. Many issues can ride in
  one release.

**Release steps (maintainer):**
- [ ] `CHANGELOG.md` has a line for each noticeable change, then the section
      is renamed to the new version
- [ ] The version is bumped (minor for a feature, patch for fixes only)
- [ ] The tag matches the version (for example `v0.2.0`) and the package or
      image is built and published from it
- [ ] The release notes link the issues included

## Rework

A failed Validate goes back to Build for a code fix, or to Define when the
plan or wireframe was wrong. Never patch around a bad plan in code.

## Local scan

`tools/scan.sh` checks added lines for secrets (with
[gitleaks](https://github.com/gitleaks/gitleaks), if installed) and for
personal words listed in `rules/private-words.txt`. That file is git-ignored,
so the words never reach the repository; start from
`rules/private-words.example.txt`. It runs locally, so it needs no GitHub
Actions.

- `tools/scan.sh`: staged changes (also runs on every commit via the
  pre-commit hook).
- `tools/scan.sh --branch`: everything the branch adds compared with `main`.
- `tools/scan.sh --all`: every tracked file.

Set the hook up once: `git config core.hooksPath tools/hooks`. Assistants run
`tools/scan.sh --branch` before asking to push or open a pull request. A hit is
fixed, not bypassed.

## Repository settings

Branch protection, access, labels and the project board are set up once. See
[github-setup.md](github-setup.md). In short: `main` is protected with no
required checks, only invited collaborators can push branches, and everyone
else forks.

## Process changes

These files define the process: `AGENTS.md`, `agents/`, `docs/lifecycle.md`,
`docs/github-setup.md`, `CONTRIBUTING.md`, `.github/` (forms, PR template,
labels, CODEOWNERS), `tools/` and `rules/`.

`docs/product.md` and `docs/concepts.md` are not process files. They are living
documents that any issue updates as the solution changes, in the same pull
request as the work, with no extra approval step.

- Only an issue labelled `process` may change the process files.
- Bugs and Features don't edit them. If one needs a process change, open a
  Process change issue and link it.
- A process issue follows the [process-editor](../agents/process-editor.md)
  playbook. It still needs a go before editing and the maintainer's approval
  to merge, and it takes effect only once merged.
- It may not weaken the gates: assistants still never pass Go or Approved,
  move cards, merge, or skip the local scan.

## Docs-only changes

A change with the `docs` label touches only documentation. Use the `small`
shortcut when it is tiny. It still uses a branch and a pull request. It may
not change what a requirement, a concept or a decision means; that
needs a full Define.
