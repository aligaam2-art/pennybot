"""Static PNG chart of one backtest trade (for reports)."""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import bars as B  # noqa: E402
from . import signals as S  # noqa: E402

UP, DOWN = "#26a69a", "#ef5350"
LABEL = {"vwap_engulfing": "E", "vwap_morning_star": "MS"}


def trade_chart(day_1m: pd.DataFrame, ticker: str, trade_time: str, cfg: dict, path, display_tz: str) -> dict:
    """Draw the day (5-min candles) around one trade; returns the trade's details."""
    tf = cfg["signals"].get("timeframe_minutes", 5)
    day = B.resample(day_1m, tf)
    trades, _, spotted = S.run_day(day, ticker, cfg)
    tr = next(t for t in trades if t.triggered_at.strftime("%H:%M") == trade_time)
    df = B.add_indicators(day, cfg)
    df = df[(df.index.time >= time(8, 30)) & (df.index.time < time(16, 0))]
    tz = ZoneInfo(display_tz)
    x = df.index.tz_convert(tz).tz_localize(None)
    w = pd.Timedelta(minutes=tf * 0.7)

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [4, 1]})
    for xi, (_, b) in zip(x, df.iterrows()):
        c = UP if b["close"] >= b["open"] else DOWN
        ax.vlines(xi, b["low"], b["high"], color=c, linewidth=0.8)
        ax.bar(xi, abs(b["close"] - b["open"]) or b["close"] * 0.0005, width=w,
               bottom=min(b["open"], b["close"]), color=c, align="center")
        axv.bar(xi, b["volume"], width=w, color=c, align="center")
    ax.plot(x, df["vwap"], color="#ff9800", linewidth=2, label="VWAP")
    ax.plot(x, df["ema_fast"], color="#42a5f5", linewidth=1, label="9 EMA")
    ax.plot(x, df["ema_slow"], color="#ab47bc", linewidth=1, label="20 EMA")

    def local(ts):
        return ts.tz_convert(tz).tz_localize(None)

    t0, t1 = local(tr.triggered_at), local(tr.exit_at)
    for y, col, name in ((tr.entry, "#1e88e5", "Entry"), (tr.stop, DOWN, "Stop"), (tr.target, UP, "Target")):
        ax.hlines(y, t0, t1 + pd.Timedelta(minutes=tf * 3), colors=col, linewidth=1.6)
        ax.axhline(y, color=col, linewidth=0.6, linestyle=":", alpha=0.6)
        ax.annotate(f"{name} {y:.4g}", (1.005, y), xycoords=("axes fraction", "data"), color=col,
                    va="center", fontsize=9, fontweight="bold")
    ax.scatter([t0], [tr.entry], marker="^", s=140, color="#1e88e5", zorder=5, edgecolor="white")
    ax.scatter([t1], [tr.exit_price], marker="X", s=120, zorder=5, edgecolor="white",
               color=UP if (tr.r or 0) > 0 else DOWN)
    for ts, st in spotted:
        if st.kind in LABEL and local(ts) <= t0 and (t0 - local(ts)) <= pd.Timedelta(minutes=tf * 3):
            ax.annotate(LABEL[st.kind], (local(ts), df.loc[ts, "low"]), xytext=(0, -16),
                        textcoords="offset points", ha="center", color="#1e88e5", fontsize=11, fontweight="bold")

    result = f"{tr.outcome.upper()} {tr.r:+.2f}R (${tr.r * cfg['signals']['account_size'] * cfg['signals']['risk_per_trade_pct'] / 100:+.0f})"
    ax.set_title(f"{ticker}  ·  {tr.triggered_at:%a %d %b}  ·  {S.SETUP_NAMES[tr.kind]}  ·  {result}",
                 fontsize=12, loc="left")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.2)
    axv.grid(alpha=0.2)
    axv.set_ylabel("Volume", fontsize=8)
    axv.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axv.set_xlabel(f"Time ({display_tz})", fontsize=8)
    fig.tight_layout(rect=(0, 0, 0.9, 1))
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return {"entry": tr.entry, "stop": tr.stop, "target": tr.target, "exit": tr.exit_price,
            "entry_local": f"{t0:%H:%M}", "exit_local": f"{t1:%H:%M}", "outcome": tr.outcome, "r": tr.r}
