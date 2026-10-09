"""Turn a ScanResult into a readable table, a CSV and a filter log."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import ROOT
from .screener import ScanResult

COLUMNS = {
    "ticker": "Ticker", "price": "Price", "change_pct": "% Change", "volume": "Volume",
    "rvol": "Rel Vol", "float": "Float", "market_cap": "Market Cap", "exchange": "Exchange",
    "rvol_method": "Rel Vol method", "name": "Name",
}


def human(n) -> str:
    if pd.isna(n):
        return "unknown"
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.2f}{unit}"
    return f"{n:.0f}"


def display_table(result: ScanResult) -> pd.DataFrame:
    """Human-friendly copy of the candidates (strings, for the terminal)."""
    df = result.candidates[list(COLUMNS)].rename(columns=COLUMNS)
    return df.assign(**{
        "Price": df["Price"].map(lambda p: f"${p:.4f}" if p < 1 else f"${p:.2f}"),
        "% Change": df["% Change"].map(lambda c: f"+{c:.1f}%"),
        "Volume": df["Volume"].map(human),
        "Rel Vol": df["Rel Vol"].map(lambda r: "unknown" if pd.isna(r) else f"{r:.1f}x"),
        "Float": df["Float"].map(human),
        "Market Cap": df["Market Cap"].map(human),
    })


def local_time(result: ScanResult, display_tz: str):
    return result.scanned_at.astimezone(ZoneInfo(display_tz))


SELECTION_COLUMNS = ["grade", "score", "catalyst", "headline", "news_age_h", "dilution_flags", "reasons",
                     "news_link"]


def save(result: ScanResult, cfg: dict, ranked: pd.DataFrame | None = None) -> tuple[Path, Path]:
    """Save the candidates CSV and the filter log; returns both paths.

    If `ranked` (output of selection.rank) is given, the CSV is in score order
    and includes the catalyst/selection columns.
    """
    folder = ROOT / cfg.get("output", {}).get("folder", "output")
    folder.mkdir(exist_ok=True)
    local = local_time(result, cfg["display_timezone"])
    stem = f"{local:%Y-%m-%d_%H%M}_{result.mode}"

    csv_path = folder / f"{stem}.csv"
    if ranked is not None:
        out = ranked[SELECTION_COLUMNS[:3] + list(COLUMNS) + SELECTION_COLUMNS[3:]].copy()
    else:
        out = result.candidates[list(COLUMNS)].copy()
    out.insert(0, "rank", out.index)
    out.insert(1, "scanned_at_local", f"{local:%Y-%m-%d %H:%M} ({cfg['display_timezone']})")
    out["float"] = out["float"].astype("object").where(out["float"].notna(), "unknown")
    out.to_csv(csv_path, index=False)

    log_path = folder / f"{stem}_filterlog.csv"
    result.funnel.to_csv(log_path, index=False)
    return csv_path, log_path
