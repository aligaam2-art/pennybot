"""Automatic paper-trading journal. Runs unattended (GitHub Actions) during US market hours.

Every few minutes it does exactly what the dashboard does - screener, catalyst
scores, VWAP + candlestick signals on 5-minute candles - and logs each new
signal as a paper trade. Only signals it actually sees live are logged (no
hindsight): a trade counts only if the stock was already on the watch list
when the entry triggered.

After the close it settles every trade (target / stop / end-of-day exit),
saves the day's 1-minute bars for future backtests and writes a summary.

Files (all under journal/, committed to the repository):
  trades.csv                  one row per paper trade
  summary.csv                 one row per trading day
  days/<date>/watchlist.csv   when each stock first made the watch list
  days/<date>/scan_<HHMM>.csv screener results every 30 minutes
  days/<date>/movers.csv      every listed mover with 1M+ volume (their bars are saved too)
  days/<date>/finalized       marker: the day is settled
  bars/<ticker>/<date>.parquet

Usage:
  python -m pennybot.journal run [--max-minutes 340]   loop until the close (or the time limit)
  python -m pennybot.journal once                      one check now (testing)
  python -m pennybot.journal finalize [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time as systime
from datetime import date, datetime, time, timedelta

import pandas as pd

from . import bars as B
from . import notify
from . import signals as S
from .catalyst import check_catalysts
from .clock import ET, REGULAR_CLOSE, REGULAR_OPEN, now_et
from .config import ROOT, load_config
from .screener import apply_filters, load_candidates
from .selection import rank

JOURNAL = ROOT / "journal"
TRADES = JOURNAL / "trades.csv"
SUMMARY = JOURNAL / "summary.csv"
COLUMNS = ["date", "ticker", "grade", "score", "catalyst", "headline", "setup", "signal_time_et",
           "logged_at_et", "entry", "stop", "target", "shares", "risk_dollars", "status", "outcome",
           "exit_time_et", "exit_price", "r", "pnl_dollars"]
POLL_MINUTES = 5
SCAN_SAVE_MINUTES = 30
COMMIT_MINUTES = 30


def log(msg: str) -> None:
    print(f"[{now_et():%H:%M:%S} ET] {msg}", flush=True)


def day_dir(d: date):
    p = JOURNAL / "days" / d.isoformat()
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_trades() -> pd.DataFrame:
    if TRADES.exists():
        return pd.read_csv(TRADES, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=COLUMNS, dtype=str)


def settle(row: pd.Series, day5: pd.DataFrame, cfg: dict, final: bool) -> list | None:
    """Follow a logged trade from its own entry/stop/target through later candles.

    Returns [status, outcome, exit_time, exit_price, r, pnl] once the stop or target
    is hit (or at the end-of-day exit / when `final`), else None. Independent of
    re-detecting the pattern, so later revisions to Yahoo's data can't lose a trade.
    """
    sc = cfg["signals"]
    exit_t = time.fromisoformat(cfg.get("backtest", {}).get("exit_time", "15:55"))
    slip = sc.get("slippage_pct", 0.2) / 100
    entry, stop, target = float(row["entry"]), float(row["stop"]), float(row["target"])
    fill = entry * (1 + slip)
    trig = time.fromisoformat(row["signal_time_et"])
    after = day5[day5.index.time > trig]
    exit_px = exit_ts = how = None
    for ts, bar in after.iterrows():
        if bar["low"] <= stop:
            exit_px, how = min(stop, bar["open"]), "stop"
        elif bar["high"] >= target:
            exit_px, how = max(target, bar["open"]), "target"
        elif ts.time() >= exit_t:
            exit_px, how = bar["close"], "time"
        if how:
            exit_ts = ts
            break
    if how is None:
        if not final or after.empty:
            return None
        exit_ts, exit_px, how = after.index[-1], after["close"].iloc[-1], "time"
    exit_fill = exit_px * (1 - slip)
    r = round((exit_fill - fill) / (entry - stop), 2)
    pnl = round(r * sc["account_size"] * sc["risk_per_trade_pct"] / 100, 2)
    return ["closed", how, exit_ts.strftime("%H:%M"), round(exit_fill, 4), r, pnl]


RESULT_COLS = ["status", "outcome", "exit_time_et", "exit_price", "r", "pnl_dollars"]


def save_trades(df: pd.DataFrame) -> None:
    JOURNAL.mkdir(exist_ok=True)
    df[COLUMNS].to_csv(TRADES, index=False)


def first_seen(d: date) -> dict[str, pd.Timestamp]:
    path = day_dir(d) / "watchlist.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {r.ticker: pd.Timestamp(r.first_seen_et).tz_localize(ET) if pd.Timestamp(r.first_seen_et).tzinfo is None
            else pd.Timestamp(r.first_seen_et) for r in df.itertuples()}


def save_first_seen(d: date, seen: dict[str, pd.Timestamp], grades: dict[str, tuple]) -> None:
    rows = [{"ticker": t, "first_seen_et": ts.isoformat(), "grade": grades.get(t, ("", ""))[0],
             "score": grades.get(t, ("", ""))[1]} for t, ts in sorted(seen.items(), key=lambda x: x[1])]
    pd.DataFrame(rows).to_csv(day_dir(d) / "watchlist.csv", index=False)


def git_commit(message: str) -> None:
    """Commit and push journal/ when running on GitHub Actions (no-op on your laptop)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "add", "journal"], cwd=ROOT, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
            return
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=ROOT, check=True)
        subprocess.run(["git", "pull", "-q", "--rebase", "-X", "theirs"], cwd=ROOT, check=False)
        subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True)
        log("pushed journal")
    except subprocess.CalledProcessError as e:
        log(f"git push failed: {e}")


