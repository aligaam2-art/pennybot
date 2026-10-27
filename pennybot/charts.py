"""Intraday candlestick chart with VWAP, EMAs, volume, key levels and the trade plan."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .bars import Levels
from .signals import SETUP_NAMES, LiveSignal, Trade

UP, DOWN = "#26a69a", "#ef5350"


PATTERN_LABEL = {"vwap_engulfing": "E", "vwap_morning_star": "MS", "orb": "ORB"}


def intraday_chart(df: pd.DataFrame, levels: Levels | None, signal: LiveSignal | None,
                   trades: list[Trade], display_tz: str, ticker: str, spotted: list | None = None,
                   minutes: int = 5) -> go.Figure:
    tz = ZoneInfo(display_tz)
    x = df.index.tz_convert(tz)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.03)

    fig.add_trace(go.Candlestick(x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                 name=ticker, increasing_line_color=UP, decreasing_line_color=DOWN,
                                 showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["vwap"], name="VWAP", line=dict(color="#ff9800", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["ema_fast"], name="9 EMA", line=dict(color="#42a5f5", width=1)),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["ema_slow"], name="20 EMA", line=dict(color="#ab47bc", width=1)),
                  row=1, col=1)
    colors = [UP if c >= o else DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=x, y=df["volume"], marker_color=colors, name="Volume", showlegend=False),
                  row=2, col=1)

    def hline(y, text, color, dash="dot", side="left"):
        if y:
            fig.add_hline(y=y, line=dict(color=color, width=1, dash=dash), row=1, col=1,
                          annotation_text=f"{text} {y:.4g}", annotation_position=f"top {side}",
                          annotation_font_size=10, annotation_font_color=color)

    if levels:
        hline(levels.prev_close, "Prev close", "#9e9e9e")
        hline(levels.premarket_high, "PM high", "#7e57c2")
        hline(levels.high_of_day, "HOD", "#5c6bc0")

    if signal and signal.entry:
        hline(signal.entry, "Entry", "#1e88e5", "solid", "right")
        hline(signal.stop, "Stop", DOWN, "solid", "right")
        hline(signal.target, "Target", UP, "solid", "right")

    if trades:
        fig.add_trace(go.Scatter(
            x=[t.triggered_at.tz_convert(tz) for t in trades], y=[t.entry for t in trades],
            mode="markers", name="Signals today",
            marker=dict(symbol="triangle-up", size=12, color="#1e88e5", line=dict(color="white", width=1)),
            text=[f"{SETUP_NAMES[t.kind]} · {t.outcome} {'' if t.r is None else f'{t.r:+.1f}R'}"
                  for t in trades], hovertemplate="%{text}<br>entry %{y}<extra></extra>"), row=1, col=1)

    swings = [w for w in (signal.swings if signal else []) if w.label]
    if swings:
        fig.add_trace(go.Scatter(
            x=[w.ts.tz_convert(tz) for w in swings],
            y=[w.price * (1.006 if w.kind == "high" else 0.994) for w in swings],
            mode="text", text=[w.label for w in swings],
            textposition=["top center" if w.kind == "high" else "bottom center" for w in swings],
            textfont=dict(color=["#2e7d32" if w.label in ("HH", "HL") else "#c62828" for w in swings], size=10),
            name="Structure (HH/HL/LH/LL)", hoverinfo="skip"), row=1, col=1)

    marks = [(ts, st) for ts, st in (spotted or []) if st.kind in PATTERN_LABEL]
    if marks:
        lows = df["low"]
        fig.add_trace(go.Scatter(
            x=[ts.tz_convert(tz) for ts, _ in marks], y=[lows.loc[ts] * 0.995 for ts, _ in marks],
            mode="text", text=[PATTERN_LABEL[st.kind] for _, st in marks], textposition="bottom center",
            textfont=dict(color="#1e88e5", size=11), name="Pattern (E = engulfing, MS = morning star)",
            hovertext=[SETUP_NAMES[st.kind] for _, st in marks], hoverinfo="text+x"), row=1, col=1)

    fig.update_layout(height=560, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.04, x=0), hovermode="x unified",
                      title=dict(text=f"{ticker} · {minutes}-minute candles · times in your zone", font_size=13))
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    return fig
