# -*- coding: utf-8 -*-
"""Plain data types for the options backtester."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Any, Dict, Mapping, Optional

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    expiration: date
    strike: float
    right: str  # "call" | "put"

    @property
    def label(self) -> str:
        return f"{self.symbol} {self.expiration.isoformat()} {self.strike:g}{self.right[0].upper()}"


@dataclass(frozen=True)
class OptionQuote:
    """End-of-day closing NBBO for one contract on one day."""

    contract: OptionContract
    day: date
    bid: float
    ask: float
    volume: Optional[int] = None
    delta: Optional[float] = None
    open_interest: Optional[int] = None
    iv: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        return (self.ask - self.bid) / mid if mid > 0 else float("inf")


def _from_mapping(cls, params: Optional[Mapping[str, Any]]):
    if not params:
        return cls()
    names = {f.name for f in fields(cls)}
    unknown = set(params) - names
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**dict(params))


@dataclass(frozen=True)
class ExitParams:
    """Premium exit rules. All percentages are fractions (0.30 = 30%).

    Defaults are starting guesses, not optimized values.
    """

    stop_loss_pct: float = 0.35        # hard stop: exit if mark <= entry * (1 - stop_loss_pct)
    trail_pct: float = 0.25            # trailing stop: exit if mark <= peak * (1 - trail_pct)
    trail_activation_pct: float = 0.20  # arm the trail only after peak gain >= this, so the hard stop governs early
    lock_trigger_pct: float = 0.50     # once peak gain >= this ...
    lock_floor_pct: float = 0.30       # ... the stop can never sit below entry * (1 + lock_floor_pct)
    take_profit_pct: Optional[float] = None  # optional fixed target, None = off
    exit_dte: int = 5                  # close when calendar days to expiry <= this
    max_hold_days: int = 30            # calendar days
    commission_per_contract: float = 0.65

    @classmethod
    def from_dict(cls, params: Optional[Mapping[str, Any]]) -> "ExitParams":
        return _from_mapping(cls, params)

    def validate(self) -> None:
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError("stop_loss_pct must be in (0, 1)")
        if not 0 < self.trail_pct < 1:
            raise ValueError("trail_pct must be in (0, 1)")
        if self.lock_floor_pct > self.lock_trigger_pct:
            raise ValueError("lock_floor_pct must not exceed lock_trigger_pct")


@dataclass(frozen=True)
class SelectionParams:
    """How to pick the contract on a signal day. Defaults are guesses."""

    target_dte: int = 30
    min_dte: int = 21
    max_dte: int = 45
    otm_pct: float = 0.02          # strike ~2% out of the money; ignored when target_delta is set
    target_delta: Optional[float] = None  # e.g. 0.40; Black-Scholes from mid when the provider has no deltas
    min_bid: float = 0.20
    max_spread_pct: float = 0.10   # (ask - bid) / mid
    allow_puts: bool = True

    @classmethod
    def from_dict(cls, params: Optional[Mapping[str, Any]]) -> "SelectionParams":
        return _from_mapping(cls, params)


@dataclass
class OptionTrade:
    symbol: str
    signal: str
    signal_date: date
    contract: OptionContract
    entry_date: date
    entry_price: float   # ask paid
    exit_date: date
    exit_price: float    # bid received (or intrinsic at expiry)
    exit_reason: str
    peak_mark: float
    return_pct: float    # net of commissions, fraction
    pnl_usd: float       # one contract, net of commissions
    days_held: int
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["contract"] = self.contract.label
        for key in ("signal_date", "entry_date", "exit_date"):
            out[key] = getattr(self, key).isoformat()
        return out
