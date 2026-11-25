# -*- coding: utf-8 -*-
"""Overfit checks for options premium rule sets: walk-forward and out-of-sample.

The harness is backtester-agnostic. It only needs a callable::

    run(params: dict, start: date, end: date) -> list[trade]

where each trade is a mapping (or object) with at least ``entry_date``,
``exit_date`` and ``return_pct`` (premium return as a fraction, 0.42 = +42%).

Paper research only. Nothing here places orders.

Verdict thresholds below are first-version defaults (marked GUESS) and should
be tuned once real backtest output is available.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

Trade = Any
RunFn = Callable[[Dict[str, Any], date, date], List[Trade]]

# GUESS: first-version acceptance thresholds.
MIN_OOS_TRADES = 30
MIN_EFFICIENCY = 0.5          # OOS score / in-sample score
MIN_PROFITABLE_FOLD_SHARE = 0.6
MIN_NEIGHBOR_SHARE = 0.6      # share of parameter neighbours that stay profitable


def _get(trade: Trade, key: str) -> Any:
    if isinstance(trade, Mapping):
        return trade.get(key)
    return getattr(trade, key, None)


@dataclass
class TradeStats:
    trades: int
    win_rate: float
    avg_return: float
    expectancy: float
    profit_factor: float
    total_return: float
    max_drawdown: float

    @property
    def score(self) -> float:
        """Ranking score: mean return per trade (premium expectancy)."""
        return self.expectancy


def compute_stats(trades: Sequence[Trade]) -> TradeStats:
    rets = [float(_get(t, "return_pct") or 0.0) for t in trades]
    n = len(rets)
    if n == 0:
        return TradeStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    # Equity path with equal fixed-dollar sizing per trade (sum of returns).
    equity, peak, mdd = 0.0, 0.0, 0.0
    for r in rets:
        equity += r
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    mean = sum(rets) / n
    return TradeStats(
        trades=n,
        win_rate=len(wins) / n,
        avg_return=mean,
        expectancy=mean,
        profit_factor=pf,
        total_return=sum(rets),
        max_drawdown=mdd,
    )


def expand_grid(grid: Mapping[str, Iterable[Any]]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    values = [list(grid[k]) for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


@dataclass
class Fold:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    best_params: Dict[str, Any]
    train_stats: TradeStats
    test_stats: TradeStats


@dataclass
class ValidationReport:
    folds: List[Fold]
    oos_stats: TradeStats
    is_stats: TradeStats
    efficiency: float
    profitable_fold_share: float
    neighbor_share: Optional[float]
    holdout_stats: Optional[TradeStats]
    final_params: Dict[str, Any]
    verdict: str
    reasons: List[str] = field(default_factory=list)

    def to_markdown(self, title: str = "Options rule validation") -> str:
        o = self.oos_stats
        lines = [
            f"## {title}",
            f"**Verdict: {self.verdict}** (paper only)",
            "",
            f"- Final params: `{self.final_params}`",
            f"- Walk-forward folds: {len(self.folds)}, profitable OOS folds: {self.profitable_fold_share:.0%}",
            f"- OOS trades: {o.trades}, win rate {o.win_rate:.0%}, expectancy {o.expectancy:+.1%}/trade, "
            f"PF {o.profit_factor:.2f}, max DD {o.max_drawdown:.1%}",
            f"- Walk-forward efficiency (OOS/IS expectancy): {self.efficiency:.2f}",
        ]
        if self.neighbor_share is not None:
            lines.append(f"- Parameter neighbours still profitable: {self.neighbor_share:.0%}")
        if self.holdout_stats is not None:
            h = self.holdout_stats
            lines.append(
                f"- Final holdout: {h.trades} trades, expectancy {h.expectancy:+.1%}, PF {h.profit_factor:.2f}"
            )
        if self.reasons:
            lines.append("")
            lines.append("Flags:")
            lines.extend(f"- {r}" for r in self.reasons)
        return "\n".join(lines)


def _best(run: RunFn, candidates: List[Dict[str, Any]], start: date, end: date, min_trades: int):
    best_params, best_stats = None, None
    for params in candidates:
        stats = compute_stats(run(dict(params), start, end))
        if stats.trades < min_trades:
            continue
        if best_stats is None or stats.score > best_stats.score:
            best_params, best_stats = params, stats
    return best_params, best_stats


def _neighbors(params: Dict[str, Any], grid: Mapping[str, Sequence[Any]]) -> List[Dict[str, Any]]:
    """One-step neighbours along each grid axis."""
    out = []
    for key, values in grid.items():
        values = list(values)
        if params.get(key) not in values:
            continue
        i = values.index(params[key])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                out.append({**params, key: values[j]})
    return out


def walk_forward(
    run: RunFn,
    grid: Mapping[str, Sequence[Any]],
    start: date,
    end: date,
    *,
    train_days: int = 365,
    test_days: int = 90,
    holdout_days: int = 0,
    min_train_trades: int = 10,
) -> ValidationReport:
    """Rolling walk-forward optimisation with an optional untouched final holdout.

    For each fold the best params on the train window are evaluated on the next
    test window. OOS trades from all folds are pooled. If ``holdout_days`` > 0
    the last part of the range is never used for selection; the params chosen on
    everything before it are scored once on the holdout.
    """
    candidates = expand_grid(grid)
    if not candidates:
        raise ValueError("grid is empty")

    wf_end = end - timedelta(days=holdout_days) if holdout_days else end
    folds: List[Fold] = []
    oos_trades: List[Trade] = []
    is_scores: List[float] = []

    cursor = start
    while cursor + timedelta(days=train_days + test_days) <= wf_end + timedelta(days=1):
        tr_s, tr_e = cursor, cursor + timedelta(days=train_days - 1)
        te_s, te_e = tr_e + timedelta(days=1), tr_e + timedelta(days=test_days)
        params, tr_stats = _best(run, candidates, tr_s, tr_e, min_train_trades)
        if params is not None:
            test_trades = run(dict(params), te_s, te_e)
            te_stats = compute_stats(test_trades)
            folds.append(Fold(tr_s, tr_e, te_s, te_e, params, tr_stats, te_stats))
            oos_trades.extend(test_trades)
            is_scores.append(tr_stats.score)
        cursor += timedelta(days=test_days)

    oos = compute_stats(oos_trades)
    is_mean = sum(is_scores) / len(is_scores) if is_scores else 0.0
    is_stats = TradeStats(sum(f.train_stats.trades for f in folds), 0.0, is_mean, is_mean, 0.0, 0.0, 0.0)
    efficiency = (oos.score / is_mean) if is_mean > 0 else 0.0
    prof_share = (
        sum(1 for f in folds if f.test_stats.trades and f.test_stats.expectancy > 0) / len(folds)
        if folds else 0.0
    )

    # Final params: best on the whole pre-holdout window.
    final_params, _ = _best(run, candidates, start, wf_end, min_train_trades)
    final_params = final_params or {}

    neighbor_share = None
    if final_params:
        nbrs = _neighbors(final_params, grid)
        if nbrs:
            ok = sum(1 for p in nbrs if compute_stats(run(dict(p), start, wf_end)).expectancy > 0)
            neighbor_share = ok / len(nbrs)

    holdout = None
    if holdout_days and final_params:
        holdout = compute_stats(run(dict(final_params), wf_end + timedelta(days=1), end))

    verdict, reasons = _verdict(folds, oos, efficiency, prof_share, neighbor_share, holdout)
    return ValidationReport(
        folds=folds,
        oos_stats=oos,
        is_stats=is_stats,
        efficiency=efficiency,
        profitable_fold_share=prof_share,
        neighbor_share=neighbor_share,
        holdout_stats=holdout,
        final_params=final_params,
        verdict=verdict,
        reasons=reasons,
    )


def _verdict(folds, oos, efficiency, prof_share, neighbor_share, holdout):
    reasons: List[str] = []
    if not folds:
        return "INSUFFICIENT_DATA", ["No walk-forward fold had enough training trades."]
    if oos.trades < MIN_OOS_TRADES:
        reasons.append(f"Only {oos.trades} OOS trades (< {MIN_OOS_TRADES}); result is not statistically meaningful.")
    if oos.expectancy <= 0:
        reasons.append("Pooled OOS expectancy is not positive.")
    if efficiency < MIN_EFFICIENCY:
        reasons.append(f"Walk-forward efficiency {efficiency:.2f} < {MIN_EFFICIENCY}: in-sample edge mostly disappears OOS.")
    if prof_share < MIN_PROFITABLE_FOLD_SHARE:
        reasons.append(f"Only {prof_share:.0%} of OOS folds profitable (< {MIN_PROFITABLE_FOLD_SHARE:.0%}).")
    if neighbor_share is not None and neighbor_share < MIN_NEIGHBOR_SHARE:
        reasons.append(f"Edge is fragile: only {neighbor_share:.0%} of neighbouring params profitable (curve-fit risk).")
    if holdout is not None and holdout.trades and holdout.expectancy <= 0:
        reasons.append("Final untouched holdout is not profitable.")

    if oos.expectancy <= 0 or (holdout is not None and holdout.trades and holdout.expectancy <= 0):
        return "OVERFIT", reasons
    if reasons:
        return "FRAGILE", reasons
    return "ROBUST", reasons
