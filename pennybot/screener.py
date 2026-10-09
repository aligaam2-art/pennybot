"""Step 1: the screener.

Two stages:
  1. load_candidates()  - downloads data (slow, network). Run once per refresh.
  2. apply_filters()    - applies the rules (instant, no network), so the
                          dashboard can toggle rules without re-downloading.

The output is a ScanResult. Later modules (catalyst check, journal, alerts)
take a ScanResult as input and add their own columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from . import yahoo
from .clock import now_et, session

OTC = "OTC"


@dataclass
class ScanResult:
    candidates: pd.DataFrame           # stocks that passed, ranked
    funnel: pd.DataFrame               # how many stocks each rule removed
    scanned_at: datetime               # US/Eastern time of the data download
    mode: str                          # "intraday" or "premarket"
    settings: dict                     # the rules used
    universe_total: int | None = None  # all US stocks under max price trading today
    notes: list[str] = field(default_factory=list)


@dataclass
class RawData:
    movers: pd.DataFrame
    scanned_at: datetime
    universe_total: int | None


def load_candidates(cfg: dict) -> RawData:
    """Download today's movers and add float, market cap and relative volume."""
    s = cfg["screener"]
    df = yahoo.fetch_movers(s["max_price"], s["min_change_pct"])
    scanned_at = now_et()
    df["float"] = np.nan
    df["rvol"] = np.nan
    df["rvol_method"] = "unknown"

    # Only look up extra data for listed stocks: OTC is always excluded.
    listed = df[(df["exchange"] != OTC) & ~_is_spac_right_warrant(df)]["ticker"].tolist()
    if listed:
        fundamentals = yahoo.fetch_fundamentals(listed)
        for i, row in df[df["ticker"].isin(listed)].iterrows():
            f = fundamentals.get(row["ticker"], {})
            df.at[i, "float"] = f.get("float")
            if pd.isna(row["market_cap"]) and f.get("market_cap"):
                df.at[i, "market_cap"] = f["market_cap"]

        tod = yahoo.time_of_day_rvol(listed, s.get("rvol_lookback_days", 10))
        for i, row in df.iterrows():
            t = row["ticker"]
            if t in tod:
                df.at[i, "rvol"] = tod[t]
                df.at[i, "rvol_method"] = "same time of day"
            elif row["avg_volume_10d"] and row["avg_volume_10d"] > 0:
                df.at[i, "rvol"] = row["volume"] / row["avg_volume_10d"]
                df.at[i, "rvol_method"] = "vs 10-day avg (full day)"

    return RawData(df, scanned_at, yahoo.universe_size(s["max_price"]))


def _is_spac_right_warrant(df: pd.DataFrame) -> pd.Series:
    t = df["ticker"].fillna("")
    name = df["name"].fillna("").str.lower()
    nasdaq_suffix = (df["exchange"] == "NASDAQ") & (t.str.len() == 5) & t.str[-1].isin(list("WRU"))
    return (
        (df["quote_type"] != "EQUITY")
        | t.str.contains(r"[-.]", regex=True)
        | nasdaq_suffix
        | name.str.contains(r"acquisition corp|warrant|\bright[s]?\b|\bunits?\b", regex=True)
    )


def build_rules(df: pd.DataFrame, s: dict, mode: str) -> list[tuple[str, pd.Series]]:
    """Each rule is (label, mask of stocks that PASS). Order = cheapest/broadest first."""
    rules = [
        (f"Exchange: {', '.join(s['exchanges'])} (no OTC)",
         df["exchange"].isin(s["exchanges"])),
    ]
    if s.get("exclude_spacs_rights_warrants", True):
        rules.append(("Not a SPAC / right / warrant / unit", ~_is_spac_right_warrant(df)))
    rules += [
        (f"Price ${s['min_price']:.2f} - ${s['max_price']:.2f}",
         df["price"].between(s["min_price"], s["max_price"])),
        (f"% change >= {s['min_change_pct']:g}%",
         df["change_pct"] >= s["min_change_pct"]),
        (f"Volume >= {s['min_volume'][mode]:,.0f}",
         df["volume"] >= s["min_volume"][mode]),
    ]
    cap_ok = df["market_cap"] < s["max_market_cap"]
    if s.get("keep_unknown_market_cap", True):
        cap_ok |= df["market_cap"].isna()
    rules.append((f"Market cap < ${s['max_market_cap'] / 1e6:,.0f}M", cap_ok))
    if s.get("use_relative_volume", True):
        rules.append((f"Relative volume >= {s['min_relative_volume']:g}x",
                      (df["rvol"] >= s["min_relative_volume"]) | df["rvol"].isna()))
    if s.get("use_float", True):
        rules.append((f"Float < {s['max_float'] / 1e6:g}M (unknown kept)",
                      (df["float"] < s["max_float"]) | df["float"].isna()))
    return rules


def apply_filters(raw: RawData, s: dict, mode: str = "intraday") -> ScanResult:
    df = raw.movers
    rules = build_rules(df, s, mode)

    funnel = [{"rule": f"Start: US stocks <= ${s['max_price']:g}, up >= {s['min_change_pct']:g}% (Yahoo)",
               "remaining": len(df), "removed_at_this_step": 0, "would_remove_on_its_own": 0}]
    passing = pd.Series(True, index=df.index)
    for label, mask in rules:
        before = passing.sum()
        passing &= mask.fillna(False)
        funnel.append({"rule": label, "remaining": int(passing.sum()),
                       "removed_at_this_step": int(before - passing.sum()),
                       "would_remove_on_its_own": int((~mask.fillna(False)).sum())})

    out = df[passing].copy()
    out = out.sort_values(["rvol", "change_pct"], ascending=[False, False], na_position="last")
    out = out.reset_index(drop=True)
    out.index += 1

    notes = []
    if session(raw.scanned_at) != "regular":
        notes.append(f"US market is {session(raw.scanned_at)} - prices/volume are from the last regular session.")
    if (out["float"].isna()).any():
        notes.append(f"{out['float'].isna().sum()} stock(s) kept with unknown float.")
    if (out["rvol_method"] == "vs 10-day avg (full day)").any():
        notes.append("Some relative volumes use the 10-day daily average (no intraday history); "
                     "these read low early in the day.")

    return ScanResult(out, pd.DataFrame(funnel), raw.scanned_at, mode, s,
                      raw.universe_total, notes)
