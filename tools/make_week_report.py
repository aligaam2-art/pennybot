"""Build reports/<folder>/report.html from trades.json + trade charts + dashboard screenshots."""
from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

RISK = 10  # $ risked per trade (1% of the $1,000 paper account)

TOUR = [
    ("01_overview", "1. The top of the page",
     "Your time and New York time, whether the US market is open, and today's opening and closing times in "
     "Dar es Salaam time. The blue box shows when the data was downloaded (free Yahoo data, a minute or two late)."),
    ("02_sidebar", "2. The sidebar: your screener rules",
     "All the filters from your Webull screener plus the extra ones (min price, relative volume, float). Change a "
     "number to see what it does: it only lasts for this session. To change the default for good, edit "
     "config.yaml. <b>Refresh data</b> downloads a new scan. At the bottom: news check on/off, auto-refresh and "
     "the beep for new signals."),
    ("03_top_picks", "3. Top 3 for day trading",
     "The three best-scored stocks. Each card shows the grade (A / B / C / Avoid), the score out of 100, the "
     "main headline, and every reason that added or removed points. Use <b>Chart</b> and <b>SEC filings</b> to "
     "check the story yourself. This is a shortlist to study, not a buy list."),
    ("04_signal_board", "4. Charts &amp; signals: the signal board",
     "This is where entries come from. For the best stocks it shows the status: 👀 watching for a pattern, "
     "🟡 setup forming (entry, stop, target and share size filled in), 🟢 triggered (the entry price was hit), "
     "❌ no longs (downtrend). It refreshes every 60 seconds and beeps on a new trigger."),
    ("05_chart", "5. The chart",
     "5-minute candles with VWAP (orange), 9 EMA (blue) and 20 EMA (purple), volume underneath, and dotted "
     "lines for yesterday's close, premarket high and high of day. <b>E</b> or <b>MS</b> under a candle = an "
     "engulfing or morning-star pattern at VWAP. ▲ = where an entry triggered. Pick another stock from the "
     "<b>Chart</b> drop-down. Hover for prices; drag to zoom; double-click to reset."),
    ("06_screener", "6. Screener tab",
     "Every stock that passed your filters, sorted by score, with the catalyst type and main headline, price, "
     "% change, volume, relative volume, float and market cap. Click a column header to re-sort."),
    ("07_catalysts", "7. Catalysts &amp; filings tab",
     "Open a stock to see why it got its score, its recent headlines tagged 🟢 strong / 🟡 weak / 🔴 danger, and "
     "its SEC filings with red flags (share sales, delisting notices, reverse-split votes)."),
    ("08_backtest", "8. Backtest tab",
     "Click <b>Run backtest</b> to test the signal rules on past days. The headline numbers are the <b>fair test</b> "
     "(earlier days only). The line below compares the market-structure rule, and today's numbers are shown "
     "separately because they are always flattering. Every trade is listed underneath."),
    ("09_filter_log", "9. Which rules cut the most",
     "How many stocks each screener rule removes. Useful when you wonder why a stock you saw in Webull is "
     "missing here."),
]


def stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "wins": 0, "win": 0, "r": 0, "usd": 0}
    total = sum(t["r"] for t in trades)
    wins = sum(t["r"] > 0 for t in trades)
    return {"n": n, "wins": wins, "win": wins / n * 100, "r": total, "usd": total * RISK, "avg": total / n}


def card(label: str, s: dict, note: str, tone: str) -> str:
    if not s["n"]:
        return ""
    return f"""<div class="stat {tone}"><div class="lbl">{label}</div>
      <div class="big">{'+' if s['usd'] >= 0 else '−'}${abs(s['usd']):.0f}</div>
      <div>{s['n']} trades · {s['wins']} wins ({s['win']:.0f}%) · {s['r']:+.1f}R</div>
      <div class="note">{note}</div></div>"""


