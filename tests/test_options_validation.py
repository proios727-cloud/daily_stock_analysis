# -*- coding: utf-8 -*-
"""Walk-forward / OOS harness on synthetic backtesters with known truth."""

import random
from datetime import date, timedelta

from src.services.options_validation import compute_stats, expand_grid, walk_forward

START, END = date(2020, 1, 1), date(2024, 12, 31)


def _days(start, end, every=4):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=every)


def real_edge_run(params, start, end):
    """Stable positive edge that is roughly flat across the grid."""
    rng = random.Random(hash((start, params["trail"])) & 0xFFFF)
    return [{"entry_date": d, "exit_date": d, "return_pct": rng.gauss(0.08, 0.3)}
            for d in _days(start, end)]


def noise_run(params, start, end):
    """Pure noise: each param set gets a random, period-dependent result."""
    out = []
    for d in _days(start, end):
        rng = random.Random(hash((d, params["trail"], params["lock"])))
        out.append({"entry_date": d, "exit_date": d, "return_pct": rng.gauss(-0.02, 0.4)})
    return out


GRID = {"trail": [0.15, 0.20, 0.25, 0.30], "lock": [0.2, 0.3]}


def test_compute_stats_basics():
    s = compute_stats([{"return_pct": 0.5}, {"return_pct": -0.25}])
    assert s.trades == 2 and s.win_rate == 0.5
    assert abs(s.profit_factor - 2.0) < 1e-9
    assert abs(s.max_drawdown - 0.25) < 1e-9
    assert compute_stats([]).trades == 0


def test_expand_grid():
    assert len(expand_grid(GRID)) == 8


def test_real_edge_is_not_flagged_overfit():
    rep = walk_forward(real_edge_run, GRID, START, END, train_days=365, test_days=90, holdout_days=180)
    assert rep.folds
    assert rep.oos_stats.expectancy > 0
    assert rep.verdict in {"ROBUST", "FRAGILE"}
    assert "Verdict" in rep.to_markdown()


def test_noise_is_flagged_overfit():
    rep = walk_forward(noise_run, GRID, START, END, train_days=365, test_days=90, holdout_days=180)
    assert rep.verdict in {"OVERFIT", "FRAGILE"}
    assert rep.efficiency < 0.5


def test_insufficient_data():
    rep = walk_forward(lambda p, s, e: [], GRID, START, END)
    assert rep.verdict == "INSUFFICIENT_DATA"
