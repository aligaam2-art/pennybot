"""Market structure from swing highs and lows.

A swing high is a candle whose high is above the `strength` candles on each
side (swing low: low below them). A swing is only known once those candles
on its right have finished, so nothing here looks into the future.

Structure:
  bullish            higher high + higher low (HH + HL)
  break of structure higher low, then a close above the last swing high
  bearish            lower high + lower low (LH + LL)
  broken             price closed below the last swing low
  range              anything else
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

BULLISH = ("bullish", "break of structure")


@dataclass
class Swing:
    ts: pd.Timestamp
    price: float
    kind: str      # high / low
    label: str     # HH, LH, HL, LL or "" for the first one


def find_swings(df: pd.DataFrame, strength: int = 2) -> list[Swing]:
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    swings: list[Swing] = []
    last = {"high": None, "low": None}
    for j in range(strength, len(df) - strength):
        left, right = slice(j - strength, j), slice(j + 1, j + strength + 1)
        for kind, arr, better in (("high", highs, lambda a, b: a > b), ("low", lows, lambda a, b: a < b)):
            v = arr[j]
            if all(better(v, x) for x in arr[left]) and all(not better(x, v) for x in arr[right]):
                prev = last[kind]
                if prev is None:
                    label = ""
                elif kind == "high":
                    label = "HH" if v > prev else "LH"
                else:
                    label = "HL" if v > prev else "LL"
                swings.append(Swing(df.index[j], float(v), kind, label))
                last[kind] = v
    return swings


def structure_state(df: pd.DataFrame, sp: dict) -> tuple[str, list[Swing]]:
    """Structure as of the last finished candle in `df`."""
    swings = find_swings(df, sp.get("swing_strength", 2))
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "unclear (not enough swings yet)", swings
    close = float(df["close"].iloc[-1])
    hh, hl = highs[-1].price > highs[-2].price, lows[-1].price > lows[-2].price
    if close < lows[-1].price:
        return "broken (closed below last swing low)", swings
    if hh and hl:
        return "bullish", swings
    if hl and sp.get("allow_break_of_structure", True) and close > highs[-1].price:
        return "break of structure", swings
    if not hh and not hl:
        return "bearish", swings
    return "range", swings


def is_bullish(state: str) -> bool:
    return state.startswith(BULLISH)
