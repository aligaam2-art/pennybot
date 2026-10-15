"""Free market data from Yahoo Finance (via yfinance).

Everything data-source specific lives here, so a different provider
(Polygon, Alpaca, ...) can replace this module later without touching
the screener logic.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery as Q

from .clock import ET, now_et
from .config import ROOT

# Yahoo exchange codes -> names used in config.yaml
EXCHANGE_NAMES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "NYSE American",
    "PCX": "NYSE Arca", "BTS": "Cboe",
    "PNK": "OTC", "OID": "OTC", "OQB": "OTC", "OQX": "OTC", "OEM": "OTC", "OGM": "OTC",
}

CACHE_DIR = ROOT / "cache"
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # hide "possibly delisted" noise
PAGE_SIZE = 250


def fetch_movers(max_price: float, min_change_pct: float) -> pd.DataFrame:
    """All US stocks at or under max_price that are up at least min_change_pct today.

    Price and % change are applied by Yahoo's screener (so we don't have to
    download all ~10,000 US stocks under $5). Every other rule is applied locally.
    """
    query = Q("and", [
        Q("eq", ["region", "us"]),
        Q("lte", ["intradayprice", max_price]),
        Q("gte", ["percentchange", min_change_pct]),
        Q("gt", ["dayvolume", 0]),
    ])
    quotes, offset = [], 0
    while True:
        page = yf.screen(query, size=PAGE_SIZE, offset=offset,
                         sortField="percentchange", sortAsc=False)
        quotes += page.get("quotes", [])
        offset += PAGE_SIZE
        if offset >= page.get("total", 0) or not page.get("quotes"):
            break

    rows = [{
        "ticker": q["symbol"],
        "name": q.get("longName") or q.get("shortName") or "",
        "price": q.get("regularMarketPrice"),
        "change_pct": q.get("regularMarketChangePercent"),
        "volume": q.get("regularMarketVolume"),
        "day_high": q.get("regularMarketDayHigh"),
        "bid": q.get("bid"),
        "ask": q.get("ask"),
        "avg_volume_10d": q.get("averageDailyVolume10Day"),
        "market_cap": q.get("marketCap"),
        "exchange_code": q.get("exchange"),
        "exchange": EXCHANGE_NAMES.get(q.get("exchange"), q.get("fullExchangeName") or q.get("exchange")),
        "quote_type": q.get("quoteType"),
        "delay_min": q.get("exchangeDataDelayedBy", 0),
        "market_state": q.get("marketState"),
    } for q in quotes]
    return pd.DataFrame(rows).drop_duplicates("ticker").reset_index(drop=True)


def universe_size(max_price: float) -> int | None:
    """How many US stocks under max_price traded today (context for the filter log)."""
    try:
        query = Q("and", [Q("eq", ["region", "us"]), Q("lte", ["intradayprice", max_price]),
                          Q("gt", ["dayvolume", 0])])
        return yf.screen(query, size=1).get("total")
    except Exception:
        return None


# ---------------------------------------------------------------- fundamentals

def _cache_file() -> Path:
    return CACHE_DIR / f"fundamentals_{date.today().isoformat()}.json"


def fetch_fundamentals(tickers: list[str]) -> dict[str, dict]:
    """Float and market cap per ticker (missing values stay None). Cached for the day."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_file()
    cache = json.loads(path.read_text()) if path.exists() else {}
    missing = [t for t in tickers if t not in cache]

    def one(t: str) -> tuple[str, dict]:
        try:
            info = yf.Ticker(t).info
            return t, {"float": info.get("floatShares"), "market_cap": info.get("marketCap"),
                       "shares_out": info.get("sharesOutstanding")}
        except Exception:
            return t, {"float": None, "market_cap": None, "shares_out": None}

    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            cache.update(dict(pool.map(one, missing)))
        path.write_text(json.dumps(cache))
    return {t: cache.get(t, {}) for t in tickers}


# ---------------------------------------------------------------- relative volume

def time_of_day_rvol(tickers: list[str], lookback_days: int) -> dict[str, float]:
    """Today's volume so far divided by the average volume traded up to the
    same time of day over the previous `lookback_days` sessions (5-minute bars).

    Tickers without enough intraday history are left out; the caller falls
    back to the 10-day daily average for those.
    """
    if not tickers:
        return {}
    def download(symbols: list[str], threads: bool) -> pd.DataFrame:
        return yf.download(symbols, period=f"{lookback_days + 7}d", interval="5m",
                           prepost=False, group_by="ticker", progress=False,
                           threads=threads, auto_adjust=False)

    def volumes(data: pd.DataFrame, symbols: list[str]) -> dict[str, pd.Series]:
        out = {}
        for t in symbols:
            try:
                vol = data[t]["Volume"].dropna()
            except KeyError:
                continue
            if not vol.empty and vol.index.tz is not None:     # skip empty/naive results
                out[t] = vol
        return out

    vols = volumes(download(tickers, threads=True), tickers)
    # Parallel downloads occasionally fail ("database is locked"); retry those one by one.
    failed = [t for t in tickers if t not in vols]
    if failed:
        vols.update(volumes(download(failed, threads=False), failed))
    today = now_et().date()
    out: dict[str, float] = {}
    for t, vol in vols.items():
        vol = vol.copy()
        vol.index = vol.index.tz_convert(ET)
        days = vol.index.date
        today_vol = vol[days == today]
        if today_vol.empty:
            continue
        cutoff = today_vol.index[-1].time()
        prior_days = sorted({d for d in days if d < today})[-lookback_days:]
        same_time = [vol[(days == d) & (vol.index.time <= cutoff)].sum() for d in prior_days]
        same_time = [v for v in same_time if v > 0]
        if len(same_time) >= 3:
            out[t] = float(today_vol.sum() / (sum(same_time) / len(same_time)))
    return out
