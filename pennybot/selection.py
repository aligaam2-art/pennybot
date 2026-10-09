"""Phase 2b: selection - score each screener result for day trading (0-100).

The score is a study aid built from the rules in config.yaml (selection:),
not a buy signal. Every point is explained in plain English in `reasons`.
"""
from __future__ import annotations

import pandas as pd

from .catalyst import TAG_ICON, CatalystInfo

FLAG_TEXT = {
    "offering_filing": "share-sale filing (S-1/S-3/424B)",
    "unregistered_sale": "private share sale (8-K 3.02)",
    "delisting_notice": "delisting notice (8-K 3.01)",
    "reverse_split_signal": "shareholder vote/info statement (possible reverse split)",
}


def _tier(value, table, above: bool) -> int | None:
    """First [threshold, points] row that matches. above=True: value >= threshold."""
    if value is None or pd.isna(value):
        return None
    for threshold, pts in table:
        if (value >= threshold) if above else (value < threshold):
            return pts
    return None


def _age(hours: float) -> str:
    return f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"


def score_stock(row: pd.Series, info: CatalystInfo | None, sel: dict) -> tuple[int, list[str], dict]:
    p = sel["points"]
    fresh_h, recent_h = sel["fresh_news_hours"], sel["recent_news_hours"]
    score, reasons = 0, []

    # --- catalyst
    cat = {"tag": "none", "headline": "", "age": None, "link": ""}
    best = info.best_headline(fresh_h, recent_h) if info else None
    if best:
        cat = {"tag": best.tag, "headline": best.title, "age": best.age_hours, "link": best.link}
        fresh, recent = best.age_hours <= fresh_h, best.age_hours <= recent_h
        key = {("strong", True): "strong_fresh", ("weak", True): "weak_fresh"}.get((best.tag, fresh))
        if key is None and recent:
            key = {"strong": "strong_recent", "danger": "danger_fresh"}.get(best.tag)
        if key:
            score += p["catalyst"][key]
            reasons.append(f"{TAG_ICON[best.tag]} {best.tag} news {_age(best.age_hours)} "
                           f"({p['catalyst'][key]:+d})")
        elif fresh:
            reasons.append(f"⚪ news {_age(best.age_hours)}, type unclear - read it (+0)")
        else:
            cat["tag"] = "none"
            reasons.append(f"⚪ no fresh news (latest {_age(best.age_hours)}) - "
                           "moving without a clear reason = higher risk")
    else:
        reasons.append("⚪ no news found - moving without a clear reason = higher risk")

    # --- relative volume
    pts = _tier(row["rvol"], p["rel_volume_at_least"], above=True)
    if pts:
        score += pts
        reasons.append(f"rel volume {row['rvol']:.0f}x ({pts:+d})")

    # --- float
    pts = _tier(row["float"], p["float_under"], above=False)
    if pts:
        score += pts
        reasons.append(f"float {row['float'] / 1e6:.1f}M ({pts:+d})")
    elif pd.isna(row["float"]):
        reasons.append("float unknown (+0)")

    # --- % change
    pts = _tier(row["change_pct"], p["change_pct_at_least"], above=True)
    if pts:
        score += pts
        txt = " - already very extended" if row["change_pct"] >= 100 else ""
        reasons.append(f"up {row['change_pct']:.0f}%{txt} ({pts:+d})")

    # --- distance from high of day (holding gains vs fading)
    high, price = row.get("day_high"), row["price"]
    if high and not pd.isna(high) and high > 0:
        off = (high - price) / high * 100
        pts = _tier(off, p["off_high_pct_within"], above=False)
        if pts:
            score += pts
            word = "holding near" if off < 5 else "pulled back from" if pts > 0 else "fading from"
            reasons.append(f"{word} day high ({off:.0f}% below, {pts:+d})")

    # --- spread
    bid, ask = row.get("bid"), row.get("ask")
    if bid and ask and bid > 0 and ask > bid:
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        pts = _tier(spread, p["spread_pct_over"], above=True)
        if pts:
            score += pts
            reasons.append(f"wide spread {spread:.1f}% ({pts:+d})")

    # --- dilution red flags from SEC filings
    if info and info.flags:
        d = p["dilution"]
        penalty = max(sum(d[f] for f in info.flags), d["max_penalty"])
        score += penalty
        reasons.append("🔴 " + ", ".join(FLAG_TEXT[f] for f in sorted(info.flags)) + f" ({penalty:+d})")

    return max(0, min(100, score)), reasons, cat


def grade(score: int, grades: dict) -> str:
    for g in ("A", "B", "C"):
        if score >= grades[g]:
            return g
    return "Avoid"


def rank(candidates: pd.DataFrame, catalysts: dict[str, CatalystInfo], cfg: dict) -> pd.DataFrame:
    """Add score, grade, reasons and catalyst columns; sort best first."""
    sel = cfg["selection"]
    df = candidates.copy()
    rows = []
    for _, row in df.iterrows():
        info = catalysts.get(row["ticker"])
        s, reasons, cat = score_stock(row, info, sel)
        rows.append({
            "score": s, "grade": grade(s, sel["grades"]), "reasons": " · ".join(reasons),
            "catalyst": f"{TAG_ICON[cat['tag']]} {cat['tag']}", "headline": cat["headline"],
            "news_age_h": cat["age"], "news_link": cat["link"],
            "dilution_flags": ", ".join(FLAG_TEXT[f] for f in sorted(info.flags)) if info else "",
        })
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    df = df.sort_values(["score", "rvol"], ascending=[False, False], na_position="last").reset_index(drop=True)
    df.index += 1
    return df
