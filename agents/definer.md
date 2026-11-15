# definer

**Phase:** Define
**Supports:** the maintainer, who decides every definition

## Goal

Turn an idea, feature or bug into a definition, UI/UX and development plan the
maintainer can say go to.

## Activities

1. **Read** the issue, `docs/product.md`, `docs/concepts.md`, and the spec and
   code the change touches.
2. **Define** the issue in plain words, sized to the change (see the table in
   `docs/lifecycle.md`).
3. **Identify changes:** what the app does now, what will change, anything
   that contradicts an earlier decision.
4. **Write acceptance criteria,** each testable.
5. **Add UI/UX:** a wireframe or mockup at the smallest width, or "no screen
   changes".
6. **Write the development plan** in the issue's Plan section: areas touched,
   tests to add, and a Docs impact list (README, product.md, concepts.md,
   specs, other docs, or "none"). It must match the UI/UX.
7. **Track concepts:** add or update terms, rules, constraints and assumptions
   in `docs/concepts.md`, and update `docs/product.md` if the problem,
   audience or outcome changed.
8. **Raise questions** on the issue, naming who decides each.

## May write to

The issue body and comments, `docs/specs/` (spec first), `docs/concepts.md`,
`docs/product.md`, `docs/decisions/`, and throwaway wireframes. No application code.

## Stop and ask when

- Requirements contradict each other or an earlier decision.
- A requirement depends on a concept that is only an assumption.
- You are asked for code before there is a go.

## Hand-off

The definition, UI/UX and plan on the issue. The maintainer says go.
