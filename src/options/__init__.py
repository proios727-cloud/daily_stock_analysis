# -*- coding: utf-8 -*-
"""Single-leg long option premium backtester.

Buys one call or put on an underlying signal, then manages the premium with a
hard stop, a trailing stop and a profit lock floor, using end-of-day NBBO.
See docs/options-backtest.md.
"""

from .models import ExitParams, OptionContract, OptionQuote, OptionTrade, SelectionParams
from .backtest import build_runner, run_backtest
from .report import summarize_trades

__all__ = [
    "ExitParams",
    "OptionContract",
    "OptionQuote",
    "OptionTrade",
    "SelectionParams",
    "build_runner",
    "run_backtest",
    "summarize_trades",
]
