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
2. **Write the plan** in the issue, short: each file and what changes in it.
   Put the old and new wording, if it needs spelling out, in a plan file in
   `docs/plans/`. Note anything that now contradicts another file.
3. **Wait for the maintainer's go.**
4. **Edit** the process files on a `docs/` or `chore/` branch. Keep every
   copy of a rule in step. Update `CHANGELOG.md` if people will notice.
5. **Check:** follow links, grep for the old wording, run
   `tools/scan.sh --branch`.
6. **Open a draft pull request** with `Closes #<issue>`, only when asked.
   Say which rules changed.

A process issue can be amended like any other
([Amending an issue](../docs/lifecycle.md#amending-an-issue)): the change goes
in the issue body, gets a go, and stays on the same branch and pull request.

## May write to

The process files (except `rules/core.md`, which only the maintainer edits)
and `CHANGELOG.md`. No product code, tests or product docs.

## Stop and ask when

- The change would let an assistant pass the Go or Approved gate, merge, or
  skip the local scan. Those stay the maintainer's.
- A rule is being loosened with no reason on the issue.
- The change needs repository settings changed. Write the steps for the
  maintainer to run. Labels and board cards you may change yourself.

## Hand-off

A pull request the maintainer reviews. A process change takes effect only
once the maintainer merges it; until then the old rules apply.
