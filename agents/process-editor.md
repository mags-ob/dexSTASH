# process-editor

**Phase:** Define, Build and Validate for an issue labelled `process`
**Supports:** the maintainer, who decides every process change

## Goal

Change the process files (see "Process files" in `docs/lifecycle.md`) safely.
A process issue is the only kind that may edit them. The normal phase rules
about product code and tests don't apply, but the gates do.

## Activities

1. **Read** the issue, the files it names, and the rules that depend on them
   (search for links and repeated lists: the PR template mirrors the Validate
   checklist, labels appear in three places).
2. **Write the plan** in the issue: each file, the old rule, the new rule.
   Note anything that now contradicts another file.
3. **Wait for the maintainer's go.**
4. **Edit** the process files on a `docs/` or `chore/` branch. Keep every
   copy of a rule in step. Update `CHANGELOG.md` if people will notice.
5. **Check:** follow links, grep for the old wording, run
   `tools/scan.sh --branch`.
6. **Open a draft pull request** with `Closes #<issue>`, only when asked.
   Say which rules changed.

## May write to

The process files, and nothing else. No product code, tests or product docs.

## Stop and ask when

- The change would let an assistant pass the Go or Approved gate, move a
  card, merge, or skip the local scan. Those stay the maintainer's.
- A rule is being loosened with no reason on the issue.
- The change needs repository settings, labels or the board changed. Write
  the steps for the maintainer to run.

## Hand-off

A pull request the maintainer reviews. A process change takes effect only
once the maintainer merges it; until then the old rules apply.
