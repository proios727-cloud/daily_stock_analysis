# -*- coding: utf-8 -*-
"""Options chain picker (pure logic, no I/O).

Ranks liquid US underlyings by how cheaply and reliably their options can be
bought and sold, then selects single long contracts per underlying that fit a
delta band and a days-to-expiry window. Read-only: it produces a ranked list
for the premium backtester, it never places orders.

Inputs are ``src.options.models.OptionQuote`` rows (the same quotes the
backtester uses) plus an underlying price per symbol. Greeks are computed here
(Black-Scholes on the mid price) when the source does not supply them, so
EOD-only feeds such as ThetaData's free tier work.

Defaults marked ``GUESS`` are first-version choices that the backtester should
tune; they are not proven.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence

from .models import OptionQuote

DEFAULT_UNIVERSE = (
    "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META",
    "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "PLTR", "NFLX",
)

OUTPUT_SCHEMA_VERSION = "options_picker.v1"


@dataclass
class PickerConfig:
    delta_min: float = 0.40
    delta_max: float = 0.60
    dte_min: int = 14  # 2 weeks
    dte_max: int = 42  # 6 weeks
    target_dte: int = 28  # GUESS: middle of the window
    risk_free_rate: float = 0.04  # GUESS: flat rate, override from env
    # Hard liquidity floors for a contract (GUESS)
    min_bid: float = 0.10
    max_rel_spread: float = 0.10  # (ask - bid) / mid
    min_volume: int = 10
    min_open_interest: int = 100  # applied only when OI is known
    # Ticker score weights (GUESS); renormalized over the metrics available
    weight_spread: float = 0.35
    weight_open_interest: float = 0.25
    weight_volume: float = 0.25
    weight_iv_rank: float = 0.15
    # For buying premium a lower IV rank is cheaper, so it scores higher (GUESS)
    prefer_low_iv_rank: bool = True
    contracts_per_ticker: int = 3
    rights: Sequence[str] = ("call", "put")


# --------------------------------------------------------------------------
# Black-Scholes helpers
# --------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _is_call(right: str) -> bool:
    return right[:1].upper() == "C"


def bs_price(spot: float, strike: float, t: float, rate: float, vol: float, right: str) -> float:
    if t <= 0 or vol <= 0:
        intrinsic = spot - strike if _is_call(right) else strike - spot
        return max(intrinsic, 0.0)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc = math.exp(-rate * t)
    if _is_call(right):
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(spot: float, strike: float, t: float, rate: float, vol: float, right: str) -> float:
    if t <= 0 or vol <= 0:
        itm = spot > strike if _is_call(right) else spot < strike
        return (1.0 if itm else 0.0) * (1 if _is_call(right) else -1)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    return _norm_cdf(d1) if _is_call(right) else _norm_cdf(d1) - 1.0


def implied_vol(price: float, spot: float, strike: float, t: float, rate: float, right: str) -> Optional[float]:
    """Bisection IV solve. Returns None when the price is outside no-arbitrage bounds."""
    if price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if price < bs_price(spot, strike, t, rate, lo, right) or price > bs_price(spot, strike, t, rate, hi, right):
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(spot, strike, t, rate, mid, right) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------
# Volatility rank
# --------------------------------------------------------------------------

def realized_vol_series(closes: Sequence[float], window: int = 21) -> List[float]:
    """Annualized rolling close-to-close volatility."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    out = []
    for i in range(window, len(rets) + 1):
        chunk = rets[i - window:i]
        mean = sum(chunk) / window
        var = sum((r - mean) ** 2 for r in chunk) / (window - 1)
        out.append(math.sqrt(var) * math.sqrt(252))
    return out


def iv_rank(current_iv: Optional[float], history: Sequence[float]) -> Optional[float]:
    """(current - min) / (max - min) over the history, clipped to [0, 1]."""
    if current_iv is None or len(history) < 20:
        return None
    lo, hi = min(history), max(history)
    if hi <= lo:
        return None
    return min(max((current_iv - lo) / (hi - lo), 0.0), 1.0)


# --------------------------------------------------------------------------
# Picker
# --------------------------------------------------------------------------

@dataclass
class ContractPick:
    contract_id: str
    symbol: str
    right: str  # "call" or "put", same as OptionContract.right
    strike: float
    expiration: str
    dte: int
    bid: float
    ask: float
    mid: float
    rel_spread: float
    volume: int
    open_interest: Optional[int]
    iv: Optional[float]
    delta: float
    greeks_source: str  # "source" or "bs_mid"
    score: float = 0.0