# ---------------------------------------------------------------- one check

class Session:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sc = cfg["signals"]
        self.today = now_et().date()
        self.seen = first_seen(self.today)
        self.grades: dict[str, tuple] = {}
        self.catalyst_cache: dict = {}
        self.catalyst_time: dict[str, datetime] = {}
        self.all_tickers: set[str] = set(self.seen)
        self.last_scan_save: datetime | None = None

    def catalysts(self, tickers: list[str]) -> dict:
        now = now_et()
        stale = [t for t in tickers
                 if t not in self.catalyst_cache or now - self.catalyst_time[t] > timedelta(minutes=30)]
        if stale:
            self.catalyst_cache.update(check_catalysts(stale, self.cfg))
            self.catalyst_time.update({t: now for t in stale})
        return {t: self.catalyst_cache[t] for t in tickers if t in self.catalyst_cache}

    def save_movers(self, movers: pd.DataFrame) -> None:
        """Remember every listed mover with real volume, so its bars are saved for future backtests."""
        sc = self.cfg["screener"]
        m = movers[movers["exchange"].isin(sc["exchanges"]) & (movers["quote_type"] == "EQUITY")
                   & (movers["price"] >= sc["min_price"]) & (movers["volume"] >= sc["min_volume"]["intraday"])
                   & ~movers["ticker"].str.contains(r"[-.]", regex=True)]
        path = day_dir(self.today) / "movers.csv"
        old = set(pd.read_csv(path)["ticker"]) if path.exists() else set()
        pd.DataFrame({"ticker": sorted(old | set(m["ticker"]))}).to_csv(path, index=False)

    def watch_list(self) -> tuple[pd.DataFrame, list[str]]:
        raw = load_candidates(self.cfg)
        self.save_movers(raw.movers)
        result = apply_filters(raw, self.cfg["screener"], "intraday")
        if result.candidates.empty:
            return pd.DataFrame(), []
        ranked = rank(result.candidates, self.catalysts(result.candidates["ticker"].tolist()), self.cfg)
        self.all_tickers |= set(ranked["ticker"])
        now = now_et()
        if not self.last_scan_save or now - self.last_scan_save >= timedelta(minutes=SCAN_SAVE_MINUTES):
            cols = ["ticker", "grade", "score", "catalyst", "price", "change_pct", "volume", "rvol", "float",
                    "market_cap", "headline", "reasons"]
            ranked[cols].to_csv(day_dir(self.today) / f"scan_{now:%H%M}.csv", index=False)
            self.last_scan_save = now
        watch = ranked[~ranked["grade"].isin(self.sc.get("skip_grades", ["Avoid"]))]
        watch = watch.head(self.sc.get("max_stocks_watched", 8))
        return ranked, watch["ticker"].tolist()

    def check(self) -> None:
        now = now_et()
        if now.weekday() >= 5:
            log("weekend - the US market is closed, nothing to check")
            return
        ranked, watch = self.watch_list()
        for _, r in ranked.iterrows():
            self.grades[r["ticker"]] = (r["grade"], int(r["score"]))
        for t in watch:
            self.seen.setdefault(t, pd.Timestamp(now))
        save_first_seen(self.today, self.seen, self.grades)

        trades = load_trades()
        open_today = trades[(trades["date"] == self.today.isoformat()) & (trades["status"] == "open")]
        tickers = sorted(set(watch) | set(open_today["ticker"]))
        bars = B.download_bars(tickers, days=1)
        missing = [t for t in tickers if t not in bars]
        if missing:
            log(f"WARNING Yahoo returned no price data for {len(missing)}/{len(tickers)}: {', '.join(missing)}")
        info = ranked.set_index("ticker") if len(ranked) else pd.DataFrame()
        new_rows = []
        for t in tickers:
            df = bars.get(t)
            if df is None or df.empty:
                continue
            day = B.resample(df[df.index.date == self.today], self.sc.get("timeframe_minutes", 5))
            tf = pd.Timedelta(minutes=self.sc.get("timeframe_minutes", 5))
            if len(day) and day.index[-1] + tf > pd.Timestamp(now):
                day = day.iloc[:-1]                 # candle still forming
            if len(day) < 3:
                continue
            for i in trades.index[(trades["ticker"] == t) & (trades["date"] == self.today.isoformat())
                                  & (trades["status"] == "open")]:
                res = settle(trades.loc[i], day, self.cfg, final=False)
                if res:
                    trades.loc[i, RESULT_COLS] = [str(x) for x in res]
                    log(f"{t} closed: {res[1]} {res[4]:+.2f}R")
                    self.alert_closed(trades.loc[i])
            day_trades, _, _ = S.run_day(day, t, self.cfg)
            for tr in day_trades:
                key = (trades["ticker"] == t) & (trades["date"] == self.today.isoformat()) \
                    & (trades["signal_time_et"] == tr.triggered_at.strftime("%H:%M"))
                if key.any():                       # already logged
                    continue
                if t not in self.seen or tr.triggered_at < self.seen[t] - tf:
                    continue                        # happened before we were watching: hindsight
                shares, risk_d = S.position_size(tr.entry, tr.stop, self.sc)
                g = info.loc[t] if t in info.index else None
                row = {"date": self.today.isoformat(), "ticker": t,
                       "grade": g["grade"] if g is not None else "", "score": g["score"] if g is not None else "",
                       "catalyst": g["catalyst"] if g is not None else "",
                       "headline": g["headline"] if g is not None else "",
                       "setup": S.SETUP_NAMES[tr.kind], "signal_time_et": tr.triggered_at.strftime("%H:%M"),
                       "logged_at_et": now.strftime("%H:%M"), "entry": tr.entry, "stop": tr.stop,
                       "target": tr.target, "shares": shares, "risk_dollars": risk_d, "status": "open",
                       "outcome": "", "exit_time_et": "", "exit_price": "", "r": "", "pnl_dollars": ""}
                res = settle(pd.Series(row), day, self.cfg, final=False)
                if res:
                    row.update(dict(zip(RESULT_COLS, res)))
                new_rows.append({k: str(v) for k, v in row.items()})
                log(f"NEW SIGNAL {t} {row['setup']} entry {tr.entry} stop {tr.stop} target {tr.target}")
                self.alert_signal(row)
                if res:
                    self.alert_closed(pd.Series({k: str(v) for k, v in row.items()}))
        if new_rows:
            trades = pd.concat([trades, pd.DataFrame(new_rows, dtype=str)], ignore_index=True)
        save_trades(trades)
        log(f"checked {len(watch)} watched stocks: {', '.join(watch) or '-'}")
        started = day_dir(self.today) / "start_alert_sent"
        if self.cfg.get("alerts", {}).get("daily_start_message", True) and not started.exists() and watch:
            if notify.send(self.cfg, "Pennybot is watching the market",
                           f"Watching {len(watch)} stocks: {', '.join(watch)}.\n"
                           "You'll get an alert here when a signal triggers.", tags="eyes", priority="low"):
                started.write_text(now.isoformat())

    def alert_signal(self, row: dict) -> None:
        when = notify.local_hhmm(row["date"], row["signal_time_et"], self.cfg)
        notify.send(self.cfg, f"SIGNAL {row['ticker']} ({row['grade'] or '-'})",
                    f"{row['setup']} at {when} your time\n"
                    f"Entry ${float(row['entry']):.4g} · Stop ${float(row['stop']):.4g} · "
                    f"Target ${float(row['target']):.4g}\n"
                    f"{row['shares']} shares, risk ${float(row['risk_dollars']):.2f} (paper)\n"
                    f"{row['headline'] or ''}".strip(),
                    tags="green_circle,chart_with_upwards_trend", priority="high")

    def alert_closed(self, row: pd.Series) -> None:
        r = float(row["r"])
        when = notify.local_hhmm(row["date"], row["exit_time_et"], self.cfg)
        word = {"target": "TARGET HIT", "stop": "STOPPED OUT", "time": "CLOSED AT END OF DAY"}.get(row["outcome"],
                                                                                                row["outcome"])
        notify.send(self.cfg, f"{word} {row['ticker']} {r:+.2f}R",
                    f"{row['setup']}: exit ${float(row['exit_price']):.4g} at {when} your time\n"
                    f"Paper result: {r:+.2f}R = ${float(row['pnl_dollars']):+.2f}",
                    tags="white_check_mark" if r > 0 else "x")


