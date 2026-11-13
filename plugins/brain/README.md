# brain

Plan-first orchestration for Claude Code. The top-tier model is spent only at three decision points - before a plan is locked, when the same error repeats, and before declaring the work done. Searching, building, checking and verifying are delegated to cheaper agents, and the main session stays in charge of the result.

## What's included

| Component | Name | Model | Role |
| --- | --- | --- | --- |
| Skill | `brain` | - | The workflow. Run it as `/brain:brain <task and done criteria>`. |
| Agent | `brain:brain` | fable, high effort | Read-only decision advisor (plan, repeated error, pre-done review). |
| Agent | `brain:opus-checker` | opus | Read-only check of the build against the plan and done criteria. |
| Agent | `brain:sonnet-checker` | sonnet | Read-only comparison of delivered values against fresh sources. |
| Agent | `brain:map-builder` | sonnet | Copies data that must be exact into a file and runs the named script. |
| Agent | `brain:scout` | haiku | Read-only search and evidence gathering. |
| Agent | `brain:implementer` | sonnet | Carries out a task whose scope is already settled. |
| Agent | `brain:reviewer` | inherits | Read-only second look at high-risk changes. |

The plugin has no repository-specific rules. If a project has an `AGENTS.md` or `CLAUDE.md`, the skill and agents read it first and defer to it; otherwise they use the defaults written into `skills/brain/SKILL.md`.

## Install

This repository doubles as a plugin marketplace: the manifest at the repository root (`.claude-plugin/marketplace.json`, marketplace name `brain-desk`) points at this folder.

From a local clone:

```bash
claude plugin marketplace add /path/to/daily_stock_analysis
claude plugin install brain@brain-desk
```

From GitHub:

```bash
claude plugin marketplace add OWNER/daily_stock_analysis
claude plugin install brain@brain-desk
```

Try it for one session without installing: `claude --plugin-dir /path/to/daily_stock_analysis/plugins/brain`.

To have a project offer it automatically, add this to that project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "brain-desk": { "source": { "source": "github", "repo": "OWNER/daily_stock_analysis" } }
  },
  "enabledPlugins": { "brain@brain-desk": true }
}
```

## Optional: attach an advisor

A plugin cannot set user settings. To let the session's advisor handle the three decision points (so `brain:brain` is not spawned), add this to `~/.claude/settings.json`:

```json
{ "advisorModel": "fable" }
```

This setting is experimental. Without it, the skill falls back to the `brain:brain` agent.

## Customising

- Models and effort live in each agent's frontmatter under `agents/`. The aliases (`fable`, `opus`, `sonnet`, `haiku`, `inherit`) need to be available to your account; change them if a session reports one as unavailable.
- Sub-agents that declare no model run at whatever your `CLAUDE_CODE_SUBAGENT_MODEL` setting says. Set it to `haiku` to keep unlabelled search agents on the light tier.
- Edit the **Defaults** section of `skills/brain/SKILL.md` to encode your own verification and delivery conventions.

## Limits

- The skill is namespaced, so the full command is `/brain:brain`. Whether a bare `/brain` also works depends on your client.
- Sub-agents are read-only by instruction and tool restriction, but the main session still decides about commits, pushes and tags.
- This plugin ships inside the repository and follows the repository's `LICENSE` (MIT).
- `plugins/brain/` is the portable, repository-independent variant of the repo-local `/brain` skill and agents in `.claude/skills/brain/` and `.claude/agents/`. The two are maintained by hand; when one changes, check whether the other needs the same change.
