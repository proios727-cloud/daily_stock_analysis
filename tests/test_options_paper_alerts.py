# -*- coding: utf-8 -*-
"""Paper options alert state machine: hard stop, trailing stop, profit lock."""

import pytest

from src.services.options_paper_alerts import (
    ExitRules,
    OptionsPaperAlerter,
    open_position,
    update_position,
)

RULES = ExitRules(stop_loss_pct=0.30, trail_pct=0.25, trail_activation_pct=0.20,
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


def _pos():
    pos, entry = open_position("XYZ C100", "XYZ", 2.00, RULES, now="t0")
    assert entry.kind == "ENTRY"
    return pos


def test_hard_stop_exit():
    pos = _pos()
    assert update_position(pos, 1.60) == []
    ev = update_position(pos, 1.39)
    assert ev[0].kind == "EXIT" and ev[0].exit_reason == "hard_stop"
    assert pos.closed
    assert update_position(pos, 5.0) == []


def test_trailing_stop_exit_rides_peak():
    pos = _pos()
    ev = update_position(pos, 2.50)  # +25% arms trail
    assert [e.kind for e in ev] == ["TRAIL_ARMED"]
    update_position(pos, 2.90)       # +45%, peak 2.90 -> trail 2.175
    ev = update_position(pos, 2.17)
    assert ev[0].exit_reason == "trailing_stop"


def test_profit_lock_is_floor_under_trail():
    pos = _pos()
    ev = update_position(pos, 3.20)  # +60% arms trail and lock
    assert {e.kind for e in ev} == {"TRAIL_ARMED", "LOCK_ARMED"}
    # trail = 3.20*0.75 = 2.40, lock = 2.50 -> lock is the active stop
    assert pos.active_stop() == ("profit_lock", pytest.approx(2.50))
    ev = update_position(pos, 2.45)
    assert ev[0].exit_reason == "profit_lock"
    assert pos.exit_premium / pos.entry_premium - 1 > 0.2


def test_trail_overtakes_lock_after_big_run():
    pos = _pos()
    update_position(pos, 5.00)       # trail 3.75 > lock 2.50
    assert pos.active_stop()[0] == "trailing_stop"
    ev = update_position(pos, 3.70)
    assert ev[0].exit_reason == "trailing_stop"


def test_lock_never_disarms():
    pos = _pos()
    update_position(pos, 3.10)
    update_position(pos, 2.60)
    assert pos.lock_armed


def test_rules_validation():
    with pytest.raises(ValueError):
        ExitRules(lock_trigger_pct=0.2, lock_floor_pct=0.3)
    with pytest.raises(ValueError):
        ExitRules(trail_pct=1.5)


def test_alerter_dispatches_on_alert_route_and_persists(tmp_path):
    notifier = FakeNotifier()
    state = tmp_path / "paper.json"
    a = OptionsPaperAlerter(notifier=notifier, state_path=str(state))
    a.enter("XYZ C100", "XYZ", 2.00, RULES, note="ma_golden_cross")
    assert a.enter("XYZ C100", "XYZ", 2.10, RULES) is None  # no duplicate entry
    a.mark({"XYZ C100": 3.20, "UNKNOWN": 1.0})
    assert all(route == "alert" for _, route, _ in notifier.sent)
    assert "PAPER ONLY" in notifier.sent[0][0]
    assert len(notifier.sent) == 3

    b = OptionsPaperAlerter(notifier=notifier, state_path=str(state))
    assert b.positions["XYZ C100"].lock_armed
    b.mark({"XYZ C100": 2.40})
    assert b.positions["XYZ C100"].exit_reason == "profit_lock"


def test_notification_failure_does_not_break_tracking():
    a = OptionsPaperAlerter(notifier=FakeNotifier(fail=True))
    a.enter("XYZ C100", "XYZ", 2.00, RULES)
    events = a.mark({"XYZ C100": 1.0})
    assert events[0].kind == "EXIT"
