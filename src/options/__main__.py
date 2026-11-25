# -*- coding: utf-8 -*-
"""CLI: python -m src.options --symbols SPY,QQQ --start 2024-01-01 --end 2025-06-30"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .backtest import run_backtest
from .models import ExitParams, SelectionParams
from .picker import selection_from_picks
from .providers import default_provider
from .report import summarize_trades, to_markdown


def _load_bars(symbol: str, start: str, end: str):
    from data_provider import DataFetcherManager

    df, source = DataFetcherManager().get_daily_data(symbol, start_date=start, end_date=end)
    if df is None or df.empty:
        raise RuntimeError(f"No underlying bars for {symbol}")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Long single-leg option premium backtest")
    ap.add_argument("--symbols", help="comma separated US tickers (required unless --picks is given)")
    ap.add_argument("--picks", help="options_picker.v1 JSON from `python -m src.options.pick`: trade its ranked "
                                    "tickers with its DTE window, liquidity floors and delta band")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--signal", default="ma_golden_cross")
    ap.add_argument("--exit-params", default="{}", help='JSON, e.g. {"trail_pct":0.2}')
    ap.add_argument("--selection-params", default="{}", help='JSON, e.g. {"target_dte":30}')
    ap.add_argument("--json", action="store_true", help="print trades and summary as JSON")
    args = ap.parse_args(argv)

    exits = ExitParams.from_dict(json.loads(args.exit_params))
    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    sel_params = json.loads(args.selection_params)
    if args.picks:
        with open(args.picks, encoding="utf-8") as fh:
            pick_symbols, pick_sel = selection_from_picks(json.load(fh))
        symbols = [s for s in pick_symbols if s in symbols] if symbols else pick_symbols
        sel_params = {**pick_sel, **sel_params}  # explicit --selection-params still win
    if not symbols:
        ap.error("no symbols: pass --symbols or a --picks file with ranked tickers")
    sel = SelectionParams.from_dict(sel_params)
    provider = default_provider()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    # 60 extra days of bars so moving averages are warm at `start`.
    warm = date.fromordinal(start.toordinal() - 60).isoformat()

    trades = []
    for sym in symbols:
        try:
            bars = _load_bars(sym, warm, args.end)
            trades += run_backtest(sym, bars, provider, args.signal, sel, exits, start, end)
        except Exception as exc:  # one ticker failing should not stop the batch
            print(f"[skip] {sym}: {exc}", file=sys.stderr)

    summary = summarize_trades(trades)
    if args.json:
        print(json.dumps({"trades": [t.to_dict() for t in trades], "summary": summary}, indent=2, default=str))
    else:
        print(to_markdown(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
