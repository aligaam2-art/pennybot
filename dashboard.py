"""Pennybot dashboard.  Start it with start_dashboard.bat (or: streamlit run dashboard.py)."""
from __future__ import annotations

import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from pennybot import bars as B
from pennybot import output
from pennybot import signals as S
from pennybot.catalyst import TAG_ICON, check_catalysts
from pennybot.charts import intraday_chart
from pennybot.clock import fmt_local, now_et, session, session_times_local
from pennybot.config import load_config
from pennybot.screener import apply_filters, load_candidates
from pennybot.selection import FLAG_TEXT, rank

st.set_page_config(page_title="Pennybot", page_icon="📈", layout="wide")
cfg = load_config()
TZ = cfg["display_timezone"]
defaults = cfg["screener"]

GRADE_BADGE = {"A": ":green-badge[A]", "B": ":blue-badge[B]", "C": ":orange-badge[C]",
               "Avoid": ":red-badge[Avoid]"}


@st.cache_data(ttl=120, show_spinner="Downloading today's movers from Yahoo Finance...")
def get_raw(max_price: float, min_change: float, lookback: int):
    c = {**cfg, "screener": {**defaults, "max_price": max_price,
                             "min_change_pct": min_change, "rvol_lookback_days": lookback}}
    for attempt in range(3):                   # Yahoo sometimes turns cloud servers away briefly
        try:
            return load_candidates(c)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


@st.cache_data(ttl=600, show_spinner="Checking news and SEC filings...")
def get_catalysts(tickers: tuple[str, ...]):
    return check_catalysts(list(tickers), cfg)


@st.cache_data(ttl=50, show_spinner=False)
def get_bars(tickers: tuple[str, ...], days: int):
    data = B.download_bars(list(tickers), days)
    B.save_finished_days(data, cfg)
    return data


BEEP = """<script>
const ctx = new (window.AudioContext || window.webkitAudioContext)();
[0, 0.25].forEach(t => { const o = ctx.createOscillator(), g = ctx.createGain();
  o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
  g.gain.setValueAtTime(0.2, ctx.currentTime + t); o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.15); });
</script>"""


def md(text) -> str:
    """Stop $ signs in headlines being read as math formulas."""
    return str(text).replace("$", "\\$")


def age_text(hours) -> str:
    if hours is None or pd.isna(hours):
        return ""
    return f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"


# ------------------------------------------------------------------ sidebar: rules
with st.sidebar:
    st.header("Screener rules")
    st.caption("Starting values come from config.yaml. Changes here are for this session only.")
    if st.button("🔄 Refresh data", width="stretch", type="primary"):
        get_raw.clear()
        get_catalysts.clear()

    s = dict(defaults)
    s["exchanges"] = st.multiselect("Exchanges (OTC always excluded)", ["NASDAQ", "NYSE", "NYSE American"],
                                    defaults["exchanges"])
    c1, c2 = st.columns(2)
    s["min_price"] = c1.number_input("Min price $", 0.0, 50.0, float(defaults["min_price"]), 0.05)
    s["max_price"] = c2.number_input("Max price $", 0.1, 50.0, float(defaults["max_price"]), 0.5)
    s["min_change_pct"] = st.number_input("Min % change", 0.0, 500.0, float(defaults["min_change_pct"]), 1.0)
    s["min_volume"] = dict(defaults["min_volume"])
    s["min_volume"]["intraday"] = st.number_input("Min volume (shares)", 0, 100_000_000,
                                                  int(defaults["min_volume"]["intraday"]), 100_000)
    s["max_market_cap"] = st.number_input("Max market cap ($M)", 1, 100_000,
                                          int(defaults["max_market_cap"] / 1e6), 50) * 1e6
    s["keep_unknown_market_cap"] = st.checkbox("Keep stocks with unknown market cap",
                                               defaults.get("keep_unknown_market_cap", True))
    st.divider()
    s["use_relative_volume"] = st.toggle("Relative volume filter", defaults.get("use_relative_volume", True))
    s["min_relative_volume"] = st.number_input("Min relative volume (x)", 0.0, 1000.0,
                                               float(defaults["min_relative_volume"]), 0.5,
                                               disabled=not s["use_relative_volume"])
    s["use_float"] = st.toggle("Float filter (unknown float is kept)", defaults.get("use_float", True))
    s["max_float"] = st.number_input("Max float (M shares)", 0.1, 1000.0, defaults["max_float"] / 1e6, 5.0,
                                     disabled=not s["use_float"]) * 1e6
    s["exclude_spacs_rights_warrants"] = st.toggle("Exclude SPACs / rights / warrants",
                                                   defaults.get("exclude_spacs_rights_warrants", True))
    st.caption("Tip: switch a filter off to see what it was hiding.")
    st.divider()
    check_news = st.toggle("Check news & SEC filings (catalysts + scores)", True)
    st.divider()
    st.subheader("Signals")
    alert_cfg = cfg["signals"].get("alerts", {})
    refresh_s = alert_cfg.get("auto_refresh_seconds", 60)
    auto_refresh = st.toggle(f"Auto-refresh signals every {refresh_s}s", True)
    sound_on = st.toggle("Beep when a signal triggers", alert_cfg.get("sound", True))
    sc = cfg["signals"]
    st.caption(f"Paper account \\${sc['account_size']:,} · risk {sc['risk_per_trade_pct']:g}% "
               f"(\\${sc['account_size'] * sc['risk_per_trade_pct'] / 100:,.0f}) per trade · "
               f"target {sc['target_r']:g}R. Change in config.yaml.")

