# -*- coding: utf-8 -*-
"""Deterministic entry triggers derived from strategies/*.yaml.

The YAML strategies are natural-language instructions for the LLM agent, so
they cannot be executed directly. Each function below is a mechanical reading
of the matching YAML's core rule (a guess at its intent, see docs). A signal
is (day, right) with right "call" for bullish and "put" for bearish.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pandas as pd

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"

Signal = Tuple[date, str]
SignalFn = Callable[[pd.DataFrame], List[Signal]]


def _prep(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    for n in (5, 10, 20):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    df["vol_ma5"] = df["volume"].rolling(5).mean().shift(1)
    df["vol_ma20"] = df["volume"].rolling(20).mean().shift(1)
    df["hh20"] = df["high"].rolling(20).max().shift(1)
    df["ll20"] = df["low"].rolling(20).min().shift(1)
    return df


def ma_golden_cross(bars: pd.DataFrame) -> List[Signal]:
    """MA5 crosses MA10 with volume > 1.2x 5-day average and bias < 5%."""
    df = _prep(bars)
    out: List[Signal] = []
    for i in range(1, len(df)):
        p, r = df.iloc[i - 1], df.iloc[i]
        if pd.isna(r.ma10) or pd.isna(r.vol_ma5) or r.vol_ma5 <= 0:
            continue
        vol_ok = r.volume > 1.2 * r.vol_ma5
        bias = abs(r.close / r.ma5 - 1)
        if p.ma5 <= p.ma10 and r.ma5 > r.ma10 and vol_ok and bias < 0.05:
            out.append((r.date, "call"))
        elif p.ma5 >= p.ma10 and r.ma5 < r.ma10 and vol_ok and bias < 0.05:
            out.append((r.date, "put"))
    return out


def volume_breakout(bars: pd.DataFrame) -> List[Signal]:
    """Close through the prior 20-day high (low) on volume > 2x 5-day average."""
    df = _prep(bars)
    out: List[Signal] = []
    for _, r in df.iterrows():
        if pd.isna(r.hh20) or pd.isna(r.vol_ma5) or r.vol_ma5 <= 0:
            continue
        if r.volume > 2.0 * r.vol_ma5:
            if r.close > r.hh20:
                out.append((r.date, "call"))
            elif r.close < r.ll20:
                out.append((r.date, "put"))
    return out


def bull_trend(bars: pd.DataFrame) -> List[Signal]:
    """First day MA5 >= MA10 >= MA20 with MA20 rising (mirror for puts)."""
    df = _prep(bars)
    df["ma20_slope"] = df["ma20"].diff(3)
    bull = (df.ma5 >= df.ma10) & (df.ma10 >= df.ma20) & (df.ma20_slope > 0)
    bear = (df.ma5 <= df.ma10) & (df.ma10 <= df.ma20) & (df.ma20_slope < 0)
    out: List[Signal] = []
    for i in range(1, len(df)):
        if bull.iloc[i] and not bull.iloc[i - 1]:
            out.append((df.date.iloc[i], "call"))
        elif bear.iloc[i] and not bear.iloc[i - 1]:
            out.append((df.date.iloc[i], "put"))
    return out


SIGNALS: Dict[str, SignalFn] = {
    "ma_golden_cross": ma_golden_cross,
    "volume_breakout": volume_breakout,
    "bull_trend": bull_trend,
}


def get_signal(name: str) -> SignalFn:
    if name not in SIGNALS:
        raise ValueError(f"No mechanical trigger for '{name}'. Supported: {sorted(SIGNALS)}")
    if not (STRATEGIES_DIR / f"{name}.yaml").exists():
        raise ValueError(f"strategies/{name}.yaml not found")
    return SIGNALS[name]
