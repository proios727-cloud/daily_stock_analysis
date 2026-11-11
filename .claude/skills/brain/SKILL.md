---
name: brain
description: Plan-first orchestration that spends the top-tier model only on decisions. The brain agent plans and reviews, the main session or implementer builds, scout searches, opus-checker checks the build, sonnet-checker verifies values. Use when the user types /brain or asks for the brain workflow.
---

# Brain

Run a task with the top-tier model used only at decision points. The rule source is the repository root `AGENTS.md`; this skill follows its model tiering (section 2), workflow (section 5) and verification matrix (section 6).

## Usage

```text
/brain <task and done criteria>
```

## Roles

| Role | Agent | Model | Does |
| --- | --- | --- | --- |
| Decide | advisor, else `brain` | top tier, high effort | Plans, diagnoses a repeated error, reviews before "done". Never edits files. |
| Build | main session or `implementer` | mid tier | Makes the changes and runs the checks. Data pulls that must be copied exactly, such as an option-chain map, also run at mid tier, never the light tier. |
| Search | `scout` | light | Read-only lookup, in parallel when there are several questions. |
| Check | `opus-checker` | upper tier | Judges what was built against the plan and done criteria, right after the build. |
| Verify | `sonnet-checker` | mid tier | Compares delivered values and claims against fresh sources. |
| Review | `reviewer` | main session model | Second look at high-risk changes before merge. |

Models and effort are set in each agent's frontmatter under `.claude/agents/`, not here.

## Steps

1. Restate the task and its done criteria. If it takes only a few tool calls, do it directly and skip the rest: `AGENTS.md` says not to delegate for its own sake.
2. Gather facts. Use `scout` for lookups that span many files; do small lookups yourself.
3. Lock the plan. Consult the advisor if one is attached to the session; otherwise send `brain` the task, the done criteria, what was found and what is ruled out. Do not start building until the plan is back. If the plan hits a high-risk area or changes scope, show it to the user first.
4. Build. Follow the plan; keep changes minimal and inside the directory boundaries in `AGENTS.md` section 1.
5. Go back to the advisor (or `brain`) only when the same error has occurred twice, or before declaring done. A plan that has to change goes through step 3 again. Plan lock, repeated error and pre-done review are the whole budget for the top-tier model.
6. Check and verify. Run the checks for the changed area from `AGENTS.md` section 6. Have `opus-checker` judge any build larger than a few lines against the plan. Where the deliverable shows data, have `sonnet-checker` compare it against fresh source calls. Use `reviewer` for high-risk changes.
7. Deliver in the structure from `AGENTS.md` section 9, including what was not verified.

## Boundaries

- Sub-agents do not run `git commit`, `git push` or `git tag`; the main session decides, under the rules in `AGENTS.md` section 1.
- External connectors are read-only unless the user says otherwise in the same conversation.
- If `advisorModel` is set in `.claude/settings.json` and the session has the advisor attached, the advisor handles all three decision points and `brain` is not spawned. `brain` is the fallback for sessions without an advisor, as `AGENTS.md` section 2 states.