# ------------------------------------------------------------------ header: clock
now = now_et()
sess = session(now)
times = session_times_local(TZ, now)
status = {"premarket": "🟡 Premarket", "regular": "🟢 Open",
          "afterhours": "🟠 After hours", "closed": "⚪ Closed"}[sess]
local_now = fmt_local(now, TZ)

st.title("📈 Pennybot")
m = st.columns(4)
m[0].metric("Your time (Dar es Salaam)", local_now[-5:])
m[1].metric("New York time", now.strftime("%H:%M"))
m[2].metric("US market", status)
m[3].metric("Open–close (yours)",
            f"{times['market_opens']:%H:%M}–{times['market_closes']:%H:%M}")
st.caption(f"{local_now[:-6]} · Premarket scan window today: {times['premarket_scan_from']:%H:%M}–"
           f"{times['premarket_scan_to']:%H:%M} your time (45–60 min before the open). "
           "US daylight-saving changes are handled automatically.")

# ------------------------------------------------------------------ journal (own data, no Yahoo needed)
def render_journal():
    st.subheader("Automatic paper-trading journal")
    st.caption("Runs by itself in the cloud (GitHub) every weekday during US market hours. Every 5 minutes it "
               "runs this dashboard's screener and signals and logs each signal it sees live as a paper trade - "
               "no hindsight. Results update about every 30 minutes.")
    jdir = Path(output.ROOT / "journal")
    trades_path = jdir / "trades.csv"
    jt = pd.read_csv(trades_path, dtype={"date": str}) if trades_path.exists() else pd.DataFrame()
    if len(jt) and "strategy" not in jt.columns:
        jt.insert(1, "strategy", "baseline")
    names = {x["id"]: x.get("name", x["id"]) for x in cfg.get("strategies", [])} or {"baseline": "Current rules"}
    if len(jt):
        jt["strategy name"] = jt["strategy"].map(lambda x: names.get(x, x))
    if jt.empty:
        st.info("No paper trades logged yet. The journal starts on the next US trading day "
                f"(from {times['market_opens']:%H:%M} your time) and fills in as signals appear.")
    else:
        closed = jt[jt["status"] == "closed"].copy()
        closed["r"] = pd.to_numeric(closed["r"])
        closed["pnl_dollars"] = pd.to_numeric(closed["pnl_dollars"])

        # ---- side-by-side comparison (only days when every strategy was running)
        start = str(cfg.get("strategies_start", ""))
        if len(names) > 1:
            st.markdown("#### Strategies side by side")
            same = closed[closed["date"] >= start] if start else closed
            st.caption(f"Same stocks, same moments, from {start or 'the start'} (when side-by-side testing began). "
                       "Judge only after 30+ trades per strategy; before that, differences are mostly luck.")
            rows = []
            for sid, name in names.items():
                r = same.loc[same["strategy"] == sid, "r"]
                p = same.loc[same["strategy"] == sid, "pnl_dollars"]
                rows.append({"Strategy": name, "Trades": len(r),
                             "Win rate": (r > 0).mean() * 100 if len(r) else None,
                             "Avg per trade (R)": r.mean() if len(r) else None,
                             "Total (R)": r.sum(), "Paper P&L ($)": p.sum()})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
                "Win rate": st.column_config.NumberColumn(format="%.0f%%"),
                "Avg per trade (R)": st.column_config.NumberColumn(format="%+.2f"),
                "Total (R)": st.column_config.NumberColumn(format="%+.1f"),
                "Paper P&L ($)": st.column_config.NumberColumn(format="$%+.0f")})
            if len(same) > 1:
                curve = same.sort_values(["date", "signal_time_et"]).copy()
                curve["n"] = curve.groupby("strategy").cumcount() + 1
                curve["equity"] = sc["account_size"] + curve.groupby("strategy")["pnl_dollars"].cumsum()
                st.altair_chart(alt.Chart(curve).mark_line(point=True).encode(
                    x=alt.X("n:Q", title="paper trade # (per strategy)"),
                    y=alt.Y("equity:Q", title="paper account $", scale=alt.Scale(zero=False)),
                    color=alt.Color("strategy name:N", title=None, legend=alt.Legend(orient="bottom")),
                    tooltip=["strategy name", "date", "ticker", "outcome", "r", "equity"]).properties(height=240),
                    width="stretch")

        choice = st.selectbox("Show trades for", list(names.values()) + ["All strategies"],
                              index=0, key="journal_strategy")
        if choice != "All strategies":
            sid = next(k for k, v in names.items() if v == choice)
            closed = closed[closed["strategy"] == sid]
            jt = jt[jt["strategy"] == sid]
        k = st.columns(5)
        k[0].metric("Paper trades", len(closed), help=f"{jt['date'].nunique()} trading days, "
                    f"{(jt['status'] == 'open').sum()} still open")
        k[1].metric("Win rate", f"{(closed['r'] > 0).mean() * 100:.0f}%" if len(closed) else "-")
        k[2].metric("Average per trade", f"{closed['r'].mean():+.2f}R" if len(closed) else "-")
        k[3].metric("Total", f"{closed['r'].sum():+.1f}R")
        k[4].metric("Paper P&L", f"${closed['pnl_dollars'].sum():+,.0f}")

        if len(closed) > 1:
            c1, c2 = st.columns(2)
            for col, by, title in ((c1, "setup", "By setup"), (c2, "grade", "By selection grade")):
                g = closed.groupby(by)["r"].agg(trades="count", win_rate=lambda r: (r > 0).mean() * 100,
                                                 avg_r="mean").reset_index()
                col.markdown(f"**{title}**")
                col.dataframe(g, hide_index=True, width="stretch", column_config={
                    "win_rate": st.column_config.NumberColumn("Win rate", format="%.0f%%"),
                    "avg_r": st.column_config.NumberColumn("Avg R", format="%+.2f")})

        def local_time(row, col):
            if not isinstance(row[col], str) or not row[col]:
                return ""
            dt_et = pd.Timestamp(f"{row['date']} {row[col]}").tz_localize("America/New_York")
            return fmt_local(dt_et, TZ)[-5:]

        view = jt.iloc[::-1].copy()
        view["signal (yours)"] = view.apply(lambda r: local_time(r, "signal_time_et"), axis=1)
        view["exit (yours)"] = view.apply(lambda r: local_time(r, "exit_time_et"), axis=1)
        st.dataframe(view[["date", "strategy name", "ticker", "grade", "setup", "signal (yours)", "entry", "stop", "target",
                           "shares", "status", "outcome", "exit (yours)", "exit_price", "r", "pnl_dollars",
                           "headline"]], hide_index=True, width="stretch")

        if view.empty:
            st.caption("No trades for this strategy yet.")
            return
        pick = st.selectbox("Chart a logged trade", range(len(view)), format_func=lambda i: (
            f"{view.iloc[i]['date']} · {view.iloc[i]['strategy name']} · {view.iloc[i]['ticker']} · {view.iloc[i]['setup']} · "
            f"{view.iloc[i]['outcome'] or 'open'}"))
        row = view.iloc[pick]
        d = pd.Timestamp(row["date"]).date()
        stored = B.load_stored(row["ticker"], cfg)
        day_bars = stored[stored.index.date == d] if len(stored) else stored
        if day_bars is None or day_bars.empty:
            day_bars = get_bars((row["ticker"],), 8).get(row["ticker"], pd.DataFrame())
            day_bars = day_bars[day_bars.index.date == d] if len(day_bars) else day_bars
        if len(day_bars):
            day5 = B.resample(day_bars, tf)
            trs, _, spotted_j = S.run_day(day5, row["ticker"], cfg)   # markers only; levels come from the log
            tr = [t for t in trs if t.triggered_at.strftime("%H:%M") == row["signal_time_et"]]
            sig_j = S.LiveSignal(row["ticker"], "", row["setup"], row["entry"], row["stop"], row["target"])
            st.plotly_chart(intraday_chart(B.add_indicators(day5, cfg), None, sig_j, tr, TZ, row["ticker"],
                                           spotted_j, tf), width="stretch")
        else:
            st.caption("Price data for this day isn't available any more.")

    summ_path = jdir / "summary.csv"
    if summ_path.exists():
        st.markdown("**Day by day**")
        summ = pd.read_csv(summ_path)
        if "strategy" in summ.columns:
            summ["strategy"] = summ["strategy"].map(lambda x: names.get(x, x))
        st.dataframe(summ.iloc[::-1], hide_index=True, width="stretch")

