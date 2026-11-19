# Plan files

An issue is written for people: short, plain words, about a minute to read.
When the work needs more than that, the detail goes here, in
`<issue>-<short-name>.md` (for example `106-short-readable-issues.md`), linked
from the issue's Plan section.

Plan files are kept after the work merges. They are the record of design
context, so a later plan can point back to what was done instead of digging
through old issues. Read the ones for your area before planning.

## What goes in one

Only what helps; leave out headings you don't need.

- **Issue:** a link to it.
- **Context:** what the area does now, and earlier decisions or plan files it
  builds on.
- **Design:** the approach, and anything considered and set aside, with why.
- **Files:** what changes in each.
- **Steps:** the build order.
- **Tests:** what each acceptance criterion is checked by.
- **Notes:** anything learned while building.

## Rules

- The issue decides; the plan file explains. If the plan file changes what
  the issue says, update the issue and get a new go.
- Write it in Define on the issue's branch, keep it in step during Build, and
  merge it with the work.
- An amendment gets its own "Added <date>:" part, as in the issue.
- Same rules as everything else in the repository: nothing personal, no
  secrets ([rules/core.md](../../rules/core.md)).
