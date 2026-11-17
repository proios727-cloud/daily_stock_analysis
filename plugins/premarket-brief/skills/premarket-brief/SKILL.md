---
name: premarket-brief
description: Read-only pre-market brief for a watchlist. For each ticker gives prior close, pre-market or last price, percent change, and notable news or earnings dates, each with source and timestamp, plus a verified / not-verified split. Use when the user types /premarket-brief or asks for a morning or pre-market brief. Analysis only; never places orders and never reads holdings.
argument-hint: "[tickers, comma separated]"
---

# Pre-market brief

Produce one draft brief for a watchlist. The same prompt runs in Cowork, Claude Code or any session with a quote tool.

## Usage

```text
/premarket-brief:premarket-brief                 default neutral list
/premarket-brief:premarket-brief NVDA, AMD, SMH  your own list, pasted inline
```

Default list (neutral, not tied to anyone's book): SPY, QQQ, IWM, DIA, AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA.

## Invariants

1. Read-only analysis; no orders, no order-shaped output.
2. Inputs are pasted inline or attached, never local paths; no dependence on any one repo's rules file.
3. Runs in one session without sub-agents; delegation is optional.
4. No credit-billed tool call without a per-run ask.
5. No account, position or size data in outbound requests. Tickers drawn from holdings are personal data and stay out of outbound requests unless approved per run. Never look up the user's holdings to build the list.
6. Outputs are drafts only.

## Steps

1. **Clock.** State the current time in US Eastern and the session state: pre-open (04:00-09:30 ET on a trading day), open, after hours, or closed (weekend or holiday). If the tools cannot tell a holiday, say so.
2. **Sources.** List the quote and news tools this session has. Mark each free or billed; if unknown, treat it as billed. Ask before any billed call. If no quote tool exists, stop and say what is missing; do not answer from memory.
3. **Per ticker.** Pull: prior regular-session close; the latest pre-market or extended-hours price if the source has one, otherwise the last trade; percent change against the prior close; the next earnings date if within 7 days; one line of notable news if a news tool is available. Record the source and the data timestamp for each number.
4. **Index read.** Two or three lines on SPY, QQQ, IWM and DIA together: direction, size of move, breadth across the four.
5. **What to watch.** Up to five items, each stated as a condition and what it would indicate ("if X holds above Y, it indicates Z"). No buy, sell, hold, size or target wording.
6. **Self-check.** If sub-agents are available, give a separate checker the table and have it re-pull every number. Otherwise re-pull three tickers yourself and report any drift. Numbers not re-pulled go under Not verified.

## Output format

```markdown
# Pre-market brief: <date>, as of <HH:MM ET> (<session state>)
Sources: <tool> (free|billed|unknown) ...

| Ticker | Prior close | Latest | Change | Latest as of | Earnings <=7d | Note |
| --- | --- | --- | --- | --- | --- | --- |

## Index read
## What to watch
## Verified
## Not verified
Draft, analysis only. Not investment advice. Prices can be stale or wrong; check your own source before acting.
```

## Rules

- A missing or stale number is shown as "missing" or "stale (as of ...)". Never estimate or fill from memory.
- Percent change is computed from the two prices shown, so the reader can check it.
- If the user supplies a list, use exactly that list. Do not add tickers from any account.
- Do not send, post or schedule the brief anywhere unless the user asks in the same conversation.