# ------------------------------------------------------------------ data
tf = sc.get("timeframe_minutes", 5)
try:
    raw = get_raw(s["max_price"], s["min_change_pct"], defaults.get("rvol_lookback_days", 10))
except Exception as e:  # network / Yahoo problems
    st.warning("**Yahoo Finance isn't answering right now**, so live prices, scores and signals can't load. "
               "This happens now and then with free data on cloud servers and usually clears within minutes. "
               "Your paper-trading journal below still works, and the cloud journal keeps running on its own.",
               icon="⏳")
    if st.button("🔄 Try Yahoo again"):
        get_raw.clear()
        st.rerun()
    with st.expander("Technical details"):
        st.code(repr(e))
    render_journal()
    st.stop()

result = apply_filters(raw, s, "intraday")
catalysts, ranked = {}, None
if check_news and len(result.candidates):
    catalysts = get_catalysts(tuple(result.candidates["ticker"]))
    ranked = rank(result.candidates, catalysts, cfg)

# save one CSV + filter log per fresh download (not on every click)
save_key = (raw.scanned_at, ranked is not None)
if st.session_state.get("saved_for") != save_key:
    csv_path, _ = output.save(result, cfg, ranked)
    st.session_state["saved_for"] = save_key
    st.session_state["saved_path"] = csv_path

