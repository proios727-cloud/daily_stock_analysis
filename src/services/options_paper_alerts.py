# -*- coding: utf-8 -*-
"""Paper-only entry / exit alerts for long options premium positions.

Tracks a paper position per contract and, on each new end-of-day premium mark,
applies exactly the backtester's exit rules (``src.options.exits.stop_level``
with ``src.options.models.ExitParams``), so live alerts fire at the levels the
backtest measured. Alerts go through the existing ``NotificationService`` on
the ``alert`` route. No orders are ever placed.

Per mark, in the backtester's order:

1. peak = max(peak, mark)
2. take_profit (if set): exit if mark >= entry * (1 + take_profit_pct)
3. stop: exit if mark <= stop_level(entry, peak) — the highest of the hard stop,
   the trailing stop (armed at trail_activation_pct) and the profit-lock floor
   (armed at lock_trigger_pct)
4. dte_exit when the expiry is known, then max_hold

Default exit levels are the backtester's unvalidated starting guesses.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.options.exits import stop_level
from src.options.models import ExitParams

logger = logging.getLogger(__name__)

PAPER_TAG = "PAPER ONLY, no order placed"


@dataclass
class PaperPosition:
    contract: str                    # e.g. "AAPL 2026-12-19 C 200"
    underlying: str
    entry_premium: float
    entry_date: str                  # ISO date
    params: ExitParams
    expiration: Optional[str] = None  # ISO date; enables dte_exit when set
    peak_premium: float = 0.0
    last_premium: float = 0.0
    lock_armed: bool = False
    trail_armed: bool = False
    closed: bool = False
    exit_reason: Optional[str] = None
    exit_premium: Optional[float] = None
    exit_date: Optional[str] = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.entry_premium <= 0:
            raise ValueError("entry_premium must be positive")
        self.params.validate()
        self.peak_premium = self.peak_premium or self.entry_premium
        self.last_premium = self.last_premium or self.entry_premium

    @property
    def gain_pct(self) -> float:
        return self.last_premium / self.entry_premium - 1

    def active_stop(self) -> tuple[str, float]:
        level, rule = stop_level(self.entry_premium, self.peak_premium, self.params)
        return rule, level


@dataclass
class AlertEvent:
    kind: str           # ENTRY | TRAIL_ARMED | LOCK_ARMED | EXIT
    contract: str
    premium: float
    detail: str
    dedup_key: str
    exit_reason: Optional[str] = None


def _key(pos: PaperPosition, suffix: str) -> str:
    return f"opt-paper:{pos.contract}:{pos.entry_date}:{suffix}"


def _close(pos: PaperPosition, mark: float, day: date, reason: str, detail: str) -> AlertEvent:
    pos.closed, pos.exit_reason, pos.exit_premium, pos.exit_date = True, reason, mark, day.isoformat()
    full = (f"{detail}; entry {pos.entry_premium:.2f}, peak {pos.peak_premium:.2f}, "
            f"P/L at mark {pos.gain_pct:+.1%} (fill at bid may be lower)")
    return AlertEvent("EXIT", pos.contract, mark, full, _key(pos, "exit"), exit_reason=reason)


def update_position(pos: PaperPosition, mark: float, day: Optional[date] = None) -> List[AlertEvent]:
    """Apply one end-of-day premium mark (mid). Returns the alert events it produced."""
    if pos.closed or mark is None or mark <= 0:
        return []
    day = day or date.today()
    if day <= date.fromisoformat(pos.entry_date):
        return []  # backtester ignores quotes on or before the entry day
    p = pos.params
    pos.last_premium = mark
    pos.peak_premium = max(pos.peak_premium, mark)
    entry = pos.entry_premium

    if p.take_profit_pct is not None and mark >= entry * (1 + p.take_profit_pct):
        return [_close(pos, mark, day, "take_profit", f"take profit hit at {mark:.2f}")]
    level, rule = stop_level(entry, pos.peak_premium, p)
    if mark <= level:
        return [_close(pos, mark, day, rule, f"{rule.replace('_', ' ')} hit at {mark:.2f} (level {level:.2f})")]
    if pos.expiration and (date.fromisoformat(pos.expiration) - day).days <= p.exit_dte:
        return [_close(pos, mark, day, "dte_exit", f"{p.exit_dte} days to expiry, closing at {mark:.2f}")]
    if (day - date.fromisoformat(pos.entry_date)).days >= p.max_hold_days:
        return [_close(pos, mark, day, "max_hold", f"held {p.max_hold_days} days, closing at {mark:.2f}")]

    events: List[AlertEvent] = []
    peak_gain = pos.peak_premium / entry - 1
    if not pos.trail_armed and peak_gain >= p.trail_activation_pct:
        pos.trail_armed = True
        events.append(AlertEvent(
            "TRAIL_ARMED", pos.contract, mark,
            f"Gain {peak_gain:+.1%}: trailing stop {p.trail_pct:.0%} below peak now active "
            f"(level {pos.peak_premium * (1 - p.trail_pct):.2f})",
            _key(pos, "trail"),
        ))
    if not pos.lock_armed and peak_gain >= p.lock_trigger_pct:
        pos.lock_armed = True
        events.append(AlertEvent(
            "LOCK_ARMED", pos.contract, mark,
            f"Gain {peak_gain:+.1%}: profit lock floor set at {entry * (1 + p.lock_floor_pct):.2f} "
            f"(+{p.lock_floor_pct:.0%} minimum at the mark)",
            _key(pos, "lock"),
        ))
    return events


def open_position(contract: str, underlying: str, entry_premium: float, params: Optional[ExitParams] = None,
                  note: str = "", day: Optional[date] = None,
                  expiration: Optional[date] = None) -> tuple[PaperPosition, AlertEvent]:
    day = day or date.today()
    p = params or ExitParams()
    pos = PaperPosition(contract=contract, underlying=underlying, entry_premium=entry_premium,
                        entry_date=day.isoformat(), params=p, note=note,
                        expiration=expiration.isoformat() if expiration else None)
    hs = entry_premium * (1 - p.stop_loss_pct)
    detail = (f"Paper buy at {entry_premium:.2f}. Hard stop {hs:.2f} (-{p.stop_loss_pct:.0%}), "
              f"trail {p.trail_pct:.0%} after +{p.trail_activation_pct:.0%}, "
              f"profit lock +{p.lock_floor_pct:.0%} after +{p.lock_trigger_pct:.0%}")
    if note:
        detail += f". Signal: {note}"
    return pos, AlertEvent("ENTRY", contract, entry_premium, detail, _key(pos, "entry"))


def format_alert(event: AlertEvent) -> tuple[str, str, str]:
    """Return (title, body, alert_type) for NotificationBuilder.build_simple_alert."""
    titles = {
        "ENTRY": ("Options entry", "info"),
        "TRAIL_ARMED": ("Trailing stop armed", "info"),
        "LOCK_ARMED": ("Profit lock armed", "success"),
        "EXIT": ("Options exit", "warning"),
    }
    label, alert_type = titles.get(event.kind, (event.kind, "info"))
    if event.kind == "EXIT" and event.exit_reason in {"profit_lock", "take_profit"}:
        alert_type = "success"
    body = f"`{event.contract}` @ {event.premium:.2f}\n\n{event.detail}\n\n_{PAPER_TAG}_"
    return f"{label} | {event.contract}", body, alert_type


class OptionsPaperAlerter:
    """Holds paper positions, applies marks and sends alerts. State can persist to JSON."""

    def __init__(self, notifier: Any = None, state_path: Optional[str] = None):
        self._notifier = notifier
        self.state_path = Path(state_path) if state_path else None
        self.positions: Dict[str, PaperPosition] = {}
        if self.state_path and self.state_path.exists():
            self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        for item in raw.get("positions", []):
            params = ExitParams.from_dict(item.pop("params"))
            pos = PaperPosition(params=params, **item)
            self.positions[pos.contract] = pos

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"positions": [asdict(p) for p in self.positions.values()]}
        self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- actions ---------------------------------------------------------
    def enter(self, contract: str, underlying: str, entry_premium: float,
              params: Optional[ExitParams] = None, note: str = "", day: Optional[date] = None,
              expiration: Optional[date] = None) -> Optional[AlertEvent]:
        existing = self.positions.get(contract)
        if existing and not existing.closed:
            logger.info("[OptionsPaper] %s already open, entry ignored", contract)
            return None
        pos, event = open_position(contract, underlying, entry_premium, params, note, day, expiration)
        self.positions[contract] = pos
        self._dispatch(event)
        self.save()
        return event

    def mark(self, marks: Dict[str, float], day: Optional[date] = None) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        for contract, premium in marks.items():
            pos = self.positions.get(contract)
            if pos is None:
                continue
            events.extend(update_position(pos, premium, day))
        for ev in events:
            self._dispatch(ev)
        self.save()
        return events

    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if not p.closed]

    def _dispatch(self, event: AlertEvent) -> bool:
        """Send one alert. A failed channel never breaks position tracking."""
        try:
            title, body, alert_type = format_alert(event)
            if self._notifier is not None:
                notifier, text = self._notifier, f"{title}\n\n{body}"
                try:
                    from src.notification import NotificationBuilder
                    text = NotificationBuilder.build_simple_alert(title=title, content=body, alert_type=alert_type)
                except ImportError:
                    pass
            else:
                from src.notification import NotificationBuilder, NotificationService

                notifier = NotificationService()
                text = NotificationBuilder.build_simple_alert(title=title, content=body, alert_type=alert_type)
            result = notifier.send_with_results(text, route_type="alert", dedup_key=event.dedup_key)
            return bool(getattr(result, "success", result))
        except Exception as exc:  # noqa: BLE001 - notification must not stop tracking
            logger.warning("[OptionsPaper] alert send failed for %s: %s", event.contract, exc)
            return False
