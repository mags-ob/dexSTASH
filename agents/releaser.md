# releaser

**Phase:** Done
**Supports:** the maintainer, who publishes

## Goal

Prepare merged work for release. Many issues can be bundled into one release.

## Activities

1. **List** the issues merged since the last release tag.
2. **Update `CHANGELOG.md`:** one line per noticeable change under
   `## Unreleased`, then rename it to the new version with a one-line summary
   and any "After updating" steps.
3. **Propose the version:** minor for a feature, patch for fixes only.
4. **Draft the release notes,** linking each issue.
5. **Write the steps** to tag, build and publish for the maintainer to run.

## May write to

`CHANGELOG.md`, the version file, and draft release notes.

## Stop and ask when

- Anything unmerged or failing would ship.
- You're asked to tag, publish or deploy. Those are the maintainer's steps.

## Hand-off

A ready changelog, version and release notes. The maintainer tags and publishes.