delay = raw.movers["delay_min"].max() if len(raw.movers) else 0
st.info(f"Data from Yahoo Finance (free) at **{fmt_local(raw.scanned_at, TZ)}** your time"
        + (f", delayed up to {delay} min" if delay else ", may lag by a few minutes")
        + ". Good for learning and paper trading, not for split-second entries.", icon="ℹ️")

# ------------------------------------------------------------------ top picks
if ranked is not None:
    n = cfg["selection"].get("top_picks", 3)
    st.subheader(f"⭐ Top {n} for day trading")
    st.caption("A study aid built from your config.yaml rules, not a buy signal. Before any paper trade, "
               "check in Webull: bid/ask spread under ~3%, price above VWAP, and no trading halt.")
    picks = ranked.head(n)
    if (picks["grade"] == "Avoid").all():
        st.warning("No good setups right now. Sitting out is a valid decision.")
    for col, (_, r) in zip(st.columns(n), picks.iterrows()):
        with col.container(border=True):
            st.markdown(f"#### {r['ticker']} {GRADE_BADGE[r['grade']]}")
            st.markdown(f"**{r['score']}/100** · \\${r['price']:.2f} · **{r['change_pct']:+.0f}%** today  \n"
                        f"{md(r['name'])}")
            if r["headline"]:
                st.markdown(f"📰 [{md(r['headline'])}]({r['news_link']}) · {age_text(r['news_age_h'])}")
            st.markdown("\n".join(f"- {md(x)}" for x in r["reasons"].split(" · ")))
            links = [f"[Chart ↗](https://finance.yahoo.com/quote/{r['ticker']})"]
            info = catalysts.get(r["ticker"])
            if info and info.sec_page:
                links.append(f"[SEC filings ↗]({info.sec_page})")
            st.markdown(" · ".join(links))

