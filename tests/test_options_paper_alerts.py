# -*- coding: utf-8 -*-
"""Paper options alerts: exit rules must match the backtester exactly."""

import random
from datetime import date, timedelta

import pytest

from src.options.exits import evaluate_exit
from src.options.models import ExitParams, OptionContract, OptionQuote
from src.services.options_paper_alerts import OptionsPaperAlerter, open_position, update_position

D0 = date(2026, 1, 5)
EXP = D0 + timedelta(days=45)
P = ExitParams(stop_loss_pct=0.30, trail_pct=0.25, trail_activation_pct=0.20,
               lock_trigger_pct=0.50, lock_floor_pct=0.25)


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_with_results(self, text, route_type=None, dedup_key=None, **_):
        if self.fail:
            raise RuntimeError("channel down")
        self.sent.append((text, route_type, dedup_key))

        class R:
            success = True
        return R()


def _pos(params=P):
    pos, entry = open_position("XYZ C100", "XYZ", 2.00, params, day=D0)
    assert entry.kind == "ENTRY"
    return pos


def _feed(pos, marks):
    events = []
    for i, m in enumerate(marks, start=1):
        events += update_position(pos, m, D0 + timedelta(days=i))
    return events


def test_hard_stop_exit():
    pos = _pos()
    ev = _feed(pos, [1.60, 1.39])
    assert ev[-1].kind == "EXIT" and ev[-1].exit_reason == "hard_stop"
    assert update_position(pos, 5.0, D0 + timedelta(days=9)) == []


def test_trailing_then_lock_then_trail_overtakes():
    pos = _pos()
    assert [e.kind for e in _feed(pos, [2.50])] == ["TRAIL_ARMED"]
    pos = _pos()
    _feed(pos, [3.20])  # trail 2.40 < lock floor 2.50
    assert pos.active_stop() == ("profit_lock", pytest.approx(2.50))
    pos = _pos()
    _feed(pos, [5.00])  # trail 3.75 > lock 2.50
    assert pos.active_stop()[0] == "trailing_stop"


def test_entry_day_mark_ignored_like_backtester():
    pos = _pos()
    assert update_position(pos, 0.5, D0) == []
    assert not pos.closed


def test_invalid_params_rejected_via_backtester_validation():
    with pytest.raises(ValueError):
        open_position("X", "X", 1.0, ExitParams(lock_trigger_pct=0.2, lock_floor_pct=0.3), day=D0)


def _quotes(mids):
    c = OptionContract("XYZ", EXP, 100.0, "call")
    return [OptionQuote(c, D0 + timedelta(days=i), m - 0.02, m + 0.02) for i, m in enumerate(mids, start=1)]


@pytest.mark.parametrize("seed", range(200))
def test_parity_with_backtester_on_random_paths(seed):
    rng = random.Random(seed)
    params = ExitParams(
        stop_loss_pct=rng.choice([0.3, 0.35, 0.5]),
        trail_pct=rng.choice([0.2, 0.25, 0.3]),
        trail_activation_pct=rng.choice([0.1, 0.2, 0.4]),
        lock_trigger_pct=rng.choice([0.3, 0.5, 1.0]),
        lock_floor_pct=rng.choice([0.1, 0.3]),
        take_profit_pct=rng.choice([None, None, 1.5]),
        max_hold_days=rng.choice([10, 30]),
    )
    mids, m = [], 2.0
    for _ in range(35):
        m = max(0.05, m * (1 + rng.gauss(0.01, 0.12)))
        mids.append(round(m, 2))
    quotes = _quotes(mids)
    expected = evaluate_exit(2.0, quotes, EXP, params, D0)

    pos, _ = open_position("XYZ C100", "XYZ", 2.0, params, day=D0, expiration=EXP)
    for q in quotes:
        update_position(pos, q.mid, q.day)
        if pos.closed:
            break
    if expected.reason == "end_of_data":
        assert not pos.closed
    else:
        assert (pos.exit_reason, pos.exit_date) == (expected.reason, expected.day.isoformat())
        assert pos.peak_premium == pytest.approx(expected.peak_mark)


def test_alerter_dispatches_on_alert_route_and_persists(tmp_path):
    notifier = FakeNotifier()
    state = tmp_path / "paper.json"
    a = OptionsPaperAlerter(notifier=notifier, state_path=str(state))
    a.enter("XYZ C100", "XYZ", 2.00, P, note="volume_breakout", day=D0)
    assert a.enter("XYZ C100", "XYZ", 2.10, P, day=D0) is None  # no duplicate entry
    a.mark({"XYZ C100": 3.20, "UNKNOWN": 1.0}, day=D0 + timedelta(days=1))
    assert all(route == "alert" for _, route, _ in notifier.sent)
    assert "PAPER ONLY" in notifier.sent[0][0]
    assert len(notifier.sent) == 3

    b = OptionsPaperAlerter(notifier=notifier, state_path=str(state))
    assert b.positions["XYZ C100"].lock_armed
    b.mark({"XYZ C100": 2.40}, day=D0 + timedelta(days=2))
    assert b.positions["XYZ C100"].exit_reason == "profit_lock"


def test_notification_failure_does_not_break_tracking():
    a = OptionsPaperAlerter(notifier=FakeNotifier(fail=True))
    a.enter("XYZ C100", "XYZ", 2.00, P, day=D0)
    events = a.mark({"XYZ C100": 1.0}, day=D0 + timedelta(days=1))
    assert events[0].kind == "EXIT"
