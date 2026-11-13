---
name: brain
description: Plan-first orchestration that spends the top-tier model only on decisions. The brain agent plans and reviews, the main session or implementer builds, scout searches, opus-checker checks the build, sonnet-checker verifies values. Use when the user types /brain or asks for the brain workflow.
argument-hint: "<task and done criteria>"
---

# Brain

Run a task with the top-tier model used only at decision points.

## Project rules first

If the repository has `AGENTS.md`, `CLAUDE.md` or an equivalent rules file, read it before step 1. Where it conflicts with this skill, the repository's rules win. If there is none, use the **Defaults** at the bottom.

## Usage

```text
/brain:brain <task and done criteria>
```

(Plugin skills are namespaced by plugin name. If your client resolves bare names, `/brain <task>` also works.)

## Roles

Agent names are namespaced by the plugin (`brain:<agent>`). If your client lists them without the prefix, use the bare name.

| Role | Agent | Model | Does |
| --- | --- | --- | --- |
| Decide | advisor, else `brain:brain` | top tier, high effort | Plans, diagnoses a repeated error, reviews before "done". Never edits files. |
| Build | main session or `brain:implementer` | mid tier | Makes the changes and runs the checks. |
| Build data | `brain:map-builder` | mid tier | Pulls data that must be copied exactly (a quote table, an option chain, a config dump) into a file and runs the named script on it. Never the light tier. |
| Search | `brain:scout`, or any agent without its own model | light | Read-only lookup and research, several in parallel when there are several questions. |
| Check | `brain:opus-checker` | upper tier | Judges what was built against the plan and done criteria, right after the build. |
| Verify | `brain:sonnet-checker` | mid tier | Compares delivered values and claims against fresh sources. |
| Review | `brain:reviewer` | main session model | Second look at high-risk changes before merge. |

Models and effort are set in each agent's frontmatter, not here.

## Steps

1. Restate the task and its done criteria. If it takes only a few tool calls, do it directly and skip the rest: do not delegate for its own sake.
2. Gather facts. Use `scout` (or several in parallel) for lookups that span many files or sources; do small lookups yourself. Use `map-builder` when the facts are data that must be copied exactly.
3. Lock the plan. Consult the advisor if one is attached to the session; otherwise send `brain` the task, the done criteria, what was found and what is ruled out. Do not start building until the plan is back. If the plan touches a high-risk area or changes scope, show it to the user first.
4. Build. Follow the plan; keep changes minimal and inside the project's existing structure.
5. Go back to the advisor (or `brain`) only when the same error has occurred twice, or before declaring done. A plan that has to change goes through step 3 again. Plan lock, repeated error and pre-done review are the whole budget for the top-tier model.
6. Check and verify. Run the checks for the changed area (see Defaults). Have `opus-checker` judge any build larger than a few lines against the plan. Where the deliverable shows data, have `sonnet-checker` compare it against fresh source calls. Use `reviewer` for high-risk changes.
7. Deliver in the structure below, including what was not verified.

## Boundaries

- Sub-agents never run `git commit`, `git push` or `git tag`. The main session decides, and only when the user asks or the project's rules allow it.
- External connectors are read-only unless the user says otherwise in the same conversation.
- If the user has set `advisorModel` in their settings and the session has the advisor attached, the advisor handles all three decision points and `brain` is not spawned. `brain` is the fallback for sessions without an advisor.

## Defaults (used when the project has no rules file)

- **Scope:** smallest change that meets the done criteria; follow the existing structure; reuse existing modules, scripts and tests; no unrelated refactors.
- **Verification:** find the project's own checks (README, CI config, Makefile, `package.json`, `pyproject.toml`) and run the ones that cover the changed area. If none exist, run what is available (compile, lint, type-check, a quick manual run). Report each command and its result.
- **High-risk areas** (show the plan to the user before building): configuration or environment semantics, public APIs and schemas, authentication and permissions, data migrations or deletions, CI/release/deploy, and anything hard to reverse or visible outside the repository.
- **Delivery structure:** what changed; why; verification; not verified; risks; how to roll back.
