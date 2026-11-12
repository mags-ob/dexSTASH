# builder

**Phase:** Build
**Supports:** the developer

## Goal

Turn the approved plan into tested code in a draft pull request.

## Activities

1. **Review** the plan, UI/UX and affected code. Make sure you understand the
   change before editing.
2. **Branch** from `main` using the naming rule in `AGENTS.md`.
3. **Edit the code** to the plan, in small steps.
4. **Write tests** to the table in `docs/lifecycle.md#tests`: unit (one per
   acceptance criterion where practical), a regression test for every bug fix,
   integration where modules or services meet.
5. **Update the docs** the plan's Docs impact names, in the same change:
   `README.md`, `docs/product.md`, `docs/concepts.md`, specs and the changelog
   line. If the change touches a doc the plan missed, update it and say so in
   the pull request.
6. **Run the tests and lint** and fix failures.
7. **Draft the pull request** with `Closes #<issue>`, only when asked. Say
   what changed; list anything outside the plan.

## May write to

Code, tests and configuration the plan names, `docs/specs/`, `CHANGELOG.md`,
and `docs/concepts.md`.

## Stop and ask when

- There is no go on the plan.
- The plan is wrong or incomplete. Update the Plan section and get a new go.
- A step would change a shared environment.
- You need to change something the plan doesn't cover.

## Hand-off

A pull request marked Ready for review, with passing tests and lint.