# ---------------------------------------------------------------- end of day

def finalize(cfg: dict, d: date | None = None, force: bool = False) -> None:
    d = d or now_et().date()
    folder = day_dir(d)
    if (folder / "finalized").exists() and not force:
        log(f"{d} already finalized")
        return
    sc = cfg["signals"]
    tickers = set(first_seen(d))
    for f in [*folder.glob("scan_*.csv"), folder / "movers.csv"]:
        if f.exists():
            tickers |= set(pd.read_csv(f)["ticker"])
    trades = load_trades()
    mask = trades["date"] == d.isoformat()
    tickers |= set(trades.loc[mask, "ticker"])
    bars = B.download_bars(sorted(tickers), days=8) if tickers else {}

    for t, df in bars.items():                     # save the day's bars for future backtests
        day = df[df.index.date == d]
        if len(day):
            out = JOURNAL / "bars" / t
            out.mkdir(parents=True, exist_ok=True)
            day.to_parquet(out / f"{d.isoformat()}.parquet")

    for i in trades.index[mask & (trades["status"] == "open")]:   # settle trades still open
        t = trades.at[i, "ticker"]
        if t not in bars:
            continue
        day = B.resample(bars[t][bars[t].index.date == d], sc.get("timeframe_minutes", 5))
        res = settle(trades.loc[i], day, cfg, final=True)
        if res:
            trades.loc[i, RESULT_COLS] = [str(x) for x in res]
    save_trades(trades)

    day_trades = trades[mask & (trades["status"] == "closed")]
    r = pd.to_numeric(day_trades["r"])
    row = {"date": d.isoformat(), "trades": len(day_trades), "wins": int((r > 0).sum()),
           "total_r": round(r.sum(), 2), "pnl_dollars": round(pd.to_numeric(day_trades["pnl_dollars"]).sum(), 2),
           "stocks_watched": len(first_seen(d))}
    summ = pd.read_csv(SUMMARY, dtype={"date": str}) if SUMMARY.exists() else pd.DataFrame()
    summ = pd.concat([summ[summ.get("date", pd.Series(dtype=str)) != d.isoformat()] if len(summ) else summ,
                      pd.DataFrame([row])], ignore_index=True)
    summ.to_csv(SUMMARY, index=False)
    lines = [f"# {d:%A %d %B %Y}", "",
             f"{row['trades']} paper trades, {row['wins']} wins, {row['total_r']:+.2f}R, ${row['pnl_dollars']:+.2f}",
             f"Stocks watched: {row['stocks_watched']}", ""]
    for _, t in day_trades.iterrows():
        lines.append(f"- {t['ticker']} ({t['grade']}) {t['setup']} at {t['signal_time_et']} ET: "
                     f"{t['outcome']} {float(t['r']):+.2f}R")
    (folder / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (folder / "finalized").write_text(now_et().isoformat())
    log("finalized: " + lines[2])
    if cfg.get("alerts", {}).get("daily_summary", True) and not force:
        notify.send(cfg, f"Pennybot day summary {d:%a %d %b}",
                    "\n".join([lines[2]] + lines[5:]) or lines[2], tags="memo", priority="low")


# ---------------------------------------------------------------- main loop

def market_open_today(cfg: dict) -> bool:
    """False on weekends and US market holidays (no SPY trades after the open)."""
    now = now_et()
    if now.weekday() >= 5:
        return False
    if now.time() < time(9, 35):
        return True                                 # can't tell yet; assume open
    spy = B.download_bars(["SPY"], days=1).get("SPY")
    return spy is not None and (spy.index.date == now.date()).any()


def run(cfg: dict, max_minutes: int) -> None:
    start = now_et()
    deadline = start + timedelta(minutes=max_minutes)
    today = start.date()
    if (day_dir(today) / "finalized").exists():
        log("today is already finalized - nothing to do")
        return
    open_dt = datetime.combine(today, REGULAR_OPEN, tzinfo=ET)
    close_dt = datetime.combine(today, REGULAR_CLOSE, tzinfo=ET)
    if not market_open_today(cfg):
        log("market closed today (weekend or holiday)")
        return
    if start < open_dt - timedelta(minutes=60):
        log("more than 60 minutes before the open - a later scheduled run will start the session")
        return

    session, last_commit = None, now_et()
    while True:
        now = now_et()
        if now >= close_dt + timedelta(minutes=10):
            if session:
                session.check()
            finalize(cfg, today)
            git_commit(f"journal: {today} final")
            return
        if now >= deadline:
            log("time limit reached - saving; the next scheduled run continues")
            git_commit(f"journal: {today} {now:%H:%M}")
            return
        if now >= open_dt + timedelta(minutes=5):
            session = session or Session(cfg)
            try:
                session.check()
            except Exception as e:                  # keep going on network hiccups
                log(f"check failed: {e!r}")
            if now_et() - last_commit >= timedelta(minutes=COMMIT_MINUTES):
                git_commit(f"journal: {today} {now_et():%H:%M}")
                last_commit = now_et()
        else:
            log("waiting for the open")
        next_tick = now + timedelta(minutes=POLL_MINUTES)
        systime.sleep(max(5.0, (next_tick - now_et()).total_seconds()))


def main() -> None:
    p = argparse.ArgumentParser(description="Automatic paper-trading journal")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max-minutes", type=int, default=340)
    sub.add_parser("once")
    f = sub.add_parser("finalize")
    f.add_argument("--date", type=date.fromisoformat)
    f.add_argument("--force", action="store_true", help="redo a day that is already finalized")
    a = p.parse_args()
    cfg = load_config()
    if a.cmd == "run":
        run(cfg, a.max_minutes)
    elif a.cmd == "once":
        Session(cfg).check()
        git_commit(f"journal: manual check {now_et():%Y-%m-%d %H:%M}")
    else:
        finalize(cfg, a.date, a.force)
        git_commit(f"journal: {(a.date or now_et().date())} final")


if __name__ == "__main__":
    main()