tab_signals, tab_journal, tab_scan, tab_news, tab_backtest, tab_funnel, tab_next = st.tabs(
    ["📊 Charts & signals", "📓 Journal", "🔎 Screener", "📰 Catalysts & filings", "🧪 Backtest",
     "🧹 Which rules cut the most", "🧩 Coming next"])

# ------------------------------------------------------------------ tab: charts & signals
skip = set(sc.get("skip_grades", ["Avoid"]))
if ranked is not None:
    watch_df = ranked[~ranked["grade"].isin(skip)].head(sc.get("max_stocks_watched", 8))
    grades = dict(zip(ranked["ticker"], ranked["grade"]))
else:
    watch_df = result.candidates.head(sc.get("max_stocks_watched", 8))
    grades = {}
watch = watch_df["ticker"].tolist()


tf = sc.get("timeframe_minutes", 5)
use_structure = sc.get("structure", {}).get("enabled", False)


@st.fragment(run_every=f"{refresh_s}s" if auto_refresh else None)
def signal_board():
    if not watch:
        st.info(f"No stocks to watch: none passed the screener with a grade other than {', '.join(skip)}.")
        return
    data = get_bars(tuple(watch), 2)
    rows, details = [], {}
    for t in watch:
        df = data.get(t)
        if df is None or df.empty:
            continue
        days = B.split_days(df)
        last_day = max(days)
        prior = [d for d in sorted(days) if d < last_day]
        prev = B.regular_close(days[prior[-1]]) if prior else None
        sig, ind, lv, trades, spotted = S.live_signal(t, days[last_day], prev, cfg)
        details[t] = (sig, ind, lv, trades, last_day, spotted)
        rows.append({"Ticker": t, "Grade": grades.get(t, ""), "Status": sig.status,
                     "Structure": sig.structure, "Setup": sig.setup,
                     "Entry": sig.entry, "Stop": sig.stop, "Target": sig.target, "Shares": sig.shares,
                     "Risk $": sig.risk_dollars,
                     "Since": fmt_local(sig.time, TZ)[-5:] if sig.time is not None else "",
                     "Trend": sig.trend})

    on = [S.SETUP_NAMES[k] for k, v in sc["setups"].items() if v.get("enabled")]
    step1 = ("1) structure bullish (HH+HL or break of structure) → 2) "
             if use_structure else "")
    st.caption(f"Rules: {step1}pattern at VWAP ({', '.join(on)}) → entry on break of the pattern high · "
               f"stop below the pattern low · target {sc['target_r']:g}R · {tf}-minute candles")
    st.caption(f"Updated {fmt_local(now_et(), TZ)} your time · data from Yahoo (about 1 min behind)"
               + (f" · refreshes every {refresh_s}s" if auto_refresh else " · auto-refresh off"))
    board = pd.DataFrame(rows)
    money = lambda v: "" if v is None or pd.isna(v) else f"${v:.4g}"   # noqa: E731
    for col in ("Entry", "Stop", "Target"):
        board[col] = board[col].map(money)
    board["Risk $"] = board["Risk $"].map(lambda v: "" if v is None or pd.isna(v) else f"${v:.2f}")
    board["Shares"] = board["Shares"].map(lambda v: "" if v is None or pd.isna(v) else f"{int(v)}")
    if not use_structure:
        board = board.drop(columns=["Structure"], errors="ignore")
    st.dataframe(board, hide_index=True, width="stretch", column_config={
        "Trend": st.column_config.TextColumn(width="large"),
        "Structure": st.column_config.TextColumn(width="medium"),
    })

    # alerts for new triggers
    seen = st.session_state.setdefault("seen_triggers", set())
    new = [(t, d[0]) for t, d in details.items()
           if d[0].status.startswith("🟢") and (t, str(d[0].time)) not in seen]
    for t, sig in new:
        seen.add((t, str(sig.time)))
        st.toast(f"🟢 {t}: {sig.setup} triggered at ${sig.entry:.4g} (stop ${sig.stop:.4g})", icon="🔔")
    if new and sound_on:
        components.html(BEEP, height=0)

    # chart for one stock
    order = sorted(details, key=lambda t: ("🟢🟡👀⏳❌".find(details[t][0].status[0]), watch.index(t)))
    pick = st.selectbox("Chart", order, format_func=lambda t: f"{t} · {details[t][0].status}")
    sig, ind, lv, trades, day, spotted = details[pick]
    if day != now_et().date():
        st.info(f"Showing the last session ({day:%a %d %b}). Today's bars appear once {pick} trades today.")
    st.markdown((f"**Structure:** {sig.structure or 'n/a'}  \n" if use_structure else "") + f"**Trend:** {sig.trend}")
    if sig.entry:
        st.markdown(f"**{sig.status}: {sig.setup}.** {md(sig.note)}  \n"
                    f"Entry **\\${sig.entry:.4g}** · Stop **\\${sig.stop:.4g}** · Target **\\${sig.target:.4g}** · "
                    f"**{sig.shares} shares** risks **\\${sig.risk_dollars:.2f}**")
        for w in sig.warnings:
            st.caption(f"⚠️ {md(w)}")
    if len(ind):
        st.plotly_chart(intraday_chart(ind, lv, sig, trades, TZ, pick, spotted, tf), width="stretch")
    if trades:
        st.caption("Signals earlier today (▲ on the chart): " + " · ".join(
            f"{fmt_local(t.triggered_at, TZ)[-5:]} {S.SETUP_NAMES[t.kind]} "
            f"{'still open' if t.r is None else f'{t.outcome} {t.r:+.1f}R'}" for t in trades))
    st.caption(("HH/HL (green) and LH/LL (red) mark swing highs and lows. " if use_structure else "")
               + f"E / MS under a candle = pattern spotted at VWAP (it only becomes a trade if the next "
               f"{sc.get('entry_valid_bars', 2)} candles break its high). Paper trading only: check the live "
               "spread and Level 2 in Webull before acting. A stock can reverse right after a trigger.")


