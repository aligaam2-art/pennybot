"""1-minute price bars, indicators (VWAP, EMAs) and key chart levels.

Bars come from Yahoo (free, about 1 minute behind, premarket included,
last 8 days only). Finished days are saved under data/bars/ so the
backtest has more history every day you use it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time

import numpy as np
import pandas as pd
import yfinance as yf

from .clock import ET, REGULAR_CLOSE, REGULAR_OPEN, now_et
from .config import ROOT

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------- download + storage

def _store_dir(cfg: dict):
    return ROOT / cfg.get("backtest", {}).get("data_folder", "data/bars")


def download_bars(tickers: list[str], days: int = 1) -> dict[str, pd.DataFrame]:
    """1-minute bars (premarket + regular + after hours) for the last `days` sessions."""
    if not tickers:
        return {}
    data = yf.download(tickers, period=f"{min(days, 8)}d", interval="1m", prepost=True,
                       group_by="ticker", progress=False, threads=True, auto_adjust=False)
    out = {}
    for t in tickers:
        try:
            df = data[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if df.empty:
            continue
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
        df.index = df.index.tz_convert(ET)
        out[t] = df
    return out


def save_finished_days(bars: dict[str, pd.DataFrame], cfg: dict) -> None:
    today = now_et().date()
    for t, df in bars.items():
        folder = _store_dir(cfg) / t
        for d, day in df.groupby(df.index.date):
            path = folder / f"{d.isoformat()}.parquet"
            if d < today and not path.exists():
                folder.mkdir(parents=True, exist_ok=True)
                day.to_parquet(path)


def load_stored(ticker: str, cfg: dict) -> pd.DataFrame:
    """Saved bars from the dashboard (data/bars) and the automatic journal (journal/bars)."""
    files = []
    for folder in (_store_dir(cfg) / ticker, ROOT / "journal" / "bars" / ticker):
        if folder.exists():
            files += sorted(folder.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat(pd.read_parquet(f) for f in files)
    df.index = pd.to_datetime(df.index).tz_convert(ET)
    return df[~df.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------- indicators

def resample(day: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Combine 1-minute bars into `minutes`-minute candles (aligned to :00, :05, ... so 9:30 starts a candle)."""
    if minutes <= 1 or day.empty:
        return day
    out = day.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["close"])


def add_indicators(day: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """VWAP and fast/slow EMAs for ONE day of bars.

    VWAP starts with the first premarket bar, or at the 9:30 open if
    chart.vwap_includes_premarket is false (premarket bars then use their own running VWAP).
    """
    c = cfg.get("chart", {})
    df = day.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    session = pd.Series(0, index=df.index)
    if not c.get("vwap_includes_premarket", True):
        session = pd.Series((df.index.time >= REGULAR_OPEN).astype(int), index=df.index)
    pv = (typical * df["volume"]).groupby(session).cumsum()
    cum_vol = df["volume"].groupby(session).cumsum().replace(0, np.nan)
    df["vwap"] = (pv / cum_vol).ffill().fillna(df["close"])
    df["ema_fast"] = df["close"].ewm(span=c.get("ema_fast", 9), adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=c.get("ema_slow", 20), adjust=False).mean()
    return df


def split_days(df: pd.DataFrame) -> dict[date, pd.DataFrame]:
    return {d: g for d, g in df.groupby(df.index.date)}


def regular_close(day: pd.DataFrame) -> float | None:
    reg = day[(day.index.time >= REGULAR_OPEN) & (day.index.time < REGULAR_CLOSE)]
    return float(reg["close"].iloc[-1]) if len(reg) else None


# ---------------------------------------------------------------- key levels

@dataclass
class Levels:
    prev_close: float | None
    premarket_high: float | None
    high_of_day: float
    low_of_day: float
    round_numbers: list[float]

    def overhead(self, price: float) -> list[tuple[str, float]]:
        """Key levels above `price`, nearest first."""
        named = [("premarket high", self.premarket_high), ("high of day", self.high_of_day)]
        named += [(f"${r:.2f} round number", r) for r in self.round_numbers]
        return sorted([(n, v) for n, v in named if v and v > price], key=lambda x: x[1])


def key_levels(day: pd.DataFrame, prev_close: float | None) -> Levels:
    pre = day[day.index.time < REGULAR_OPEN]
    price = float(day["close"].iloc[-1])
    step = 0.5 if price >= 1 else 0.1
    base = np.floor(price / step) * step
    rounds = [round(base + k * step, 2) for k in range(-1, 3) if base + k * step > 0]
    return Levels(prev_close, float(pre["high"].max()) if len(pre) else None,
                  float(day["high"].max()), float(day["low"].min()), rounds)


def trend_text(df: pd.DataFrame) -> str:
    """Plain-English read of the latest bars."""
    last = df.iloc[-1]
    parts = []
    above_vwap = last["close"] > last["vwap"]
    parts.append("above VWAP" if above_vwap else "below VWAP")
    ema_up = last["ema_fast"] > last["ema_slow"]
    parts.append("9 EMA above 20 EMA" if ema_up else "9 EMA below 20 EMA")
    lows = df["low"].tail(15)
    if len(lows) >= 15:
        a, b, c = lows.iloc[:5].min(), lows.iloc[5:10].min(), lows.iloc[10:].min()
        parts.append("higher lows" if a < b < c else "lower lows" if a > b > c else "choppy lows")
    if above_vwap and ema_up and "higher lows" in parts:
        verdict = "uptrend"
    elif not above_vwap and not ema_up:
        verdict = "downtrend - no long setups"
    else:
        verdict = "mixed"
    return ", ".join(parts) + f": {verdict}"


def is_regular(ts: pd.Timestamp, first_minutes: int = 0, until: time = REGULAR_CLOSE) -> bool:
    start = (pd.Timestamp.combine(ts.date(), REGULAR_OPEN) + pd.Timedelta(minutes=first_minutes)).time()
    return start <= ts.time() < until
