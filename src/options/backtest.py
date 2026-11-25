# -*- coding: utf-8 -*-
"""Signal -> contract -> premium path -> exit, one open position per ticker."""

from __future__ import annotations

import logging
from dataclasses import fields
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional

import pandas as pd

from .exits import evaluate_exit
from .models import CONTRACT_MULTIPLIER, ExitParams, OptionQuote, OptionTrade, SelectionParams
from .picker import PickerConfig, bs_delta, implied_vol
from .providers import OptionDataProvider
from .signals import get_signal

logger = logging.getLogger(__name__)

_EXIT_KEYS = {f.name for f in fields(ExitParams)}
_SELECT_KEYS = {f.name for f in fields(SelectionParams)}


def _mid_delta(q: OptionQuote, day: date, spot: float) -> Optional[float]:
    """Black-Scholes delta from the mid, for EOD sources without greeks (same model as the picker)."""
    t = (q.contract.expiration - day).days / 365.0
    rate = PickerConfig().risk_free_rate
    iv = implied_vol(q.mid, spot, q.contract.strike, t, rate, q.contract.right)
    return bs_delta(spot, q.contract.strike, t, rate, iv, q.contract.right) if iv is not None else None


def select_contract(
    chain: List[OptionQuote], day: date, spot: float, right: str, sel: SelectionParams
) -> Optional[OptionQuote]:
    """Pick the liquid contract closest to target DTE, then to target delta / moneyness."""
    pool = [
        q for q in chain
        if q.contract.right == right
        and q.day == day
        and sel.min_dte <= (q.contract.expiration - day).days <= sel.max_dte
        and q.bid >= sel.min_bid
        and q.spread_pct <= sel.max_spread_pct
    ]
    if not pool:
        return None
    best_exp = min({q.contract.expiration for q in pool}, key=lambda e: abs((e - day).days - sel.target_dte))
    pool = [q for q in pool if q.contract.expiration == best_exp]
    if sel.target_delta is not None:
        deltas = {id(q): q.delta if q.delta is not None else _mid_delta(q, day, spot) for q in pool}
        if all(d is not None for d in deltas.values()):
            return min(pool, key=lambda q: abs(abs(deltas[id(q)]) - sel.target_delta))
    target = spot * (1 + sel.otm_pct) if right == "call" else spot * (1 - sel.otm_pct)
    return min(pool, key=lambda q: abs(q.contract.strike - target))


def _close_on_or_before(bars: pd.DataFrame, day: date) -> Optional[float]:
    sub = bars[bars["date"] <= day]
    return float(sub["close"].iloc[-1]) if not sub.empty else None


def run_backtest(
    symbol: str,
    bars: pd.DataFrame,
    provider: OptionDataProvider,
    signal: str = "ma_golden_cross",
    selection: Optional[SelectionParams] = None,
    exits: Optional[ExitParams] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> List[OptionTrade]:
    """Backtest one ticker. `bars` needs date, open, high, low, close, volume."""
    sel = selection or SelectionParams()
    ex = exits or ExitParams()
    ex.validate()
    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    days = list(df["date"])
    last_day = days[-1] if days else None

    trades: List[OptionTrade] = []
    busy_until: Optional[date] = None
    for sig_day, right in get_signal(signal)(df):
        if (start and sig_day < start) or (end and sig_day > end):
            continue
        if right == "put" and not sel.allow_puts:
            continue
        if busy_until is not None and sig_day <= busy_until:
            continue
        idx = days.index(sig_day)
        if idx + 1 >= len(days):
            continue
        entry_day = days[idx + 1]  # act on the session after the signal close (no look-ahead)
        spot = float(df["close"].iloc[idx + 1])

        chain = provider.chain(symbol, entry_day, right, sel.max_dte)
        pick = select_contract(chain, entry_day, spot, right, sel)
        if pick is None:
            logger.info("%s %s: no liquid %s contract on %s", symbol, signal, right, entry_day)
            continue
        contract = pick.contract
        path_end = min(contract.expiration, entry_day + timedelta(days=ex.max_hold_days + 7), last_day)
        path = [q for q in provider.history(contract, entry_day, path_end) if q.day > entry_day]
        if not path:
            continue
        expiry_close = _close_on_or_before(df, contract.expiration) if last_day >= contract.expiration else None
        decision = evaluate_exit(pick.ask, path, contract.expiration, ex, entry_day, expiry_close)

        fees = 2 * ex.commission_per_contract
        cost = pick.ask * CONTRACT_MULTIPLIER + ex.commission_per_contract
        pnl = (decision.price - pick.ask) * CONTRACT_MULTIPLIER - fees
        trades.append(OptionTrade(
            symbol=symbol, signal=signal, signal_date=sig_day, contract=contract,
            entry_date=entry_day, entry_price=pick.ask, exit_date=decision.day,
            exit_price=decision.price, exit_reason=decision.reason, peak_mark=decision.peak_mark,
            return_pct=pnl / cost, pnl_usd=pnl, days_held=(decision.day - entry_day).days,
            extra={"spot_at_entry": spot, "entry_spread_pct": pick.spread_pct},
        ))
        busy_until = decision.day
    return trades


def build_runner(
    symbol: str,
    provider: OptionDataProvider,
    bars: pd.DataFrame,
    base_params: Optional[Mapping[str, Any]] = None,
) -> Callable[[Dict[str, Any], date, date], List[OptionTrade]]:
    """Return run(params, start, end) for walk-forward / optimisation harnesses.

    params may hold any ExitParams or SelectionParams field plus "signal".
    """
    base = dict(base_params or {})

    def run(params: Dict[str, Any], start: date, end: date) -> List[OptionTrade]:
        merged = {**base, **(params or {})}
        signal = merged.pop("signal", "ma_golden_cross")
        unknown = set(merged) - _EXIT_KEYS - _SELECT_KEYS
        if unknown:
            raise ValueError(f"Unknown params: {sorted(unknown)}")
        return run_backtest(
            symbol, bars, provider, signal,
            SelectionParams.from_dict({k: v for k, v in merged.items() if k in _SELECT_KEYS}),
            ExitParams.from_dict({k: v for k, v in merged.items() if k in _EXIT_KEYS}),
            start, end,
        )

    return run