with tab_signals:
    signal_board()

# ------------------------------------------------------------------ tab: journal
with tab_journal:
    render_journal()

# ------------------------------------------------------------------ tab: backtest
with tab_backtest:
    st.subheader("Would these signals have made money?")
    st.caption("Runs the exact same signal rules on the last 8 days of 1-minute bars, only on days each stock "
               "was in play (up at least "
               f"{cfg['backtest']['min_day_change_pct']:g}% with {cfg['backtest']['min_day_volume']:,} shares). "
               "Stocks = today's screener results plus every stock from your saved scans. Bars are saved to "
               "data/bars, so the history grows each day you use the dashboard.")
    wide = st.checkbox("Also test all of today's listed movers (more trades, slower)", True,
                       help="Every NASDAQ/NYSE stock from $0.50 up 5%+ today, not only the ones that passed all filters")
    if st.button("▶ Run backtest", type="primary"):
        past = set()
        for f in Path(output.ROOT / "output").glob("*.csv"):
            if not f.name.endswith("_filterlog.csv"):
                try:
                    past |= set(pd.read_csv(f, usecols=["ticker"])["ticker"])
                except Exception:
                    pass
        extra = set()
        if wide:
            m = raw.movers
            extra = set(m[m["exchange"].isin(["NASDAQ", "NYSE", "NYSE American"])
                          & (m["quote_type"] == "EQUITY") & (m["price"] >= 0.5)
                          & ~m["ticker"].str.contains(r"[-.]", regex=True)]["ticker"])
        tickers = sorted(set(result.candidates["ticker"]) | past | extra)
        with st.spinner(f"Downloading 1-minute bars for {len(tickers)} stocks..."):
            fresh = get_bars(tuple(tickers), 8)
        merged = {}
        for t in tickers:
            parts = [x for x in (B.load_stored(t, cfg), fresh.get(t)) if x is not None and len(x)]
            if parts:
                df = pd.concat(parts)
                merged[t] = df[~df.index.duplicated(keep="last")].sort_index()
        st.session_state["bt"] = S.backtest(merged, cfg)
        flipped = {**cfg, "signals": {**sc, "structure": {**sc.get("structure", {}), "enabled": not use_structure}}}
        st.session_state["bt_other_structure"] = S.backtest(merged, flipped)

    bt_trades = st.session_state.get("bt")
    if bt_trades is not None:
        today_d = now_et().date()
        fair = bt_trades[bt_trades["date"] != today_d] if len(bt_trades) else bt_trades
        biased = bt_trades[bt_trades["date"] == today_d] if len(bt_trades) else bt_trades
        summ = S.summarize(fair, cfg)
        st.markdown("#### Fair test: earlier days")
        if not summ["trades"]:
            st.warning("No signals triggered on earlier days yet. History grows as bars are saved each day.")
        else:
            k = st.columns(5)
            k[0].metric("Trades", summ["trades"], help=f"{summ['tickers']} stocks over {summ['days']} days")
            k[1].metric("Win rate", f"{summ['win_rate']:.0f}%")
            k[2].metric("Average per trade", f"{summ['avg_r']:+.2f}R")
            k[3].metric("Total", f"{summ['total_r']:+.1f}R")
            k[4].metric("Paper P&L", f"${summ['dollars']:+,.0f}",
                        help=f"at ${sc['account_size'] * sc['risk_per_trade_pct'] / 100:,.0f} risk per trade")
            by = fair.groupby("setup")["r"].agg(trades="count", win_rate=lambda r: (r > 0).mean() * 100,
                                                avg_r="mean", total_r="sum").reset_index()
            st.dataframe(by, hide_index=True, width="stretch", column_config={
                "win_rate": st.column_config.NumberColumn("Win rate", format="%.0f%%"),
                "avg_r": st.column_config.NumberColumn("Avg R", format="%+.2f"),
                "total_r": st.column_config.NumberColumn("Total R", format="%+.1f")})
        other = st.session_state.get("bt_other_structure")
        if other is not None and len(other):
            o = S.summarize(other[other["date"] != today_d], cfg)
            if o["trades"]:
                st.markdown(
                    f"**Does the structure rule help?** Same patterns {'WITHOUT' if use_structure else 'WITH'} "
                    "the market-structure rule on earlier days: "
                    f"{o['trades']} trades, {o['win_rate']:.0f}% win, {o['avg_r']:+.2f}R avg, {o['total_r']:+.1f}R total"
                    + (f" (vs {summ['trades']} trades, {summ['win_rate']:.0f}% win, {summ['avg_r']:+.2f}R avg "
                       f"{'with' if use_structure else 'without'} it)." if summ["trades"] else "."))
        if len(biased):
            b = S.summarize(biased, cfg)
            st.caption(f"Today (not counted above): {b['trades']} trades, {b['win_rate']:.0f}% win, "
                       f"{b['avg_r']:+.2f}R avg. Ignore this number: these stocks were chosen BECAUSE they "
                       "went up today, so today always looks better than it really is.")
        if len(bt_trades):
            st.dataframe(bt_trades.sort_values(["date", "time_et"], ascending=False), hide_index=True,
                         width="stretch", column_config={"r": st.column_config.NumberColumn("R", format="%+.2f")})
            st.caption("1R = the amount risked. With a 2R target you need to win about 1 trade in 3 to break even "
                       "(before costs). Small samples are noisy: treat this as a first look, not proof. "
                       "Past days use today's list of stocks, so it is not a perfect test of what you'd have seen live.")