@dataclass
class TickerRank:
    symbol: str
    underlying_price: float
    rank: int = 0
    score: float = 0.0
    eligible_contracts: int = 0
    median_rel_spread: Optional[float] = None
    total_volume: int = 0
    total_open_interest: Optional[int] = None
    atm_iv: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_rank_source: str = "none"  # "iv_history", "hv_proxy" or "none"
    contracts: List[ContractPick] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def occ_symbol(symbol: str, expiration: date, right: str, strike: float) -> str:
    """OCC-style id, e.g. SPY261218C00725000. ``right`` is "call"/"put" (or C/P)."""
    return f"{symbol.upper()}{expiration:%y%m%d}{right[:1].upper()}{int(round(strike * 1000)):08d}"


def _enrich(q: OptionQuote, spot: float, as_of: date, cfg: PickerConfig) -> Optional[ContractPick]:
    k = q.contract
    dte = (k.expiration - as_of).days
    if q.bid <= 0 or q.ask <= 0 or q.ask < q.bid:
        return None
    mid = q.mid
    t = max(dte, 0) / 365.0
    iv, delta, source = q.iv, q.delta, "source"
    if iv is None or delta is None:
        iv = implied_vol(mid, spot, k.strike, t, cfg.risk_free_rate, k.right)
        if iv is None:
            return None
        delta = bs_delta(spot, k.strike, t, cfg.risk_free_rate, iv, k.right)
        source = "bs_mid"
    return ContractPick(
        contract_id=occ_symbol(k.symbol, k.expiration, k.right, k.strike),
        symbol=k.symbol.upper(), right=k.right, strike=k.strike,
        expiration=k.expiration.isoformat(), dte=dte,
        bid=q.bid, ask=q.ask, mid=round(mid, 4),
        rel_spread=round((q.ask - q.bid) / mid, 4),
        volume=int(q.volume or 0), open_interest=q.open_interest,
        iv=round(iv, 4) if iv is not None else None,
        delta=round(delta, 4), greeks_source=source,
    )


def _passes(c: ContractPick, cfg: PickerConfig) -> bool:
    if not (cfg.dte_min <= c.dte <= cfg.dte_max):
        return False
    if not (cfg.delta_min <= abs(c.delta) <= cfg.delta_max):
        return False
    if c.bid < cfg.min_bid or c.rel_spread > cfg.max_rel_spread or c.volume < cfg.min_volume:
        return False
    if c.open_interest is not None and c.open_interest < cfg.min_open_interest:
        return False
    return True


def _contract_score(c: ContractPick, cfg: PickerConfig) -> float:
    """0..1, higher is better. Equal parts delta fit, DTE fit, tight spread, activity."""
    delta_fit = 1 - min(abs(abs(c.delta) - 0.5) / 0.1, 1.0)
    half_window = max(cfg.dte_max - cfg.dte_min, 1) / 2
    dte_fit = 1 - min(abs(c.dte - cfg.target_dte) / half_window, 1.0)
    spread_fit = 1 - min(c.rel_spread / cfg.max_rel_spread, 1.0)
    activity = c.volume + (c.open_interest or 0)
    activity_fit = min(math.log10(1 + activity) / 5, 1.0)  # 100k contracts ~ 1.0
    return round(0.3 * delta_fit + 0.2 * dte_fit + 0.3 * spread_fit + 0.2 * activity_fit, 4)


def _atm_iv(contracts: Iterable[ContractPick], cfg: PickerConfig) -> Optional[float]:
    """Mean IV of the contracts closest to 0.50 delta and the target DTE."""
    pool = [c for c in contracts if c.iv and cfg.dte_min <= c.dte <= cfg.dte_max]
    if not pool:
        return None
    pool.sort(key=lambda c: (abs(abs(c.delta) - 0.5), abs(c.dte - cfg.target_dte)))
    top = pool[:4]
    return sum(c.iv for c in top) / len(top)


def _percentile_scores(values: Dict[str, Optional[float]], higher_is_better: bool) -> Dict[str, Optional[float]]:
    known = {k: v for k, v in values.items() if v is not None}
    if not known:
        return {k: None for k in values}
    ordered = sorted(known.values())
    n = len(ordered)
    out: Dict[str, Optional[float]] = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
            continue
        pct = 1.0 if n == 1 else sum(1 for x in ordered if x < v) / (n - 1)
        out[k] = pct if higher_is_better else 1 - pct
    return out