def main(folder: Path, today: str) -> None:
    trades = json.loads((folder / "trades.json").read_text())
    fair = [t for t in trades if t["date"] != today]
    biased = [t for t in trades if t["date"] == today]
    days = sorted({t["date"] for t in trades})

    rows = []
    for t in trades:
        win = t["r"] > 0
        rows.append(f"""<section class="trade" id="t{t['n']}">
  <h3>#{t['n']} · {escape(t['ticker'])} · {t['date']} · {escape(t['setup'])}
    <span class="pill {'win' if win else 'loss'}">{t['outcome']} {t['r']:+.2f}R ({'+' if win else '−'}${abs(t['r']) * RISK:.0f})</span>
    {'<span class="pill today">today: biased</span>' if t['date'] == today else ''}</h3>
  <img src="{t['img']}" alt="{escape(t['ticker'])} trade chart" loading="lazy">
  <table><tr><th>Entry time</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit time</th><th>Exit</th></tr>
  <tr><td>{t['entry_local']}</td><td>${t['entry']:.4g}</td><td>${t['stop']:.4g}</td><td>${t['target']:.4g}</td>
  <td>{t['exit_local']}</td><td>${t['exit']:.4g}</td></tr></table>
</section>""")

    per_day = "".join(
        f"<tr><td>{d}</td><td>{s['n']}</td><td>{s['wins']}</td><td>{s['r']:+.1f}R</td><td>{'+' if s['usd'] >= 0 else '−'}${abs(s['usd']):.0f}</td>"
        f"<td>{'biased (today)' if d == today else 'fair'}</td></tr>"
        for d in days for s in [stats([t for t in trades if t['date'] == d])])

    tour = "".join(f"""<section class="tour"><h3>{title}</h3><p>{text}</p>
  <img src="dashboard/{img}.png" alt="{title}" loading="lazy"></section>""" for img, title, text in TOUR)

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pennybot week review</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d1d1f; --muted:#666; --line:#e4e4e0; --win:#1b8a5a; --loss:#c62828; --accent:#1e66d0; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#16171a; --card:#1f2024; --ink:#ececec; --muted:#a0a0a0; --line:#33343a; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:1.9rem; margin:.2em 0; }} h2 {{ margin-top:2.2em; border-bottom:1px solid var(--line); padding-bottom:.3em; }}
h3 {{ font-size:1.05rem; display:flex; flex-wrap:wrap; gap:.5em; align-items:center; }}
.sub {{ color:var(--muted); }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin:16px 0; }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.stat .lbl {{ font-size:.85rem; color:var(--muted); }} .stat .big {{ font-size:1.8rem; font-weight:700; }}
.stat.fair {{ border-left:4px solid var(--win); }} .stat.biased {{ border-left:4px solid #e0a000; }} .stat.all {{ border-left:4px solid var(--accent); }}
.note {{ font-size:.82rem; color:var(--muted); margin-top:4px; }}
.callout {{ background:var(--card); border:1px solid var(--line); border-left:4px solid #e0a000; border-radius:8px; padding:12px 14px; }}
table {{ border-collapse:collapse; width:100%; background:var(--card); font-size:.92rem; }}
th, td {{ border:1px solid var(--line); padding:6px 8px; text-align:left; }} th {{ color:var(--muted); font-weight:600; }}
.trade, .tour {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; margin:16px 0; }}
img {{ max-width:100%; height:auto; border-radius:6px; border:1px solid var(--line); display:block; margin:8px 0; }}
.pill {{ font-size:.8rem; padding:2px 8px; border-radius:99px; color:#fff; }}
.pill.win {{ background:var(--win); }} .pill.loss {{ background:var(--loss); }} .pill.today {{ background:#b07d00; }}
.legend li {{ margin:.2em 0; }} nav a {{ color:var(--accent); }}
</style></head><body><main>
<h1>Pennybot week review</h1>
<p class="sub">Monday 5 – Wednesday 7 October 2026 · VWAP + candlestick setups on 5-minute candles · $1,000 paper account,
${RISK} risk per trade · all times Dar es Salaam (UTC+3)</p>
<nav><a href="#results">Results</a> · <a href="#read">How to read a trade chart</a> · <a href="#trades">All {len(trades)} trades</a> ·
<a href="#tour">Dashboard tour</a></nav>

<h2 id="results">Results</h2>
<div class="stats">
{card("Monday + Tuesday (fair test)", stats(fair), "The honest number: these stocks were not picked using these days.", "fair")}
{card("Wednesday (today)", stats(biased), "Flattering: stocks were picked BECAUSE they went up today.", "biased")}
{card("Whole week", stats(trades), "Mixes the fair and the flattering days.", "all")}
</div>
<table><tr><th>Day</th><th>Trades</th><th>Wins</th><th>Result</th><th>Paper $</th><th>Test type</th></tr>{per_day}</table>
<div class="callout" style="margin-top:14px"><b>Read this before trusting the numbers.</b> We can only test stocks we know about:
today's movers plus stocks from saved scans. Stocks that ran on Monday or Tuesday but not today are missing, so Monday and
Tuesday have few trades. {len(fair)} fair trades is far too few to prove the setup works. The dashboard saves price data every
day, so this test becomes more reliable each week. Fills include 0.2% slippage each way; real penny-stock fills can be worse.</div>

<h2 id="read">How to read a trade chart</h2>
<ul class="legend">
<li><b>Candles</b>: 5 minutes each. Green = closed higher, red = closed lower. Volume bars are at the bottom.</li>
<li><b>Orange line = VWAP</b>, blue = 9 EMA, purple = 20 EMA.</li>
<li><b>E</b> / <b>MS</b> under a candle = bullish engulfing / morning star found at VWAP.</li>
<li><b>▲ blue triangle</b> = entry: the next candle broke the pattern's high.</li>
<li><b>Blue / red / green lines</b> = entry, stop (below the pattern low) and target (2× the risk).</li>
<li><b>✕</b> = exit: green if the target was hit, red if the stop was hit. "time" = closed at 15:55 New York time (22:55 yours).</li>
<li><b>R</b> = result in units of risk. +1.9R ≈ +$19, −1.1R ≈ −$11 with $10 risked (slippage makes it not exactly 2 and −1).</li>
</ul>

<h2 id="trades">All {len(trades)} trades</h2>
{''.join(rows)}

<h2 id="tour">Dashboard tour</h2>
<p>Open the dashboard by double-clicking <b>start_dashboard.bat</b> in Documents\\pennybot. Here is what each part does,
top to bottom.</p>
{tour}
<p class="sub">Paper trading and learning only. Not financial advice.</p>
</main></body></html>"""
    (folder / "report.html").write_text(html, encoding="utf-8")
    print("wrote", folder / "report.html")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
