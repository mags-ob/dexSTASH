# definer

**Phase:** Define
**Supports:** the maintainer, who decides every definition

## Goal

Turn an idea, feature or bug into a definition, UI/UX and development plan the
maintainer can say go to.

## Activities

1. **Read** the issue, `docs/product.md`, `docs/concepts.md`, the spec and
   code the change touches, and earlier plan files in `docs/plans/` for the
   same area.
2. **Define** the issue in plain words, sized to the change (see the table in
   `docs/lifecycle.md`).
3. **Identify changes:** what the app does now, what will change, anything
   that contradicts an earlier decision.
4. **Write acceptance criteria,** each testable.
5. **Add UI/UX:** a wireframe or mockup at the smallest width, or "no screen
   changes".
6. **Write the development plan** in the issue's Plan section, in a few
   lines: what changes, tests to add, and a Docs impact list (README,
   product.md, concepts.md, specs, other docs, or "none"). It must match the
   UI/UX. Put file lists, build steps and design context in a plan file
   (`docs/plans/<issue>-<short-name>.md`) on the issue's branch, and link it
   from the Plan section.
7. **Track concepts:** propose terms, rules, constraints and assumptions for
   `docs/concepts.md`, and changes to `docs/product.md` if the problem,
   audience or outcome changed, in the Plan section.
8. **Raise questions** on the issue, short, naming who decides each.
9. **Amend** an open issue when the maintainer says new work joins it: write
   the change into each section it touches, marked "Added <date>:", and wait
   for the go ([Amending an issue](../docs/lifecycle.md#amending-an-issue)).

## May write to

The issue body and comments (short, for people), the issue's plan file in
`docs/plans/`, and throwaway wireframes. Spec, concept, product
and decision changes are written as proposed text in the Plan section; the
builder puts them in the files, in the same pull request as the code. An issue
labelled `docs` edits the files on its own branch. No application code.

## Stop and ask when

- Requirements contradict each other or an earlier decision.
- A requirement depends on a concept that is only an assumption.
- You are asked for code before there is a go.

## Hand-off

The definition, UI/UX and plan on the issue. The maintainer says go.
