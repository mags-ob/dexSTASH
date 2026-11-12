# Agents

One playbook per phase, plus a reviewer used during Validate. Each file is
tool-neutral: any AI assistant reads the file for the issue's current phase
and acts as that agent. The rules in [AGENTS.md](../AGENTS.md) apply to all.

| Phase | Agent |
| --- | --- |
| Define | [definer](definer.md) |
| Build | [builder](builder.md) |
| Validate | [validator](validator.md), with [reviewer](reviewer.md) |
| Done | [releaser](releaser.md) |
| `process` issues | [process-editor](process-editor.md) |

## File shape

Phase, Supports, Goal, Activities, May write to, Stop and ask when, Hand-off.
Keep each file short.
