# kickoff

Brainstorm before coding. `/kickoff:kickoff <idea>` turns an idea into a reviewable `KICKOFF.md` with one top-tier decision, then stops for approval. No code is written.

## Install

```text
/plugin marketplace add <path-or-repo-containing-this-marketplace>
/plugin install kickoff@brain-desk
```

## Use

```text
/kickoff:kickoff a tool that ...          full brief
/kickoff:kickoff a tool that ... --lite   brainstorm only, no top-tier call
```

Optional: install the `brain` plugin from the same marketplace; kickoff uses `brain:brain` for the plan-lock decision when no advisor is attached, and falls back to the main session otherwise.

The brief ends with paste-ready handoff prompts for Cowork or any fresh agent session.

The brief separates agent tasks (paste-ready prompts) from your own tasks (a short checklist), and marks setup-dependent capabilities as `depends` with a check step.
