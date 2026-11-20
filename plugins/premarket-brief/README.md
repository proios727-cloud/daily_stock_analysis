# premarket-brief

A read-only pre-market brief for a neutral watchlist. One draft per run, with sources, timestamps and a verified / not-verified split. It never places orders and never reads holdings.

## Install

```text
/plugin marketplace add <path-or-repo-containing-this-marketplace>
/plugin install premarket-brief@brain-desk
```

## Use

```text
/premarket-brief:premarket-brief
/premarket-brief:premarket-brief NVDA, AMD, SMH
```

It uses whatever quote and news tools the session has and asks before any billed call. `RUNBOOK-dev-chores.md` maps repo chores to the skills that already handle them.
