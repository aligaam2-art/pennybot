"""Candlestick patterns (pure functions on finished bars).

Each function looks at the LAST bars of `hist` and returns True/False.
Bars need columns open, high, low, close, volume.
"""
from __future__ import annotations

import pandas as pd


def body(bar: pd.Series) -> float:
    return abs(bar["close"] - bar["open"])


def is_green(bar: pd.Series) -> bool:
    return bar["close"] > bar["open"]


def is_red(bar: pd.Series) -> bool:
    return bar["close"] < bar["open"]


def bullish_engulfing(hist: pd.DataFrame, p: dict) -> bool:
    """Red candle, then a green candle whose body covers the red body."""
    if len(hist) < 2:
        return False
    c1, c2 = hist.iloc[-2], hist.iloc[-1]
    if not (is_red(c1) and is_green(c2)):
        return False
    engulfs = c2["open"] <= c1["close"] and c2["close"] >= c1["open"] and body(c2) > body(c1)
    vol_ok = (not p.get("require_volume", True)) or c2["volume"] >= c1["volume"]
    return engulfs and vol_ok


def morning_star(hist: pd.DataFrame, p: dict) -> bool:
    """Big red candle, small 'star' candle, green candle closing above the red body's middle."""
    if len(hist) < 3 + 10:
        return False
    c1, c2, c3 = hist.iloc[-3], hist.iloc[-2], hist.iloc[-1]
    avg_body = (hist["close"] - hist["open"]).abs().iloc[-13:-3].mean()
    big_red = is_red(c1) and body(c1) >= p.get("big_body_mult", 1.0) * avg_body and body(c1) > 0
    small_star = body(c2) <= p.get("star_max_body_pct", 50) / 100 * body(c1)
    star_low = c2["low"] <= min(c1["low"], c3["low"]) * 1.002      # star is (about) the lowest point
    recovery = is_green(c3) and c3["close"] >= (c1["open"] + c1["close"]) / 2
    vol_ok = (not p.get("require_volume", True)) or c3["volume"] >= c2["volume"]
    return big_red and small_star and star_low and recovery and vol_ok
