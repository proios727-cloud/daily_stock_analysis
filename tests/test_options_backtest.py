# -*- coding: utf-8 -*-
"""Offline tests for src/options (exit rules, selection, engine, parsers, report)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from src.options.backtest import build_runner, run_backtest, select_contract
from src.options.exits import evaluate_exit, stop_level
from src.options.models import ExitParams, OptionContract, OptionQuote, SelectionParams
from src.options.providers import FallbackProvider, parse_av_chain, parse_theta_eod
from src.options.report import summarize_trades, to_markdown
from src.options.signals import get_signal, ma_golden_cross

D0 = date(2025, 1, 2)
EXP = date(2025, 2, 21)
C = OptionContract("SPY", EXP, 600.0, "call")


def path(mids, start=D0, spread=0.0):
    """Quotes on consecutive days after `start` with the given mid prices."""
    return [OptionQuote(C, start + timedelta(days=i + 1), m - spread / 2, m + spread / 2) for i, m in enumerate(mids)]


P = ExitParams(stop_loss_pct=0.35, trail_pct=0.25, lock_trigger_pct=0.50, lock_floor_pct=0.30,
               exit_dte=0, max_hold_days=60, trail_activation_pct=0.20)


def test_hard_stop():
    d = evaluate_exit(10.0, path([9.0, 7.0, 6.4]), EXP, P, D0)
    assert d.reason == "hard_stop" and d.price == pytest.approx(6.4)


def test_trailing_stop_from_peak():
    # Peak 13 (+30%, below lock trigger) -> trail stop 9.75
    d = evaluate_exit(10.0, path([12.0, 13.0, 11.0, 9.7]), EXP, P, D0)
    assert d.reason == "trailing_stop" and d.peak_mark == 13.0


def test_profit_lock_floor_beats_trail():
    # Peak 15.2 (+52%): trail stop 11.4, lock floor 13.0 -> lock binds first
    d = evaluate_exit(10.0, path([12.0, 15.2, 14.0, 12.9]), EXP, P, D0)
    assert d.reason == "profit_lock" and d.price == pytest.approx(12.9)


def test_trail_beats_lock_on_big_run():
    # Peak 20: trail 15 > floor 13 -> trailing stop binds
    d = evaluate_exit(10.0, path([14.0, 20.0, 14.9]), EXP, P, D0)
    assert d.reason == "trailing_stop"


def test_stop_never_moves_down():
    lvl1, _ = stop_level(10.0, 16.0, P)
    lvl2, _ = stop_level(10.0, 16.0, P)  # same peak after a pullback
    assert lvl1 == lvl2 >= 13.0


def test_gap_fill_uses_bid_not_stop():
    d = evaluate_exit(10.0, path([10.0, 3.0], spread=0.2), EXP, P, D0)
    assert d.price == pytest.approx(2.9)  # worse than the -35% stop


def test_dte_exit_and_expiry_intrinsic():
    short_exp = D0 + timedelta(days=6)
    params = ExitParams(exit_dte=3, max_hold_days=60)
    q = [OptionQuote(OptionContract("SPY", short_exp, 600, "call"), D0 + timedelta(days=i), 10, 10.2) for i in (1, 2, 3)]
    assert evaluate_exit(10.1, q, short_exp, params, D0).reason == "dte_exit"
    params = ExitParams(exit_dte=0, max_hold_days=60, trail_activation_pct=0.20)
    d = evaluate_exit(10.1, q[:1], short_exp, params, D0, underlying_close_at_expiry=604.0)
    assert d.reason == "expiry" and d.price == pytest.approx(4.0)


def test_param_validation():
    with pytest.raises(ValueError):
        ExitParams(lock_trigger_pct=0.2, lock_floor_pct=0.3).validate()
    with pytest.raises(ValueError):
        ExitParams.from_dict({"bogus": 1})


def test_select_contract_dte_moneyness_liquidity():
    day = D0
    chain = [
        OptionQuote(OptionContract("SPY", day + timedelta(days=30), k, "call"), day, b, b + s)
        for k, b, s in [(600, 5.0, 0.1), (610, 2.0, 0.1), (612, 1.8, 0.9)]
    ] + [OptionQuote(OptionContract("SPY", day + timedelta(days=7), 610, "call"), day, 1.0, 1.05)]
    pick = select_contract(chain, day, 600.0, "call", SelectionParams(otm_pct=0.02))
    assert pick.contract.strike == 610 and (pick.contract.expiration - day).days == 30


def _bars_with_cross(n=60):
    closes = [100 - i * 0.3 for i in range(30)] + [91 + i * 0.8 for i in range(n - 30)]
    rows = []
    for i, c in enumerate(closes):
        rows.append({"date": D0 + timedelta(days=i), "open": c, "high": c + 0.5, "low": c - 0.5,
                     "close": c, "volume": 1_000_000 * (3 if i == 34 else 1)})
    return pd.DataFrame(rows)


def test_signal_registry_matches_yaml():
    assert get_signal("ma_golden_cross") is ma_golden_cross
    with pytest.raises(ValueError):
        get_signal("chan_theory")


class FakeProvider:
    name = "fake"

    def __init__(self, bars):
        self.bars = bars.set_index("date")["close"].to_dict()

    def chain(self, symbol, day, right, max_dte):
        spot = self.bars[day]
        exp = day + timedelta(days=30)
        return [OptionQuote(OptionContract(symbol, exp, round(spot), right), day, 2.0, 2.1)]

    def history(self, contract, start, end):
        out, d = [], start
        while d <= end:
            if d in self.bars:
                intrinsic = max(0.0, self.bars[d] - contract.strike)
                mid = 2.05 + intrinsic * 0.6
                out.append(OptionQuote(contract, d, mid - 0.05, mid + 0.05))
            d += timedelta(days=1)
        return out


def test_engine_end_to_end_and_runner_contract():
    bars = _bars_with_cross()
    sigs = ma_golden_cross(bars)
    assert any(r == "call" for _, r in sigs)
    run = build_runner("SPY", FakeProvider(bars), bars, {"signal": "ma_golden_cross"})
    trades = run({"trail_pct": 0.2, "lock_trigger_pct": 0.5, "lock_floor_pct": 0.3, "stop_loss_pct": 0.35},
                 bars.date.min(), bars.date.max())
    assert trades, "expected at least one trade"
    t = trades[0]
    assert t.entry_date > t.signal_date  # no look-ahead
    assert t.entry_price == pytest.approx(2.1)  # paid the ask
    assert isinstance(t.return_pct, float) and t.exit_date >= t.entry_date
    with pytest.raises(ValueError):
        run({"nope": 1}, bars.date.min(), bars.date.max())


def test_report_metrics():
    bars = _bars_with_cross()
    trades = run_backtest("SPY", bars, FakeProvider(bars), "ma_golden_cross",
                          exits=ExitParams(exit_dte=0, max_hold_days=10))
    s = summarize_trades(trades)
    assert s["SPY"]["trades"] == s["__overall__"]["trades"] == len(trades)
    assert 0 <= s["SPY"]["win_rate"] <= 1
    assert "| SPY |" in to_markdown(s)


def test_parse_theta_eod_shape():
    payload = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "CALL", "expiration": "2025-02-21"},
                             "data": [{"created": "2025-01-21T17:15:40.288", "bid": 11.74, "ask": 11.80, "volume": 8751},
                                      {"created": "2025-01-22T17:19:04.107", "bid": 0, "ask": 0}]}]}
    q = parse_theta_eod(payload)
    assert len(q) == 1 and q[0].contract == C and q[0].day == date(2025, 1, 21) and q[0].mid == pytest.approx(11.77)


def test_parse_av_chain_shape():
    payload = {"data": [{"contractID": "SPY250221C00600000", "symbol": "SPY", "expiration": "2025-02-21",
                         "strike": "600.00", "type": "call", "bid": "11.74", "ask": "11.80", "delta": "0.52",
                         "volume": "10", "date": "2025-01-21"}]}
    q = parse_av_chain(payload, date(2025, 1, 21))
    assert q[0].contract == C and q[0].delta == pytest.approx(0.52)


def test_fallback_provider_moves_on():
    class Broken:
        name = "broken"

        def chain(self, *a):
            raise RuntimeError("down")

        def history(self, *a):
            return []

    class Good:
        name = "good"

        def chain(self, *a):
            return ["ok"]

        def history(self, *a):
            return ["ok"]

    fp = FallbackProvider([Broken(), Good()])
    assert fp.chain("SPY", D0, "call", 45) == ["ok"]
    assert fp.history(C, D0, D0) == ["ok"]
