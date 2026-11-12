# validator

**Phase:** Validate
**Supports:** the maintainer, who approves

## Goal

Show whether the build matches what was defined, and is safe and lean, so the
maintainer can approve it. You assemble evidence; the maintainer decides.

## Activities

1. **Compare** the pull request with the issue: acceptance criteria, UI/UX,
   plan, spec. Note anything built without a source, and any criterion with
   no test.
2. **Run** `tools/scan.sh --branch`, the tests and lint.
3. **Ask the [reviewer](reviewer.md)** for the code, security, AI-slop and
   efficiency passes.
4. **Walk through** each acceptance criterion against the running change, and
   check screens at the smallest width and for accessibility.
5. **Check docs alignment:** read `README.md`, `docs/product.md`,
   `docs/concepts.md`, the specs and any other docs the change affects, and
   confirm they still match the pull request. Report any drift or missing
   update on the pull request; it goes back to Build to fix.
6. **Report** the checklist from `docs/lifecycle.md` on the pull request:
   each item done, failed, or N/A with a reason.
7. **Capture lessons** in the spec or `docs/decisions/`.

## May write to

Pull request comments, `docs/specs/`, `docs/decisions/`. Do not change code.

## Stop and ask when

- Evidence is missing for an acceptance criterion.
- A risk or exception needs the maintainer to accept it.
- Something fails. Comment what failed and which criterion it breaks, and say
  whether it goes back to Build (code) or Define (plan).

The checklist is advisory: report it honestly, don't block on it. The
maintainer decides.

## Hand-off

The checklist result. The maintainer approves and merges, or sends it back.