# ------------------------------------------------------------------ tab: results
with tab_scan:
    cands = result.candidates
    k = st.columns(4)
    k[0].metric("Stocks passing", len(cands))
    k[1].metric("Started with", len(raw.movers), help="US stocks under max price, up at least min % today")
    k[2].metric("Unknown float", int(cands["float"].isna().sum()))
    k[3].metric("Top relative volume", f"{cands['rvol'].max():.0f}x" if len(cands) else "-")

    for note in result.notes:
        st.caption(f"• {note}")

    if cands.empty:
        st.warning("No stocks pass all the rules right now. Try switching a filter off in the sidebar.")
    else:
        table = ranked if ranked is not None else cands
        cols = ["ticker", "name", "price", "change_pct", "volume", "rvol", "float",
                "market_cap", "exchange", "rvol_method"]
        if ranked is not None:
            cols = ["grade", "score", "ticker", "catalyst", "headline"] + cols[1:]
        view = table[cols].copy()
        view["float"] = view["float"] / 1e6
        view["market_cap"] = view["market_cap"] / 1e6
        view["volume"] = view["volume"] / 1e6
        view.insert(view.columns.get_loc("ticker") + 1, "chart",
                    "https://finance.yahoo.com/quote/" + view["ticker"])
        st.dataframe(
            view, width="stretch", height=min(38 * (len(view) + 1), 700),
            column_config={
                "grade": st.column_config.TextColumn("Grade"),
                "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                "catalyst": st.column_config.TextColumn("Catalyst"),
                "headline": st.column_config.TextColumn("Main headline", width="large"),
                "ticker": st.column_config.TextColumn("Ticker"),
                "chart": st.column_config.LinkColumn("Chart", display_text="open ↗"),
                "name": st.column_config.TextColumn("Company", width="medium"),
                "price": st.column_config.NumberColumn("Price", format="$%.4f"),
                "change_pct": st.column_config.NumberColumn("% Change", format="+%.1f%%"),
                "volume": st.column_config.NumberColumn("Volume (M)", format="%.2f"),
                "rvol": st.column_config.NumberColumn("Rel Vol", format="%.1fx",
                                                      help="Today's volume vs normal for this time of day"),
                "float": st.column_config.NumberColumn("Float (M)", format="%.2f",
                                                       help="Empty = unknown (kept on purpose)"),
                "market_cap": st.column_config.NumberColumn("Mkt Cap ($M)", format="%.1f"),
                "exchange": st.column_config.TextColumn("Exchange"),
                "rvol_method": st.column_config.TextColumn("Rel Vol method"),
            },
        )
        st.caption(("Sorted by day-trading score. " if ranked is not None else
                    "Ranked by relative volume, then % change. ")
                   + "Empty Float = unknown (kept, not dropped). Click a column header to re-sort.")
    st.caption(f"Saved this scan to `{st.session_state.get('saved_path')}`")

