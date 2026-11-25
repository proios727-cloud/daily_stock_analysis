# -*- coding: utf-8 -*-
"""Offline tests for the options chain picker."""

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.options.models import OptionContract, OptionQuote
from src.options.pick import main as pick_main
from src.options.picker import (
    PickerConfig, bs_delta, bs_price, flat_contract_rows, implied_vol, iv_rank, occ_symbol, rank_universe,
    to_payload,
)
from src.options.providers import ThetaSnapshotProvider

AS_OF = date(2026, 11, 23)


def _chain(symbol, spot, vol, spread_pct, volume, expiries=(25,), strikes_pct=(-0.06, -0.03, 0, 0.03, 0.06)):
    rows = []
    for dte in expiries:
        exp = AS_OF + timedelta(days=dte)
        for pct in strikes_pct:
            k = round(spot * (1 + pct), 1)
            for right in ("call", "put"):
                mid = bs_price(spot, k, dte / 365, 0.04, vol, right)
                half = mid * spread_pct / 2
                rows.append(OptionQuote(OptionContract(symbol, exp, k, right), AS_OF,
                                        round(mid - half, 2), round(mid + half, 2), volume))
    return rows


def test_iv_round_trip_and_delta_sign():
    price = bs_price(100, 100, 30 / 365, 0.04, 0.25, "call")
    assert implied_vol(price, 100, 100, 30 / 365, 0.04, "call") == pytest.approx(0.25, abs=1e-4)
    assert 0.5 < bs_delta(100, 100, 30 / 365, 0.04, 0.25, "call") < 0.56
    assert -0.5 < bs_delta(100, 100, 30 / 365, 0.04, 0.25, "put") < -0.44
    assert implied_vol(0.0, 100, 100, 0.1, 0.04, "call") is None


def test_iv_rank_bounds():
    hist = [0.1 + i * 0.01 for i in range(30)]
    assert iv_rank(0.1, hist) == 0.0
    assert iv_rank(1.0, hist) == 1.0
    assert iv_rank(0.2, hist[:5]) is None


def test_occ_symbol():
    assert occ_symbol("spy", date(2026, 12, 18), "call", 725) == "SPY261218C00725000"
    assert occ_symbol("IWM", date(2026, 12, 18), "put", 267.5) == "IWM261218P00267500"


def test_filters_delta_and_dte_window():
    chains = {"AAA": _chain("AAA", 100, 0.3, 0.02, 500, expiries=(7, 25, 60))}
    ranks = rank_universe(chains, {"AAA": 100.0}, AS_OF, PickerConfig())
    picks = ranks[0].contracts
    assert picks
    for c in picks:
        assert 14 <= c.dte <= 42
        assert 0.40 <= abs(c.delta) <= 0.60
        assert c.greeks_source == "bs_mid"
    assert {c.right for c in picks} == {"call", "put"}


def test_tighter_and_busier_ticker_ranks_first():
    chains = {
        "TIGHT": _chain("TIGHT", 100, 0.3, 0.01, 5000),
        "WIDE": _chain("WIDE", 100, 0.3, 0.08, 50),
    }
    ranks = rank_universe(chains, {"TIGHT": 100.0, "WIDE": 100.0}, AS_OF)
    assert [r.symbol for r in ranks] == ["TIGHT", "WIDE"]
    assert ranks[0].rank == 1 and ranks[0].score > ranks[1].score


def test_illiquid_or_missing_spot_is_unranked_not_fatal():
    chains = {
        "OK": _chain("OK", 100, 0.3, 0.02, 500),
        "THIN": _chain("THIN", 100, 0.3, 0.5, 1),
        "NOSPOT": _chain("NOSPOT", 100, 0.3, 0.02, 500),
    }
    ranks = rank_universe(chains, {"OK": 100.0, "THIN": 100.0}, AS_OF)
    by = {r.symbol: r for r in ranks}
    assert by["OK"].rank == 1
    assert by["THIN"].rank == 0 and by["THIN"].notes
    assert by["NOSPOT"].rank == 0 and "no underlying price" in by["NOSPOT"].notes


def test_open_interest_floor_only_when_known():
    rows = [replace(q, open_interest=5) for q in _chain("AAA", 100, 0.3, 0.02, 500)]
    ranks = rank_universe({"AAA": rows}, {"AAA": 100.0}, AS_OF)
    assert ranks[0].eligible_contracts == 0


def test_payload_and_flat_rows_are_json_ready():
    chains = {"AAA": _chain("AAA", 100, 0.3, 0.02, 500)}
    cfg = PickerConfig()
    ranks = rank_universe(chains, {"AAA": 100.0}, AS_OF, cfg, vol_history={"AAA": [0.2 + i / 1000 for i in range(60)]})
    payload = to_payload(ranks, AS_OF, cfg, {"chain": "test"})
    json.dumps(payload)
    assert payload["schema"] == "options_picker.v1"
    assert payload["tickers"][0]["iv_rank_source"] == "hv_proxy"
    rows = flat_contract_rows(ranks, AS_OF)
    assert rows and {"contract_id", "delta", "dte", "ticker_rank"} <= set(rows[0])


