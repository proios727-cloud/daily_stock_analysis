# -*- coding: utf-8 -*-
"""CLI: rank liquid US option underlyings and pick 0.40-0.60 delta, 2-6 week contracts.

Read-only. Writes a ranked JSON (schema ``options_picker.v1``) and a flat CSV
for the premium backtester. See docs/options-picker.md.

  python -m src.options.pick --date 2026-11-23
  python -m src.options.pick --date 2026-11-23 --snapshot-dir ./snapshots
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

from .picker import (
    DEFAULT_UNIVERSE, PickerConfig, flat_contract_rows, rank_universe, realized_vol_series, to_payload,
)
from .providers import ThetaSnapshotProvider, _to_date, default_provider

logger = logging.getLogger(__name__)


def _snapshot_closes(directory: str, symbol: str, start: date, end: date) -> List[Tuple[date, float]]:
    """Closes from saved ThetaData ``stock/history/eod`` JSON (``<SYMBOL>_stock.json``)."""
    path = Path(directory) / f"{symbol.upper()}_stock.json"
    rows = json.loads(path.read_text(encoding="utf-8")).get("response", [])
    out = []
    for r in rows:
        stamp = r.get("created") or r.get("last_trade")
        if stamp and r.get("close"):
            d = _to_date(stamp)
            if start <= d <= end:
                out.append((d, float(r["close"])))
    return sorted(out)


def _live_closes(symbol: str, start: date, end: date) -> List[Tuple[date, float]]:
    from data_provider import DataFetcherManager

    df, _ = DataFetcherManager().get_daily_data(symbol, start_date=start.isoformat(), end_date=end.isoformat())
    if df is None or df.empty:
        return []
    return [(_to_date(d), float(c)) for d, c in zip(df["date"], df["close"])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only options chain picker")
    ap.add_argument("--symbols", help="comma list; default OPTIONS_PICKER_UNIVERSE or the built-in liquid list")
    ap.add_argument("--date", help="quote date YYYY-MM-DD (default today)")
    ap.add_argument("--snapshot-dir", help="replay saved ThetaData JSON (<SYM>_options.json, <SYM>_stock.json)")
    ap.add_argument("--delta-min", type=float, default=0.40)
    ap.add_argument("--delta-max", type=float, default=0.60)
    ap.add_argument("--dte-min", type=int, default=14)
    ap.add_argument("--dte-max", type=int, default=42)
    ap.add_argument("--per-ticker", type=int, default=3)
    ap.add_argument("--rate", type=float, default=float(os.getenv("OPTIONS_PICKER_RISK_FREE_RATE", "0.04")))
    ap.add_argument("--out", default="options_picks.json", help="JSON path; a .csv is written beside it")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    raw = args.symbols or os.getenv("OPTIONS_PICKER_UNIVERSE", "")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()] or list(DEFAULT_UNIVERSE)
    as_of = date.fromisoformat(args.date) if args.date else date.today()
    cfg = PickerConfig(delta_min=args.delta_min, delta_max=args.delta_max, dte_min=args.dte_min,
                       dte_max=args.dte_max, target_dte=(args.dte_min + args.dte_max) // 2,
                       contracts_per_ticker=args.per_ticker, risk_free_rate=args.rate)

    provider = ThetaSnapshotProvider(args.snapshot_dir) if args.snapshot_dir else default_provider()
    start = as_of - timedelta(days=365)
    chains: Dict[str, list] = {}
    spots: Dict[str, float] = {}
    hv: Dict[str, List[float]] = {}
    errors: List[str] = []
    for sym in symbols:  # one ticker failing must not stop the batch
        try:
            chains[sym] = [q for right in cfg.rights for q in provider.chain(sym, as_of, right, cfg.dte_max + 1)]
            closes = (_snapshot_closes(args.snapshot_dir, sym, start, as_of) if args.snapshot_dir
                      else _live_closes(sym, start, as_of))
            if closes:
                spots[sym] = closes[-1][1]
                hv[sym] = realized_vol_series([c for _, c in closes])
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            logger.warning("skip %s: %s", sym, exc)

    ranks = rank_universe(chains, spots, as_of, cfg, vol_history=hv, vol_history_kind="hv_proxy")
    payload = to_payload(ranks, as_of, cfg, {
        "chain": provider.name, "underlying": "snapshot" if args.snapshot_dir else "DataFetcherManager",
        "greeks": "Black-Scholes on mid when the source has none", "iv_rank": "hv_proxy",
    })
    payload["errors"] = errors

    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = flat_contract_rows(ranks, as_of)
    if rows:
        with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    print(f"{'#':>2} {'SYM':6} {'score':>6} {'spread':>7} {'vol':>8} {'IVrk':>5}  top contract")
    for r in ranks:
        top = r.contracts[0] if r.contracts else None
        ivr = f"{r.iv_rank:.2f}" if r.iv_rank is not None else "  -"
        spr = f"{r.median_rel_spread:.3f}" if r.median_rel_spread is not None else "   -"
        desc = f"{top.contract_id} d={top.delta:+.2f} dte={top.dte} mid={top.mid}" if top else "; ".join(r.notes)
        print(f"{r.rank:>2} {r.symbol:6} {r.score:6.3f} {spr:>7} {r.total_volume:8d} {ivr:>5}  {desc}")
    print(f"\nWrote {out} ({len(rows)} contracts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