# ------------------------------------------------------------------ tab: catalysts
with tab_news:
    if ranked is None:
        st.info("Switch on **Check news & SEC filings** in the sidebar to see catalysts.")
    else:
        st.caption("🟢 strong · 🟡 weak · 🔴 danger (offering, reverse split, bad results) · "
                   "⚪ unclear / market round-up. Keywords are editable in config.yaml.")
        for _, r in ranked.iterrows():
            info = catalysts.get(r["ticker"])
            with st.expander(f"{r['ticker']} · {r['grade']} ({r['score']}) · {r['catalyst']} · "
                             f"{r['change_pct']:+.0f}%"):
                st.markdown("**Why this score:** " + md(r["reasons"]))
                st.markdown("**Recent headlines**")
                if info and info.headlines:
                    st.markdown("\n".join(
                        f"- {TAG_ICON[h.tag]} [{md(h.title)}]({h.link}) · {fmt_local(h.published, TZ)} "
                        f"({age_text(h.age_hours)})" for h in info.headlines[:8]))
                else:
                    st.caption("No headlines found.")
                st.markdown(f"**SEC filings (last {cfg['catalyst'].get('filing_lookback_days', 90)} days)**")
                if info and info.filings:
                    st.dataframe(pd.DataFrame([{
                        "Filed": f.filed.isoformat(), "Form": f.form, "8-K items": f.items,
                        "Red flag": FLAG_TEXT.get(f.flag, "") if f.flag else "", "Document": f.url,
                    } for f in info.filings]), hide_index=True, width="stretch",
                        column_config={"Document": st.column_config.LinkColumn(display_text="open ↗")})
                else:
                    st.caption("No filings in this period.")
                for err in (info.errors if info else []):
                    st.caption(f"⚠️ {err}")

# ------------------------------------------------------------------ tab: funnel
with tab_funnel:
    f = result.funnel.iloc[1:].copy()
    st.subheader("If each rule were used on its own, how many stocks would it remove?")
    st.caption(f"Out of the {len(raw.movers)} US stocks under \\${s['max_price']:g} that are up "
               f"{s['min_change_pct']:g}% or more today"
               + (f" (from {raw.universe_total:,} US stocks under \\${s['max_price']:g} trading today)."
                  if raw.universe_total else "."))
    chart = alt.Chart(f).mark_bar().encode(
        x=alt.X("would_remove_on_its_own:Q", title="stocks removed"),
        y=alt.Y("rule:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
        tooltip=["rule", "would_remove_on_its_own"],
    )
    st.altair_chart(chart, width="stretch")
    st.subheader("Step by step (rules applied in order)")
    st.dataframe(result.funnel.rename(columns={
        "rule": "Rule", "remaining": "Stocks left", "removed_at_this_step": "Removed at this step",
        "would_remove_on_its_own": "Would remove on its own"}), width="stretch", hide_index=True)

# ------------------------------------------------------------------ tab: roadmap
with tab_next:
    st.markdown("""
These panels will plug into the same screener results:

- **Premarket mode**: scan 45–60 min before the open.
- **Alerts**: get notified when a new stock passes the screener.
""")
