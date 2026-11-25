# -*- coding: utf-8 -*-
"""Paper-only entry / exit alerts for long options premium positions.

Tracks a paper position per contract and, on each new premium mark, decides
whether a stop has been hit. Alerts go through the existing
``NotificationService`` on the ``alert`` route. No orders are ever placed.

Exit rules (all measured on option premium, not the underlying):

* Hard stop:   exit if mark <= entry * (1 - stop_loss_pct).
* Trailing:    once armed (gain >= trail_activation_pct), exit if mark <= peak * (1 - trail_pct).
* Profit lock: once gain >= lock_trigger_pct, a floor at entry * (1 + lock_floor_pct)
               is set and never removed; exit if mark <= floor.

The effective stop is the highest of the active levels, so the profit lock is a
guard rail under the trailing stop: the trail chases the peak for max return,
the lock guarantees a minimum banked gain once reached.

Default percentages are first-version guesses (marked GUESS); the backtester
and walk-forward validation should replace them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PAPER_TAG = "PAPER ONLY, no order placed"


@dataclass
class ExitRules:
    stop_loss_pct: float = 0.35      # GUESS
    trail_pct: float = 0.25          # GUESS
    trail_activation_pct: float = 0.20      # GUESS: trail only after +20%
    lock_trigger_pct: float = 0.50   # GUESS
    lock_floor_pct: float = 0.30     # GUESS, matches backtester default

    def __post_init__(self) -> None:
        for name in ("stop_loss_pct", "trail_pct"):
            v = getattr(self, name)
            if not 0 < v < 1:
                raise ValueError(f"{name} must be in (0, 1), got {v}")
        if self.lock_floor_pct >= self.lock_trigger_pct:
            raise ValueError("lock_floor_pct must be below lock_trigger_pct")


@dataclass
class PaperPosition:
    contract: str                    # e.g. "AAPL 2026-12-19 C 200"
    underlying: str
    entry_premium: float
    entry_time: str
    rules: ExitRules = field(default_factory=ExitRules)
    peak_premium: float = 0.0
    last_premium: float = 0.0
    lock_armed: bool = False
    trail_armed: bool = False
    closed: bool = False
    exit_reason: Optional[str] = None
    exit_premium: Optional[float] = None
    exit_time: Optional[str] = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.entry_premium <= 0:
            raise ValueError("entry_premium must be positive")
        self.peak_premium = self.peak_premium or self.entry_premium
        self.last_premium = self.last_premium or self.entry_premium

    @property
    def gain_pct(self) -> float:
        return self.last_premium / self.entry_premium - 1

    def stop_levels(self) -> Dict[str, float]:
        r = self.rules
        levels = {"hard_stop": self.entry_premium * (1 - r.stop_loss_pct)}
        if self.trail_armed:
            levels["trailing_stop"] = self.peak_premium * (1 - r.trail_pct)
        if self.lock_armed:
            levels["profit_lock"] = self.entry_premium * (1 + r.lock_floor_pct)
        return levels

    def active_stop(self) -> tuple[str, float]:
        name, level = max(self.stop_levels().items(), key=lambda kv: kv[1])
        return name, level


@dataclass
class AlertEvent:
    kind: str           # ENTRY | TRAIL_ARMED | LOCK_ARMED | EXIT
    contract: str
    premium: float
    detail: str
    dedup_key: str
    exit_reason: Optional[str] = None


def update_position(pos: PaperPosition, mark: float, now: Optional[str] = None) -> List[AlertEvent]:
    """Apply a new premium mark. Returns the alert events it produced."""
    if pos.closed or mark is None or mark <= 0:
        return []
    now = now or datetime.now().isoformat(timespec="seconds")
    events: List[AlertEvent] = []
    r = pos.rules

    # Check the stop that was in force before this mark (no look-ahead on the peak).
    stop_name, stop_level = pos.active_stop()
    if mark <= stop_level:
        pos.last_premium = mark
        pos.closed, pos.exit_reason, pos.exit_premium, pos.exit_time = True, stop_name, mark, now
        events.append(AlertEvent(
            kind="EXIT",
            contract=pos.contract,
            premium=mark,
            detail=(f"{stop_name.replace('_', ' ')} hit at {mark:.2f} (level {stop_level:.2f}); "
                    f"entry {pos.entry_premium:.2f}, peak {pos.peak_premium:.2f}, P/L {pos.gain_pct:+.1%}"),
            dedup_key=f"opt-paper:{pos.contract}:{pos.entry_time}:exit",
            exit_reason=stop_name,
        ))
        return events

    pos.last_premium = mark
    pos.peak_premium = max(pos.peak_premium, mark)
    peak_gain = pos.peak_premium / pos.entry_premium - 1

    if not pos.trail_armed and peak_gain >= r.trail_activation_pct:
        pos.trail_armed = True
        events.append(AlertEvent(
            "TRAIL_ARMED", pos.contract, mark,
            f"Gain {peak_gain:+.1%}: trailing stop {r.trail_pct:.0%} below peak now active "
            f"(level {pos.peak_premium * (1 - r.trail_pct):.2f})",
            f"opt-paper:{pos.contract}:{pos.entry_time}:trail",
        ))
    if not pos.lock_armed and peak_gain >= r.lock_trigger_pct:
        pos.lock_armed = True
        events.append(AlertEvent(
            "LOCK_ARMED", pos.contract, mark,
            f"Gain {peak_gain:+.1%}: profit lock floor set at {pos.entry_premium * (1 + r.lock_floor_pct):.2f} "
            f"(+{r.lock_floor_pct:.0%} banked minimum)",
            f"opt-paper:{pos.contract}:{pos.entry_time}:lock",
        ))
    return events


def open_position(contract: str, underlying: str, entry_premium: float, rules: Optional[ExitRules] = None,
                  note: str = "", now: Optional[str] = None) -> tuple[PaperPosition, AlertEvent]:
    now = now or datetime.now().isoformat(timespec="seconds")
    pos = PaperPosition(contract=contract, underlying=underlying, entry_premium=entry_premium,
                        entry_time=now, rules=rules or ExitRules(), note=note)
    hs = pos.stop_levels()["hard_stop"]
    r = pos.rules
    detail = (f"Paper buy at {entry_premium:.2f}. Hard stop {hs:.2f} (-{r.stop_loss_pct:.0%}), "
              f"trail {r.trail_pct:.0%} after +{r.trail_activation_pct:.0%}, "
              f"profit lock +{r.lock_floor_pct:.0%} after +{r.lock_trigger_pct:.0%}")
    if note:
        detail += f". Signal: {note}"
    return pos, AlertEvent("ENTRY", contract, entry_premium, detail, f"opt-paper:{contract}:{now}:entry")


def format_alert(event: AlertEvent) -> tuple[str, str, str]:
    """Return (title, body, alert_type) for NotificationBuilder.build_simple_alert."""
    titles = {
        "ENTRY": ("Options entry", "info"),
        "TRAIL_ARMED": ("Trailing stop armed", "info"),
        "LOCK_ARMED": ("Profit lock armed", "success"),
        "EXIT": ("Options exit", "warning"),
    }
    label, alert_type = titles.get(event.kind, (event.kind, "info"))
    if event.kind == "EXIT" and event.exit_reason == "profit_lock":
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
            rules = ExitRules(**item.pop("rules"))
            pos = PaperPosition(rules=rules, **item)
            self.positions[pos.contract] = pos

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"positions": [asdict(p) for p in self.positions.values()]}
        self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- actions ---------------------------------------------------------
    def enter(self, contract: str, underlying: str, entry_premium: float,
              rules: Optional[ExitRules] = None, note: str = "") -> Optional[AlertEvent]:
        existing = self.positions.get(contract)
        if existing and not existing.closed:
            logger.info("[OptionsPaper] %s already open, entry ignored", contract)
            return None
        pos, event = open_position(contract, underlying, entry_premium, rules, note)
        self.positions[contract] = pos
        self._dispatch(event)
        self.save()
        return event

    def mark(self, marks: Dict[str, float]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        for contract, premium in marks.items():
            pos = self.positions.get(contract)
            if pos is None:
                continue
            events.extend(update_position(pos, premium))
        for ev in events:
            self._dispatch(ev)
        self.save()
        return events

    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if not p.closed]

    def _dispatch(self, event: AlertEvent) -> bool:
        """Send one alert. A failed channel never breaks position tracking."""
        try:
            from src.notification import NotificationBuilder, NotificationService

            notifier = self._notifier or NotificationService()
            title, body, alert_type = format_alert(event)
            text = NotificationBuilder.build_simple_alert(title=title, content=body, alert_type=alert_type)
            result = notifier.send_with_results(text, route_type="alert", dedup_key=event.dedup_key)
            return bool(getattr(result, "success", result))
        except Exception as exc:  # noqa: BLE001 - notification must not stop tracking
            logger.warning("[OptionsPaper] alert send failed for %s: %s", event.contract, exc)
            return False
