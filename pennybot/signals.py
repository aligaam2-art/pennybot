"""Phase 3: entry signals and backtest on 1-minute bars.

One engine is used both live and for the backtest, so the backtest tests
exactly the rules you see on the dashboard:

  * At each bar, look ONLY at finished bars before it and find setups
    (bull flag, high-of-day breakout, VWAP reclaim). A setup has an entry
    (trigger) price, a stop and a target.
  * If the next bar trades through the entry price, the signal TRIGGERS.
  * Backtest: after a trigger, follow the bars until the stop, the target
    or the end-of-day exit, and record the result in R (1R = the risk).

Long trades only. All settings live in config.yaml (signals:, backtest:).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import time

import pandas as pd

from . import bars as B
from . import patterns as P
from . import structure as M
from .clock import now_et

SETUP_NAMES = {"vwap_engulfing": "VWAP + bullish engulfing", "vwap_morning_star": "VWAP + morning star",
               "bull_flag": "Bull flag", "hod_breakout": "High-of-day breakout",
               "vwap_reclaim": "VWAP reclaim"}
PATTERNS = {"vwap_engulfing": (P.bullish_engulfing, 2), "vwap_morning_star": (P.morning_star, 3)}


@dataclass
class Setup:
    kind: str
    entry: float
    stop: float
    target: float
    note: str
    bars_left: int = 1             # how many candles the entry stays valid

    @property
    def risk(self) -> float:
        return self.entry - self.stop


@dataclass
class Trade:
    ticker: str
    kind: str
    triggered_at: pd.Timestamp
    entry: float
    stop: float
    target: float
    fill: float
    exit_at: pd.Timestamp | None = None
    exit_price: float | None = None
    outcome: str = "open"          # target / stop / time / open
    r: float | None = None


def _tick(price: float) -> float:
    return 0.01 if price >= 1 else 0.0001


def _parse_time(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


# ---------------------------------------------------------------- setups

def _finish(kind: str, entry: float, stop: float, note: str, sc: dict) -> Setup | None:
    if not (stop < entry) or stop <= 0:
        return None
    stop_pct = (entry - stop) / entry * 100
    if stop_pct > sc["max_stop_pct"] or stop_pct < sc.get("min_stop_pct", 1.0):
        return None
    target = entry + sc["target_r"] * (entry - stop)
    return Setup(kind, round(entry, 4), round(stop, 4), round(target, 4), note)


def find_setups(hist: pd.DataFrame, sc: dict) -> list[Setup]:
    """Setups visible after the last bar in `hist` (finished bars, indicators added)."""
    if len(hist) < 3:
        return []
    last = hist.iloc[-1]
    tick = _tick(last["close"])
    st = sc["setups"]
    out: list[Setup] = []

    # --- step 1: market structure, step 2: candlestick pattern at VWAP (your setups)
    ms = sc.get("structure", {})
    for kind, (detect, n_bars) in PATTERNS.items():
        ps = st.get(kind, {})
        if not ps.get("enabled", False) or not detect(hist, ps):
            continue
        structure_note = ""
        if ms.get("enabled", False):
            # structure must already be in our favour BEFORE the pattern's first candle
            state, _ = M.structure_state(hist.iloc[:-n_bars], ms)
            if not M.is_bullish(state):
                continue
            structure_note = f"structure {state}, "
        pat = hist.tail(n_bars)
        low = pat["low"].min()
        vwap = last["vwap"]
        near = low <= vwap * (1 + ps.get("vwap_near_pct", 1.0) / 100)
        if near and last["close"] > vwap:
            touch = "touched VWAP" if low <= vwap else f"held {(low / vwap - 1) * 100:.1f}% above VWAP"
            s = _finish(kind, pat["high"].max() + tick, low - tick,
                        f"{structure_note}pattern {touch} and closed above it", sc)
            if s:
                s.bars_left = sc.get("entry_valid_bars", 2)
                out.append(s)

    if len(hist) < 20:              # the older momentum setups need more history
        return out

    # --- bull flag / first pullback
    bf = st.get("bull_flag", {})
    if bf.get("enabled", True):
        window = hist.tail(bf["pole_max_bars"] + bf["pullback_max_bars"])
        p = hist.index.get_loc(window["high"].idxmax())
        pull = hist.iloc[p + 1:]
        if bf["pullback_min_bars"] <= len(pull) <= bf["pullback_max_bars"]:
            pole = hist.iloc[max(0, p - bf["pole_max_bars"] + 1): p + 1]
            pole_high, pole_low = pole["high"].max(), pole["low"].min()
            gain = (pole_high - pole_low) / pole_low * 100
            retrace = (pole_high - pull["low"].min()) / max(pole_high - pole_low, 1e-9) * 100
            if (gain >= bf["pole_min_pct"] and retrace <= bf["max_retrace_pct"]
                    and pull["volume"].mean() < pole["volume"].mean()
                    and pull["low"].min() > last["vwap"]):
                s = _finish("bull_flag", pull["high"].iloc[-1] + tick, pull["low"].min() - tick,
                            f"push +{gain:.0f}%, pullback gave back {retrace:.0f}% on lighter volume", sc)
                if s:
                    out.append(s)

    # --- high-of-day breakout
    hb = st.get("hod_breakout", {})
    if hb.get("enabled", True):
        hod = hist["high"].max()
        near = (hod - last["close"]) / hod * 100
        vol_ok = hist["volume"].tail(3).mean() >= hb["volume_mult"] * hist["volume"].tail(20).mean()
        if near <= hb["near_high_pct"] and vol_ok and last["close"] > last["vwap"] \
                and last["ema_fast"] > last["ema_slow"]:
            stop = max(hist["low"].tail(5).min(), last["vwap"]) - tick
            s = _finish("hod_breakout", hod + tick, stop,
                        f"{near:.1f}% under the high of day with rising volume", sc)
            if s:
                out.append(s)

    # --- VWAP reclaim
    vr = st.get("vwap_reclaim", {})
    if vr.get("enabled", True):
        look = hist.tail(vr["lookback_bars"] + 1)
        below = (look["close"].iloc[:-1] < look["vwap"].iloc[:-1]).sum()
        prev = hist.iloc[-2]
        vol_ok = last["volume"] >= vr["volume_mult"] * hist["volume"].tail(20).mean()
        if (below >= vr["min_bars_below"] and prev["close"] < prev["vwap"]
                and last["close"] > last["vwap"] and vol_ok):
            s = _finish("vwap_reclaim", last["high"] + tick, look["low"].min() - tick,
                        f"closed back above VWAP after {below} bars below", sc)
            if s:
                out.append(s)
    return out


# ---------------------------------------------------------------- engine

def run_day(day: pd.DataFrame, ticker: str, cfg: dict
            ) -> tuple[list[Trade], list[Setup], list[tuple[pd.Timestamp, Setup]]]:
    """Walk one day of bars (already at the signal timeframe) bar by bar.

    Returns (triggered trades, setups still open after the last bar,
    every (bar time, setup) spotted - used to mark patterns on the chart).
    """
    sc = cfg["signals"]
    bt = cfg.get("backtest", {})
    exit_t = _parse_time(bt.get("exit_time", "15:55"))
    last_entry = _parse_time(sc.get("last_entry_time", "15:30"))
    slip = sc.get("slippage_pct", 0.2) / 100
    df = B.add_indicators(day, cfg)

    trades: list[Trade] = []
    open_trade: Trade | None = None
    pending: list[Setup] = []
    spotted: list[tuple[pd.Timestamp, Setup]] = []
    for i in range(1, len(df)):
        bar, ts = df.iloc[i], df.index[i]

        if open_trade:                              # manage the open trade
            if bar["low"] <= open_trade.stop:
                px, how = min(open_trade.stop, bar["open"]), "stop"
            elif bar["high"] >= open_trade.target:
                px, how = max(open_trade.target, bar["open"]), "target"
            elif ts.time() >= exit_t:
                px, how = bar["close"], "time"
            else:
                continue
            exit_fill = px * (1 - slip)
            open_trade.exit_at, open_trade.exit_price, open_trade.outcome = ts, round(exit_fill, 4), how
            open_trade.r = round((exit_fill - open_trade.fill) / (open_trade.entry - open_trade.stop), 2)
            open_trade = None
            pending = []
            continue

        in_window = B.is_regular(ts, sc.get("no_signals_first_minutes", 2), last_entry)
        if in_window and pending:                   # did this bar trigger a pending setup?
            hits = [s for s in pending if bar["high"] >= s.entry]
            if hits:
                s = min(hits, key=lambda x: x.entry)
                fill = max(s.entry, bar["open"]) * (1 + slip)
                open_trade = Trade(ticker, s.kind, ts, s.entry, s.stop, s.target, round(fill, 4))
                trades.append(open_trade)
                pending = []
                if bar["low"] <= s.stop and bar["close"] < s.entry:
                    # the trigger candle also fell through the stop: count it as stopped (worst case)
                    exit_fill = s.stop * (1 - slip)
                    open_trade.exit_at, open_trade.exit_price, open_trade.outcome = ts, round(exit_fill, 4), "stop"
                    open_trade.r = round((exit_fill - open_trade.fill) / (s.entry - s.stop), 2)
                    open_trade = None
                continue

        # setups from earlier candles stay valid for a few candles unless the stop was broken
        carried = []
        for x in pending:
            if x.bars_left > 1 and bar["low"] > x.stop:
                x.bars_left -= 1
                carried.append(x)
        hist = df.iloc[: i + 1]                     # bar i is finished; look for setups for bar i+1
        new = find_setups(hist, sc) if B.is_regular(ts, 0, last_entry) else []
        spotted += [(ts, x) for x in new]
        pending = new + [x for x in carried if all(x.kind != n.kind for n in new)]

    return trades, (pending if not open_trade else []), spotted


# ---------------------------------------------------------------- live signal board

@dataclass
class LiveSignal:
    ticker: str
    status: str                   # 🟢 Triggered / 🟡 Setup forming / 👀 Watching / ❌ No longs
    setup: str = ""
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    shares: int | None = None
    risk_dollars: float | None = None
    time: pd.Timestamp | None = None
    note: str = ""
    trend: str = ""
    structure: str = ""
    swings: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def position_size(entry: float, stop: float, sc: dict) -> tuple[int, float]:
    """Shares so that hitting the stop loses at most risk_per_trade_pct of the account."""
    risk_budget = sc["account_size"] * sc["risk_per_trade_pct"] / 100
    shares = math.floor(risk_budget / (entry - stop))
    shares = min(shares, math.floor(sc["account_size"] / entry))   # no margin
    return max(shares, 0), round(shares * (entry - stop), 2)


def live_signal(ticker: str, bars_today: pd.DataFrame, prev_close: float | None, cfg: dict
                ) -> tuple[LiveSignal, pd.DataFrame, B.Levels, list[Trade], list]:
    sc = cfg["signals"]
    now = now_et()
    tf = sc.get("timeframe_minutes", 5)
    day = B.resample(bars_today, tf)
    if len(day) and day.index[-1] + pd.Timedelta(minutes=tf) > pd.Timestamp(now):
        day = day.iloc[:-1]                         # drop the candle still forming
    df = B.add_indicators(day, cfg) if len(day) else day
    if len(df) < 3:
        return LiveSignal(ticker, "👀 Not enough data yet"), df, None, [], []

    levels = B.key_levels(df, prev_close)
    trades, setups, spotted = run_day(day, ticker, cfg)
    trend = B.trend_text(df)
    use_ms = sc.get("structure", {}).get("enabled", False)
    ms_state, swings = M.structure_state(df, sc.get("structure", {})) if use_ms else ("", [])
    recent = [t for t in trades
              if (now - t.triggered_at).total_seconds() / 60 <= sc.get("recent_trigger_minutes", 5)]

    def build(status, s_entry, s_stop, s_target, kind, ts, note) -> LiveSignal:
        shares, risk_d = position_size(s_entry, s_stop, sc)
        warn = [f"{name} at ${lvl:.2f} is before the target"
                for name, lvl in levels.overhead(s_entry) if lvl < s_target][:2]
        return LiveSignal(ticker, status, SETUP_NAMES[kind], s_entry, s_stop, s_target, shares, risk_d,
                          ts, note, trend, ms_state, swings, warn)

    if recent:
        t = recent[-1]
        sig = build("🟢 Triggered", t.entry, t.stop, t.target, t.kind, t.triggered_at,
                    "price broke the entry level")
    elif setups:
        s = min(setups, key=lambda x: x.risk / x.entry)
        sig = build("🟡 Setup forming", s.entry, s.stop, s.target, s.kind, df.index[-1], s.note)
    elif use_ms and not M.is_bullish(ms_state):
        sig = LiveSignal(ticker, "❌ Structure not bullish" if ms_state.startswith(("bearish", "broken"))
                         else "⏳ Waiting for structure", trend=trend, structure=ms_state, swings=swings)
    elif not use_ms and "downtrend" in trend:
        sig = LiveSignal(ticker, "❌ No longs (downtrend)", trend=trend)
    else:
        sig = LiveSignal(ticker, "👀 Watching for pattern", trend=trend, structure=ms_state, swings=swings)
    return sig, df, levels, trades, spotted


# ---------------------------------------------------------------- backtest

def in_play_days(df: pd.DataFrame, cfg: dict) -> list[tuple]:
    """(date, day_bars) for days the stock was a mover (vs the previous day's close)."""
    bt = cfg.get("backtest", {})
    days = B.split_days(df)
    out, prev_close = [], None
    for d in sorted(days):
        day = days[d]
        reg = day[(day.index.time >= time(9, 30)) & (day.index.time < time(16, 0))]
        if prev_close and len(reg):
            change = (reg["high"].max() - prev_close) / prev_close * 100
            if change >= bt.get("min_day_change_pct", 5) and reg["volume"].sum() >= bt.get("min_day_volume", 1e6):
                out.append((d, day))
        prev_close = B.regular_close(day) or prev_close
    return out


def backtest(ticker_bars: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    rows = []
    for t, df in ticker_bars.items():
        for d, day in in_play_days(df, cfg):
            trades, _, _ = run_day(B.resample(day, cfg["signals"].get("timeframe_minutes", 5)), t, cfg)
            for tr in trades:
                if tr.r is None:
                    continue                        # still running (today) or data ended early
                rows.append({"date": d, "ticker": t, "setup": SETUP_NAMES[tr.kind],
                             "time_et": tr.triggered_at.strftime("%H:%M"), "entry": tr.entry,
                             "stop": tr.stop, "target": tr.target, "exit": tr.exit_price,
                             "outcome": tr.outcome, "r": tr.r})
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame, cfg: dict) -> dict:
    if trades.empty:
        return {"trades": 0}
    risk_d = cfg["signals"]["account_size"] * cfg["signals"]["risk_per_trade_pct"] / 100
    wins = trades["r"] > 0
    return {
        "trades": len(trades),
        "win_rate": wins.mean() * 100,
        "avg_r": trades["r"].mean(),
        "total_r": trades["r"].sum(),
        "dollars": trades["r"].sum() * risk_d,
        "days": trades["date"].nunique(),
        "tickers": trades["ticker"].nunique(),
    }
