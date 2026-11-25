# Options premium backtest (`src/options/`)

Backtests buying **one call or put** on an underlying signal and selling it back for premium gains. US tickers only. Research tool: it places no orders.

## Run

```bash
python -m src.options --symbols SPY,QQQ,NVDA --start 2024-01-01 --end 2025-06-30 \
  --signal ma_golden_cross \
  --exit-params '{"stop_loss_pct":0.35,"trail_pct":0.25,"lock_trigger_pct":0.5,"lock_floor_pct":0.3}' \
  --selection-params '{"target_dte":30,"otm_pct":0.02}'
```

Add `--picks picks.json` (from `python -m src.options.pick`) to trade the picker's ranked tickers with its DTE window, liquidity floors and delta band; see [options-picker.md](options-picker.md). Add `--json` for every trade plus the summary. Output is a per-ticker table: trades, win rate, expectancy (mean net premium return per trade, and $ per contract), profit factor, max drawdown ($, one contract, cumulative), average days held, and the count of each exit reason.

From code (also the hook for walk-forward harnesses):

```python
from src.options import build_runner
run = build_runner("SPY", provider, bars, {"signal": "bull_trend"})
trades = run({"trail_pct": 0.2}, start, end)   # list[OptionTrade]; each has entry_date, exit_date, return_pct
```

## Data

| Source | Use | Setup |
| --- | --- | --- |
| ThetaData v3 EOD (`/option/history/eod`) | primary: chain on entry day + contract path | Theta Terminal running locally; `THETADATA_BASE_URL` (default `http://127.0.0.1:25503/v3`) |
| Alpha Vantage `HISTORICAL_OPTIONS` | fallback, one call per symbol per day (slow on free keys) | `ALPHAVANTAGE_API_KEY` |

Underlying daily bars come from the existing `DataFetcherManager`. A provider failure or empty result falls through to the next one; a failing ticker is skipped, not fatal.

## Entry

Signal on day *t* close, entry at day *t+1* closing **ask** (no look-ahead). Contract: expiry closest to `target_dte` inside `[min_dte, max_dte]`, strike closest to spot × (1 ± `otm_pct`) or to `target_delta` (provider deltas when present, as from Alpha Vantage; otherwise Black-Scholes delta from the closing mid, as for ThetaData EOD). Liquidity filter: `min_bid`, `max_spread_pct`. One open position per ticker; signals while in a trade are skipped.

Mechanical triggers (the YAML strategies are LLM instructions, so these are readings of their core rule):

| Strategy | Call when | Put when |
| --- | --- | --- |
| `ma_golden_cross` | MA5 crosses above MA10, volume > 1.2× 5-day avg, bias < 5% | mirror cross down |
| `volume_breakout` | close > prior 20-day high, volume > 2× 5-day avg | close < prior 20-day low |
| `bull_trend` | first day MA5 ≥ MA10 ≥ MA20 with MA20 rising | mirror |

## Exits (checked on each closing mid, filled at closing bid)

The active stop is the **highest** of:

- **Hard stop**: entry × (1 − `stop_loss_pct`).
- **Trailing stop**: peak × (1 − `trail_pct`), armed once peak gain ≥ `trail_activation_pct`.
- **Profit lock**: once peak gain ≥ `lock_trigger_pct`, a floor at entry × (1 + `lock_floor_pct`). Example with defaults: after +50%, the trade can never close below about +30% at the mark.

Since the peak only rises, the stop only ratchets up. Also: optional `take_profit_pct`, `exit_dte` (close when ≤ N calendar days to expiry), `max_hold_days`, and settlement at intrinsic if data runs to expiry. Commission `commission_per_contract` per side.

| Param | Default |
| --- | --- |
| `stop_loss_pct` | 0.35 |
| `trail_pct` | 0.25 |
| `trail_activation_pct` | 0.20 |
| `lock_trigger_pct` / `lock_floor_pct` | 0.50 / 0.30 |
| `exit_dte` / `max_hold_days` | 5 / 30 |
| `target_dte` (`min`–`max`) | 30 (21–45) |
| `otm_pct`, `min_bid`, `max_spread_pct` | 0.02, 0.20, 0.10 |

Defaults are starting guesses, not fitted values.

## Known limits

- EOD only: intraday stop touches are invisible, and a gap fills at the bid, so a loss can exceed `stop_loss_pct` and a profit-lock exit can land below the floor.
- Fills at the closing NBBO; no extra slippage, no partial fills, one contract.
- No early assignment or dividend handling.
- Results are in-sample; use a walk-forward harness before trusting any parameter set.