THETA_PAYLOAD = {"response": [{
    "contract": {"symbol": "SPY", "strike": 725.0, "right": "CALL", "expiration": "2026-12-18"},
    "data": [{"created": "2026-11-23T17:16:36.686", "bid": 16.59, "ask": 16.71, "volume": 14424}],
}]}


def test_snapshot_provider_filters_day_right_and_dte(tmp_path):
    (tmp_path / "SPY_options.json").write_text(json.dumps(THETA_PAYLOAD))
    p = ThetaSnapshotProvider(str(tmp_path))
    assert len(p.chain("SPY", AS_OF, "call", 43)) == 1
    assert p.chain("SPY", AS_OF, "put", 43) == []
    assert p.chain("SPY", AS_OF, "call", 10) == []
    assert p.chain("SPY", date(2026, 11, 20), "call", 43) == []


def test_cli_end_to_end_on_snapshot(tmp_path):
    exp = AS_OF + timedelta(days=25)
    groups = [{"contract": {"symbol": "AAA", "strike": q.contract.strike, "right": q.contract.right.upper(),
                            "expiration": exp.isoformat()},
               "data": [{"created": "2026-11-23T17:16:00", "bid": q.bid, "ask": q.ask, "volume": 500}]}
              for q in _chain("AAA", 100, 0.3, 0.02, 500)]
    (tmp_path / "AAA_options.json").write_text(json.dumps({"response": groups}))
    closes = [100 * (1 + 0.01 * ((-1) ** i)) for i in range(60)] + [100.0]
    stock = [{"created": (AS_OF - timedelta(days=len(closes) - 1 - i)).isoformat() + "T17:15:00", "close": c}
             for i, c in enumerate(closes)]
    (tmp_path / "AAA_stock.json").write_text(json.dumps({"response": stock}))
    out = tmp_path / "picks.json"
    assert pick_main(["--date", AS_OF.isoformat(), "--snapshot-dir", str(tmp_path),
                      "--symbols", "AAA,BBB", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    top = payload["tickers"][0]
    assert top["symbol"] == "AAA" and top["rank"] == 1 and top["contracts"]
    assert top["iv_rank_source"] == "hv_proxy"
    assert any("BBB" in e for e in payload["errors"])
    assert (tmp_path / "picks.csv").exists()


# --- backtester wiring (--picks) -------------------------------------------

def _payload_for(symbols_ranks):
    cfg = PickerConfig()
    ranks = rank_universe({s: _chain(s, 100, 0.3, 0.02 if r else 0.5, 500 if r else 1) for s, r in symbols_ranks},
                          {s: 100.0 for s, _ in symbols_ranks}, AS_OF, cfg)
    return to_payload(ranks, AS_OF, cfg, {"chain": "test"})


def test_selection_from_picks_maps_band_and_ranked_symbols():
    from src.options.models import SelectionParams
    from src.options.picker import selection_from_picks

    symbols, overrides = selection_from_picks(_payload_for([("AAA", True), ("THIN", False)]))
    assert symbols == ["AAA"]
    sel = SelectionParams.from_dict(overrides)
    assert (sel.min_dte, sel.max_dte, sel.target_dte, sel.target_delta) == (14, 42, 28, 0.5)
    assert sel.max_spread_pct == 0.10 and sel.min_bid == 0.10
    with pytest.raises(ValueError):
        selection_from_picks({"schema": "other"})


def test_select_contract_uses_bs_delta_when_source_has_none():
    from src.options.backtest import select_contract
    from src.options.models import SelectionParams

    chain = _chain("AAA", 100, 0.3, 0.02, 500, strikes_pct=(-0.10, -0.05, 0, 0.05, 0.10))
    assert all(q.delta is None for q in chain)
    near_atm = select_contract(chain, AS_OF, 100.0, "call", SelectionParams(min_dte=14, max_dte=42, target_delta=0.5))
    assert near_atm.contract.strike == 100.0
    otm = select_contract(chain, AS_OF, 100.0, "call", SelectionParams(min_dte=14, max_dte=42, target_delta=0.25))
    assert otm.contract.strike > 100.0


def test_backtest_cli_reads_picks_file(tmp_path, monkeypatch):
    import src.options.__main__ as cli

    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps(_payload_for([("AAA", True), ("BBB", True), ("THIN", False)])))
    seen = []
    monkeypatch.setattr(cli, "default_provider", lambda: object())
    monkeypatch.setattr(cli, "_load_bars", lambda sym, start, end: sym)
    monkeypatch.setattr(cli, "run_backtest", lambda sym, bars, prov, sig, sel, ex, s, e: seen.append((sym, sel)) or [])

    assert cli.main(["--picks", str(picks), "--start", "2026-01-01", "--end", "2026-06-30",
                     "--selection-params", '{"max_dte": 35}']) == 0
    assert [s for s, _ in seen] == ["AAA", "BBB"]
    assert seen[0][1].target_delta == 0.5 and seen[0][1].max_dte == 35 and seen[0][1].min_dte == 14

    seen.clear()
    assert cli.main(["--picks", str(picks), "--symbols", "BBB,ZZZ", "--start", "2026-01-01", "--end", "2026-06-30"]) == 0
    assert [s for s, _ in seen] == ["BBB"]
