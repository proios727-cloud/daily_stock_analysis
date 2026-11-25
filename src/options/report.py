# -*- coding: utf-8 -*-
"""Per-ticker metrics and a Markdown summary."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List

from .models import OptionTrade


def _metrics(trades: List[OptionTrade]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = -sum(t.pnl_usd for t in trades if t.pnl_usd <= 0)

    # Max drawdown of cumulative 1-lot P&L in dollars, in exit order.
    equity = peak = max_dd = 0.0
    streak = max_streak = 0
    for t in sorted(trades, key=lambda x: x.exit_date):
        equity += t.pnl_usd
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        streak = streak + 1 if t.pnl_usd <= 0 else 0
        max_streak = max(max_streak, streak)

    return {
        "trades": n,
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": sum(rets) / n,  # mean net premium return per trade
        "expectancy_usd": sum(t.pnl_usd for t in trades) / n,
        "total_pnl_usd": sum(t.pnl_usd for t in trades),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown_usd": max_dd,
        "max_consecutive_losses": max_streak,
        "avg_days_held": sum(t.days_held for t in trades) / n,
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
    }


def summarize_trades(trades: Iterable[OptionTrade]) -> Dict[str, Dict[str, Any]]:
    by_symbol: Dict[str, List[OptionTrade]] = defaultdict(list)
    all_trades: List[OptionTrade] = []
    for t in trades:
        by_symbol[t.symbol].append(t)
        all_trades.append(t)
    out = {sym: _metrics(ts) for sym, ts in sorted(by_symbol.items())}
    out["__overall__"] = _metrics(all_trades)
    return out


def to_markdown(summary: Dict[str, Dict[str, Any]]) -> str:
    head = "| Ticker | Trades | Win rate | Expectancy | Exp. $ | Profit factor | Max DD $ | Avg days | Exits |\n"
    head += "|---|---:|---:|---:|---:|---:|---:|---:|---|\n"
    rows = []
    for sym, m in summary.items():
        label = "All" if sym == "__overall__" else sym
        if m["trades"] == 0:
            rows.append(f"| {label} | 0 | - | - | - | - | - | - | - |")
            continue
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        exits = ", ".join(f"{k} {v}" for k, v in sorted(m["exit_reasons"].items()))
        rows.append(
            f"| {label} | {m['trades']} | {m['win_rate']:.0%} | {m['expectancy_pct']:+.1%} | "
            f"{m['expectancy_usd']:+.0f} | {pf} | {m['max_drawdown_usd']:.0f} | {m['avg_days_held']:.1f} | {exits} |"
        )
    return head + "\n".join(rows) + "\n"
