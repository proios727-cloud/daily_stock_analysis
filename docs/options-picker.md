# Options chain picker

Read-only tool that ranks liquid US option underlyings and picks single long
contracts for the options premium backtester. It never places orders.

- Logic: `src/options/picker.py` (pure, no I/O)
- Data: the backtester's providers in `src/options/providers.py` (ThetaData
  first, Alpha Vantage fallback) and `ThetaSnapshotProvider` for saved JSON;
  underlying closes come from `DataFetcherManager`, or from the snapshot
- CLI: `python -m src.options.pick`
- Tests: `tests/test_options_picker.py`
- Backtester: [options-backtest.md](options-backtest.md)

## Run

```bash
# Live, against a local Theta Terminal
python -m src.options.pick --date 2026-11-23

# Replay saved ThetaData JSON (<SYMBOL>_options.json, <SYMBOL>_stock.json)
python -m src.options.pick --date 2026-11-23 --snapshot-dir ./snapshots

# Narrower universe, other band
python -m src.options.pick --symbols SPY,QQQ,NVDA --delta-min 0.45 --delta-max 0.55
```

Output: `options_picks.json` (schema `options_picker.v1`) and `options_picks.csv`
beside it, plus a console table.

## How it ranks

1. Per underlying, read the EOD chain out to `dte_max + 1` days and the last
   year of closes.
2. Compute IV and delta from the mid price (Black-Scholes, flat rate) when the
   source has no greeks. ThetaData free tier has none.
3. Keep contracts with 0.40 ≤ |delta| ≤ 0.60, 14 to 42 DTE, bid ≥ 0.10,
   spread ≤ 10% of mid, volume ≥ 10, and open interest ≥ 100 when known.
4. Ticker score is a weighted percentile across the universe: tight median
   spread 0.35, open interest 0.25, volume 0.25, IV rank 0.15 (lower IV rank
   scores higher, since buying premium is cheaper then). Missing metrics are
   dropped and the weights renormalized.
5. Contract score: delta closeness to 0.50 (0.3), DTE closeness to the window
   middle (0.2), spread (0.3), volume plus OI (0.2). Up to 3 per ticker, at
   least one call and one put when both pass.

## Output fields

`tickers[]`: `symbol`, `rank` (0 = not ranked), `score`, `underlying_price`,
`eligible_contracts`, `median_rel_spread`, `total_volume`,
`total_open_interest`, `atm_iv`, `iv_rank`, `iv_rank_source`, `notes`,
`contracts[]`.

`contracts[]`: `contract_id` (OCC style, `SPY261218C00725000`), `symbol`,
`right` (`call`/`put`, same as `OptionContract.right`), `strike`, `expiration`, `dte`, `bid`, `ask`, `mid`,
`rel_spread`, `volume`, `open_interest`, `iv`, `delta`, `greeks_source`
(`source` or `bs_mid`), `score`.

## Known limits (first version)

- IV rank is a proxy: current ATM IV placed in the 1-year range of 21-day
  realized vol (`iv_rank_source: "hv_proxy"`). A true IV rank needs a stored
  ATM IV history; `rank_universe(..., vol_history_kind="iv_history")` accepts
  one when available.
- Open interest needs a paid ThetaData tier or Alpha Vantage; without it the
  OI weight is dropped.
- All weights, floors and the 0.04 rate are first guesses, to be tuned by the
  backtester.
- Delta ignores dividends and uses European pricing on American options.

## Config

See `.env.example`: `OPTIONS_PICKER_UNIVERSE`, `OPTIONS_PICKER_RISK_FREE_RATE`,
plus the backtester's `THETADATA_BASE_URL` and `ALPHAVANTAGE_API_KEY`.
