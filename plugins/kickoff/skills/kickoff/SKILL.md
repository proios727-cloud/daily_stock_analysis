---
name: kickoff
description: Brainstorm-before-coding. Turns a project idea into a reviewable KICKOFF.md (goal, non-goals, invariants, options, task breakdown, verification plan, honest limits, paste-ready handoff prompts) using one top-tier decision, then stops for approval. Use when the user types /kickoff, wants to start a new project, or wants to brainstorm before building.
argument-hint: "<idea> [--lite]"
---

# Kickoff

Produce a brief, not code. The expensive reasoning happens once, up front; building and checking are cheap and separate. Stop when `KICKOFF.md` is written.

## Project rules first

If the repository has `AGENTS.md`, `CLAUDE.md` or an equivalent rules file, read it before step 1. Where it conflicts with this skill, the repository's rules win.

## Usage

```text
/kickoff:kickoff <idea>          full: intake, recon, options, one top-tier decision, KICKOFF.md
/kickoff:kickoff <idea> --lite   brainstorm only: no top-tier call, options and open questions
```

(If your client resolves bare names, `/kickoff <idea>` also works.)

## Steps

1. **Intake.** Ask at most 5 questions, only decisions the user owns: who it is for, whether a real data sample exists, hard constraints (time, budget, platform), what "done" looks like, anything that must never happen. State a default for everything else instead of asking. If the idea is already specific, ask fewer.
2. **Recon.** Read-only, light tier, in parallel when there are several questions: existing code and prior art in the repository; web prior art only if an agent with web access is available. Facts only, no design. If a fact cannot be found, record it as an open item.
3. **Options.** Give 2-3 approaches. For each: cost, main risk, and what it makes impossible. Always include "do not build: use or buy what exists" and say when it wins.
4. **Plan lock (the single top-tier call).** Use the advisor if one is attached; otherwise `brain:brain` if the brain plugin is installed; otherwise the main session with an explicit pass that tries to argue against its own pick. Send the idea, answers, recon facts and options. Get back: the chosen approach, numbered invariants, non-goals, kill criteria, the top 3 risks. Skip this step in `--lite` mode and say so in the output.
5. **Write `KICKOFF.md`** from `templates/KICKOFF.md` in the project root (or the folder the user names). Fill every section; write "none" rather than leaving one blank.
6. **Stop.** Show the user the brief and the open questions. Do not write code, create repos, publish, spend money or contact anyone until the user approves.

## Rules

- **Missing real-world data is a named open item.** If a sample, account, API or file the design depends on was not seen, list it under Honest limits. Never assume its format.
- **The builder never verifies its own work.** In the Verification plan, every task is checked by a different agent that receives the spec and the invariants, not the builder's reasoning.
- **Verified and unverified are separate lists.** No task is "ready" without a stated check and its evidence.
- **No outward actions without explicit approval:** publishing, deploying, sending messages, spending, trading, deleting.
- **Tiers by alias only:** light, mid, top. Do not write model version numbers into the brief; they go stale.
- **Small ideas stay small.** If the whole thing is a few tool calls, say so, skip the ceremony, and do it or hand it back.

## Cowork and agent handoff

The last section of `KICKOFF.md` rewrites each task as a self-contained prompt: goal, inputs (with paths), the exact output file, the acceptance test, what not to touch, and the tier to run it on. A fresh session with no memory of this conversation must be able to execute it. This works the same in Cowork, a new Claude Code session, or a workflow script.

## Delivery

Report: where `KICKOFF.md` is, the chosen approach in two lines, the open questions the user must answer, and what was not verified (for example, that handoff prompts were not run in Cowork).
