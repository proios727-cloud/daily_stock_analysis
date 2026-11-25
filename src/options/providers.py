# -*- coding: utf-8 -*-
"""Historical option EOD providers: ThetaData (primary), Alpha Vantage (fallback).

Both return closing NBBO (bid/ask) per contract per day. Network calls live
only here so the engine stays pure and testable with a fake provider.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

import requests

from .models import OptionContract, OptionQuote

logger = logging.getLogger(__name__)

_DEFAULT_THETA_URL = "http://127.0.0.1:25503/v3"  # local Theta Terminal v3 (guess: default port)
_AV_URL = "https://www.alphavantage.co/query"


class OptionDataProvider(Protocol):
    name: str

    def chain(self, symbol: str, day: date, right: str, max_dte: int) -> List[OptionQuote]:
        """All contracts of one right quoted on `day` with expiry within max_dte."""

    def history(self, contract: OptionContract, start: date, end: date) -> List[OptionQuote]:
        """Daily quotes for one contract between start and end (inclusive)."""


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.strptime(text, "%Y-%m-%d").date()


def _f(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "", "None") else float(value)
    except (TypeError, ValueError):
        return None


def _valid(bid: Optional[float], ask: Optional[float]) -> bool:
    return bid is not None and ask is not None and ask > 0 and bid >= 0 and ask >= bid


# --------------------------------------------------------------------------- ThetaData
def parse_theta_eod(payload: Any) -> List[OptionQuote]:
    """Parse ThetaData v3 option EOD JSON ({"response": [{contract, data: [...]}, ...]})."""
    groups = payload.get("response", []) if isinstance(payload, dict) else payload or []
    quotes: List[OptionQuote] = []
    for group in groups:
        c = group.get("contract", {})
        contract = OptionContract(
            symbol=str(c.get("symbol")),
            expiration=_to_date(c.get("expiration")),
            strike=float(c.get("strike")),
            right=str(c.get("right", "")).lower(),
        )
        for row in group.get("data", []):
            bid, ask = _f(row.get("bid")), _f(row.get("ask"))
            stamp = row.get("created") or row.get("last_trade") or row.get("date")
            if not _valid(bid, ask) or stamp is None:
                continue
            vol = row.get("volume")
            quotes.append(OptionQuote(contract, _to_date(stamp), bid, ask, int(vol) if vol is not None else None))
    return quotes


class ThetaDataProvider:
    name = "thetadata"

    def __init__(self, base_url: Optional[str] = None, timeout: int = 60, session: Optional[requests.Session] = None):
        self.base_url = (base_url or os.getenv("THETADATA_BASE_URL") or _DEFAULT_THETA_URL).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _eod(self, params: Dict[str, Any]) -> List[OptionQuote]:
        resp = self.session.get(
            f"{self.base_url}/option/history/eod", params={**params, "format": "json"}, timeout=self.timeout
        )
        resp.raise_for_status()
        return parse_theta_eod(resp.json())

    def chain(self, symbol: str, day: date, right: str, max_dte: int) -> List[OptionQuote]:
        d = day.strftime("%Y%m%d")
        return self._eod({"symbol": symbol, "expiration": "*", "right": right,
                          "start_date": d, "end_date": d, "max_dte": max_dte})

    def history(self, contract: OptionContract, start: date, end: date) -> List[OptionQuote]:
        return self._eod({
            "symbol": contract.symbol,
            "expiration": contract.expiration.strftime("%Y%m%d"),
            "strike": f"{contract.strike:.3f}",
            "right": contract.right,
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        })


# --------------------------------------------------------------------------- Alpha Vantage
def parse_av_chain(payload: Dict[str, Any], day: date) -> List[OptionQuote]:
    """Parse Alpha Vantage HISTORICAL_OPTIONS JSON ({"data": [...]})."""
    quotes: List[OptionQuote] = []
    for row in payload.get("data", []) or []:
        bid, ask = _f(row.get("bid")), _f(row.get("ask"))
        if not _valid(bid, ask):
            continue
        contract = OptionContract(
            symbol=str(row.get("symbol")),
            expiration=_to_date(row.get("expiration")),
            strike=float(row.get("strike")),
            right=str(row.get("type", "")).lower(),
        )
        vol, oi = _f(row.get("volume")), _f(row.get("open_interest"))
        quotes.append(OptionQuote(contract, _to_date(row.get("date") or day), bid, ask,
                                  int(vol) if vol is not None else None, _f(row.get("delta")),
                                  int(oi) if oi is not None else None, _f(row.get("implied_volatility"))))
    return quotes


class AlphaVantageOptionsProvider:
    """One HISTORICAL_OPTIONS call per symbol per day (cached). Slow on free keys."""

    name = "alphavantage"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 30, session: Optional[requests.Session] = None):
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise ValueError("ALPHAVANTAGE_API_KEY is not set")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._cache: Dict[Tuple[str, date], List[OptionQuote]] = {}

    def _day(self, symbol: str, day: date) -> List[OptionQuote]:
        key = (symbol, day)
        if key not in self._cache:
            resp = self.session.get(_AV_URL, params={
                "function": "HISTORICAL_OPTIONS", "symbol": symbol,
                "date": day.isoformat(), "datatype": "json", "apikey": self.api_key,
            }, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            if "data" not in payload:
                raise RuntimeError(f"Alpha Vantage: {payload.get('Information') or payload.get('message') or 'no data'}")
            self._cache[key] = parse_av_chain(payload, day)
        return self._cache[key]

    def chain(self, symbol: str, day: date, right: str, max_dte: int) -> List[OptionQuote]:
        limit = day + timedelta(days=max_dte)
        return [q for q in self._day(symbol, day) if q.contract.right == right and q.contract.expiration <= limit]

    def history(self, contract: OptionContract, start: date, end: date) -> List[OptionQuote]:
        out: List[OptionQuote] = []
        day = start
        while day <= min(end, contract.expiration):
            if day.weekday() < 5:
                out.extend(q for q in self._day(contract.symbol, day) if q.contract == contract)
            day += timedelta(days=1)
        return out


# --------------------------------------------------------------------------- snapshot
class ThetaSnapshotProvider:
    """Replays saved ThetaData option EOD JSON from ``<dir>/<SYMBOL>_options.json``.

    For chains pulled outside the Theta Terminal (for example through the
    ThetaData connector) so the picker and backtester can run offline.
    """

    name = "thetadata-snapshot"

    def __init__(self, directory: str):
        self.directory = directory

    def _load(self, symbol: str) -> List[OptionQuote]:
        import json

        path = os.path.join(self.directory, f"{symbol.upper()}_options.json")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, encoding="utf-8") as fh:
            return parse_theta_eod(json.load(fh))

    def chain(self, symbol: str, day: date, right: str, max_dte: int) -> List[OptionQuote]:
        limit = day + timedelta(days=max_dte)
        return [q for q in self._load(symbol)
                if q.day == day and q.contract.right == right and q.contract.expiration <= limit]

    def history(self, contract: OptionContract, start: date, end: date) -> List[OptionQuote]:
        return [q for q in self._load(contract.symbol) if q.contract == contract and start <= q.day <= end]


# --------------------------------------------------------------------------- fallback
class FallbackProvider:
    """Try providers in order; a failure or an empty result moves to the next."""

    def __init__(self, providers: Sequence[OptionDataProvider]):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = list(providers)
        self.name = "+".join(p.name for p in self.providers)

    def _try(self, method: str, *args) -> List[OptionQuote]:
        errors = []
        for p in self.providers:
            try:
                result = getattr(p, method)(*args)
                if result:
                    return result
                errors.append(f"{p.name}: empty")
            except Exception as exc:  # one source failing must not stop the run
                errors.append(f"{p.name}: {exc}")
                logger.warning("options provider %s.%s failed: %s", p.name, method, exc)
        logger.info("options %s returned nothing (%s)", method, "; ".join(errors))
        return []

    def chain(self, symbol, day, right, max_dte):
        return self._try("chain", symbol, day, right, max_dte)

    def history(self, contract, start, end):
        return self._try("history", contract, start, end)


def default_provider() -> FallbackProvider:
    providers: List[OptionDataProvider] = [ThetaDataProvider()]
    if os.getenv("ALPHAVANTAGE_API_KEY"):
        providers.append(AlphaVantageOptionsProvider())
    return FallbackProvider(providers)
