Closes #<!-- issue number -->

## What changed
<!-- Short. The why is on the issue. -->

## Outside the plan
<!-- List anything not in the issue's Plan, or write "None". -->

Host:
<!-- Only if someone running dexSTASH must act (a database step, a new setting). Otherwise delete this line. -->

## Tests run
<!-- Name the ones you ran and the result. See docs/lifecycle.md#tests. -->
- Unit:
- Regression (bug fixes):
- Integration:
- Other:

## Checklist (advisory; mark N/A with a reason)
- [ ] `tools/scan.sh --branch` is clean
- [ ] Tests pass and lint is clean; each acceptance criterion is covered
- [ ] Code review
- [ ] Security review
- [ ] AI-slop reduction (dead code, needless abstraction, noisy comments)
- [ ] Efficiency review
- [ ] Acceptance criteria walked through on the running change
- [ ] UI checked at smallest width, and accessibility (or no screen changes)
- [ ] Docs aligned: README, product.md, concepts.md, specs and other docs match this change; changelog added
