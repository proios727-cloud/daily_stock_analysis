# -*- coding: utf-8 -*-
"""CLI: python -m src.options --symbols SPY,QQQ --start 2024-01-01 --end 2025-06-30"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .backtest import run_backtest
from .models import ExitParams, SelectionParams
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
    ap.add_argument("--symbols", required=True, help="comma separated US tickers")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--signal", default="ma_golden_cross")
    ap.add_argument("--exit-params", default="{}", help='JSON, e.g. {"trail_pct":0.2}')
    ap.add_argument("--selection-params", default="{}", help='JSON, e.g. {"target_dte":30}')
    ap.add_argument("--json", action="store_true", help="print trades and summary as JSON")
    args = ap.parse_args(argv)

    exits = ExitParams.from_dict(json.loads(args.exit_params))
    sel = SelectionParams.from_dict(json.loads(args.selection_params))
    provider = default_provider()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    # 60 extra days of bars so moving averages are warm at `start`.
    warm = date.fromordinal(start.toordinal() - 60).isoformat()

    trades = []
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
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
