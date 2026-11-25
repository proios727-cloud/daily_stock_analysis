# -*- coding: utf-8 -*-
"""Exit rule evaluation on an end-of-day premium path (pure logic)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .models import ExitParams, OptionQuote


@dataclass(frozen=True)
class ExitDecision:
    day: date
    price: float
    reason: str
    peak_mark: float


def stop_level(entry: float, peak: float, params: ExitParams) -> tuple[float, str]:
    """Return the active stop price and which rule set it.

    The stop is the highest of: hard stop, trailing stop (once armed) and the
    profit-lock floor (once peak gain reached lock_trigger_pct). Because peak
    only rises, the stop only ratchets up.
    """
    candidates = [(entry * (1 - params.stop_loss_pct), "hard_stop")]
    peak_gain = peak / entry - 1
    if peak_gain >= params.trail_activation_pct:
        candidates.append((peak * (1 - params.trail_pct), "trailing_stop"))
    if peak_gain >= params.lock_trigger_pct:
        candidates.append((entry * (1 + params.lock_floor_pct), "profit_lock"))
    return max(candidates, key=lambda c: c[0])


def evaluate_exit(
    entry_price: float,
    path: Sequence[OptionQuote],
    expiration: date,
    params: ExitParams,
    entry_date: date,
    underlying_close_at_expiry: Optional[float] = None,
) -> ExitDecision:
    """Walk quotes after entry and return the first exit.

    EOD model (guess, see docs): rules are checked on the closing mid, and
    the fill is that day's closing bid. A gap through the stop fills at the
    bid, not at the stop price, so losses can exceed stop_loss_pct.
    """
    peak = entry_price
    last: Optional[OptionQuote] = None
    for quote in path:
        if quote.day <= entry_date:
            continue
        last = quote
        mark = quote.mid
        peak = max(peak, mark)
        dte = (expiration - quote.day).days
        held = (quote.day - entry_date).days

        if params.take_profit_pct is not None and mark >= entry_price * (1 + params.take_profit_pct):
            return ExitDecision(quote.day, quote.bid, "take_profit", peak)
        level, rule = stop_level(entry_price, peak, params)
        if mark <= level:
            return ExitDecision(quote.day, quote.bid, rule, peak)
        if dte <= params.exit_dte:
            return ExitDecision(quote.day, quote.bid, "dte_exit", peak)
        if held >= params.max_hold_days:
            return ExitDecision(quote.day, quote.bid, "max_hold", peak)

    # Path ran out before any rule fired: settle at expiry or last quote.
    if underlying_close_at_expiry is not None and (last is None or last.day < expiration):
        contract = path[0].contract if path else None
        if contract is not None:
            intrinsic = (
                max(0.0, underlying_close_at_expiry - contract.strike)
                if contract.right == "call"
                else max(0.0, contract.strike - underlying_close_at_expiry)
            )
            return ExitDecision(expiration, intrinsic, "expiry", peak)
    if last is not None:
        return ExitDecision(last.day, last.bid, "end_of_data", peak)
    raise ValueError("No quotes after entry; cannot evaluate exit")
