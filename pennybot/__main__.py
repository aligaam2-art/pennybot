"""Terminal version of the screener:  python -m pennybot"""
from __future__ import annotations

import argparse

import pandas as pd

from . import output
from .catalyst import check_catalysts
from .config import load_config
from .screener import apply_filters, load_candidates
from .selection import rank


def main() -> None:
    parser = argparse.ArgumentParser(description="Penny stock screener")
    parser.add_argument("--mode", choices=["intraday", "premarket"], default="intraday")
    parser.add_argument("--no-catalysts", action="store_true", help="screener only (skip news/SEC check)")
    args = parser.parse_args()

    cfg = load_config()
    print("Downloading today's movers from Yahoo Finance...")
    result = apply_filters(load_candidates(cfg), cfg["screener"], args.mode)
    local = output.local_time(result, cfg["display_timezone"])

    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 70)
    print(f"\nScan at {local:%a %d %b %Y %H:%M} ({cfg['display_timezone']})  |  mode: {args.mode}")
    if result.universe_total:
        print(f"US stocks under ${cfg['screener']['max_price']:g} trading today: {result.universe_total:,}")
    print("\nFILTER LOG")
    print(result.funnel.to_string(index=False))
    print(f"\nRESULTS: {len(result.candidates)} stocks (ranked by relative volume, then % change)")
    if len(result.candidates):
        print(output.display_table(result).drop(columns=["Name"]).to_string())
    for note in result.notes:
        print(f"note: {note}")

    ranked = None
    if len(result.candidates) and not args.no_catalysts:
        print("\nChecking news and SEC filings...")
        catalysts = check_catalysts(result.candidates["ticker"].tolist(), cfg)
        ranked = rank(result.candidates, catalysts, cfg)
        print("\nSELECTION (day trading score, best first)")
        for i, r in ranked.iterrows():
            print(f"\n{i:>2}. {r['ticker']:<6} {r['grade']:<5} score {r['score']:>3}   "
                  f"${r['price']:.2f}  +{r['change_pct']:.0f}%")
            if r["headline"]:
                print(f"    news: {r['headline'][:110]}")
            print(f"    why:  {r['reasons']}")

    csv_path, log_path = output.save(result, cfg, ranked)
    print(f"\nSaved: {csv_path}\n       {log_path}")


if __name__ == "__main__":
    main()