def rank_universe(
    chains: Dict[str, Sequence[OptionQuote]],
    spots: Dict[str, float],
    as_of: date,
    cfg: Optional[PickerConfig] = None,
    vol_history: Optional[Dict[str, Sequence[float]]] = None,
    vol_history_kind: str = "hv_proxy",
) -> List[TickerRank]:
    """Rank tickers and pick contracts.

    ``vol_history`` maps symbol -> daily vol series (annualized). With
    ``vol_history_kind="iv_history"`` it is a true IV history; with
    ``"hv_proxy"`` it is realized vol, so the rank says how rich current IV is
    against past realized vol (a stand-in until IV history is collected).
    """
    cfg = cfg or PickerConfig()
    vol_history = vol_history or {}
    ranks: List[TickerRank] = []

    for symbol, quotes in chains.items():
        spot = spots.get(symbol)
        tr = TickerRank(symbol=symbol.upper(), underlying_price=spot or 0.0)
        if not spot:
            tr.notes.append("no underlying price")
            ranks.append(tr)
            continue
        todays = [q for q in quotes if q.day == as_of and q.contract.right in cfg.rights]
        enriched = [c for q in todays for c in [_enrich(q, spot, as_of, cfg)] if c]
        eligible = [c for c in enriched if _passes(c, cfg)]
        tr.eligible_contracts = len(eligible)
        tr.atm_iv = _atm_iv(enriched, cfg)
        hist = vol_history.get(symbol)
        if hist:
            tr.iv_rank = iv_rank(tr.atm_iv, hist)
            tr.iv_rank_source = vol_history_kind if tr.iv_rank is not None else "none"
        if eligible:
            tr.median_rel_spread = median(c.rel_spread for c in eligible)
            tr.total_volume = sum(c.volume for c in eligible)
            ois = [c.open_interest for c in eligible if c.open_interest is not None]
            tr.total_open_interest = sum(ois) if ois else None
            for c in eligible:
                c.score = _contract_score(c, cfg)
            eligible.sort(key=lambda c: c.score, reverse=True)
            tr.contracts = _diverse_top(eligible, cfg.contracts_per_ticker)
        else:
            tr.notes.append("no contract passed delta/DTE/liquidity filters")
        ranks.append(tr)

    scored = [r for r in ranks if r.eligible_contracts]
    by = {r.symbol: r for r in scored}
    components = [
        (cfg.weight_spread, _percentile_scores({s: r.median_rel_spread for s, r in by.items()}, False)),
        (cfg.weight_open_interest, _percentile_scores(
            {s: (math.log1p(r.total_open_interest) if r.total_open_interest is not None else None) for s, r in by.items()}, True)),
        (cfg.weight_volume, _percentile_scores({s: math.log1p(r.total_volume) for s, r in by.items()}, True)),
        (cfg.weight_iv_rank, _percentile_scores({s: r.iv_rank for s, r in by.items()}, not cfg.prefer_low_iv_rank)),
    ]
    for s, r in by.items():
        num = den = 0.0
        for w, scores in components:
            if scores.get(s) is not None:
                num += w * scores[s]
                den += w
        r.score = round(num / den, 4) if den else 0.0

    ranks.sort(key=lambda r: (r.eligible_contracts > 0, r.score), reverse=True)
    for i, r in enumerate(ranks, 1):
        r.rank = i if r.eligible_contracts else 0
    return ranks


def _diverse_top(contracts: List[ContractPick], n: int) -> List[ContractPick]:
    """Best contracts, keeping at least one call and one put when both exist."""
    picks: List[ContractPick] = []
    for right in ("call", "put"):
        best = next((c for c in contracts if c.right == right), None)
        if best:
            picks.append(best)
    for c in contracts:
        if len(picks) >= n:
            break
        if c not in picks:
            picks.append(c)
    picks = picks[:n]
    picks.sort(key=lambda c: c.score, reverse=True)
    return picks


def to_payload(ranks: List[TickerRank], as_of: date, cfg: PickerConfig, sources: Dict[str, str]) -> dict:
    """JSON-ready output for the backtester."""
    return {
        "schema": OUTPUT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "sources": sources,
        "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(cfg).items()},
        "tickers": [asdict(r) for r in ranks],
    }


def flat_contract_rows(ranks: List[TickerRank], as_of: date) -> List[dict]:
    """One row per picked contract, ticker rank attached; CSV friendly."""
    rows = []
    for r in ranks:
        for c in r.contracts:
            row = {"as_of": as_of.isoformat(), "ticker_rank": r.rank, "ticker_score": r.score,
                   "underlying_price": r.underlying_price}
            row.update(asdict(c))
            rows.append(row)
    return rows
