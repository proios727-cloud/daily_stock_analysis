# Runbook: dev and repo chores

These already have skills. Use them; do not write new ones.

| Chore | Skill | How to invoke |
| --- | --- | --- |
| Understand or triage an issue | `analyze-issue` | `/analyze-issue <issue number or URL>` |
| Review a pull request | `analyze-pr` | `/analyze-pr <PR number or URL>` |
| Fix an issue end to end | `fix-issue` | `/fix-issue <issue number or URL>` |
| Any larger task that needs a plan, a build and a check | `brain` | `/brain <task and done criteria>` |
| Start something new and decide whether to build it | `kickoff` | `/kickoff:kickoff <idea>` |

`analyze-issue`, `analyze-pr` and `fix-issue` are repository skills and exist only where that repository's `.claude/skills/` is present. `brain` and `kickoff` are also available as portable plugins.
