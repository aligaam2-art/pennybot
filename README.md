# Pennybot

Penny stock screener and dashboard for paper trading. It uses free Yahoo Finance data,
which may be a few minutes delayed.

## How to use
- **Dashboard:** double-click `start_dashboard.bat`. Your browser opens at http://localhost:8501.
  Close the black window to stop it.
- **Terminal only:** double-click `run_screener.bat`.
- **Change the rules:** edit `config.yaml` in Notepad and save, then refresh the dashboard.
- **Saved scans:** every scan is saved in `output/` as a CSV, plus a `_filterlog.csv`
  that shows how many stocks each rule removed.

## Files
- `config.yaml`: screener rules
- `dashboard.py`: the browser dashboard
- `pennybot/screener.py`: the rules and the filter log
- `pennybot/yahoo.py`: data download (swap this out to change data provider)
- `pennybot/catalyst.py`: news headlines (Yahoo) + SEC filings, tagged strong / weak / danger
- `pennybot/selection.py`: day-trading score 0-100, grade A/B/C/Avoid, with reasons
- `pennybot/bars.py`: 1-minute bars, VWAP/EMAs, key levels (bars saved to data/bars)
- `pennybot/structure.py`: market structure (swing highs/lows, HH/HL/LH/LL, break of structure)
- `pennybot/patterns.py`: candlestick patterns (bullish engulfing, morning star)
- `pennybot/signals.py`: entry signals on 5-minute candles (pattern at VWAP; older 1-min setups switched off) + backtest
- `pennybot/charts.py`: the intraday chart
- `pennybot/clock.py`: US market hours shown in Dar es Salaam time (handles daylight saving)

## Selection score
The score is a study aid from the rules in `config.yaml` (`catalyst:` and `selection:`),
not a buy signal. Before any paper trade, check in Webull: spread under ~3%, price above VWAP, no halt.

## Signals
Rules: candlestick pattern at VWAP, entry on break of the pattern high. (Market-structure filter is
built but switched off in config.yaml; the Backtest tab keeps comparing it.)
Paper trading only. Each signal gives entry, stop, target (2R) and share size for 1% risk of a
$1,000 paper account (change in config.yaml). Use the Backtest tab to see how the rules performed.

## Automatic paper-trading journal (cloud)
`pennybot/journal.py` runs on GitHub Actions (`.github/workflows/journal.yml`) every weekday during US
market hours. Every 5 minutes it runs the screener + signals and logs each live signal as a paper trade
in `journal/trades.csv`; after the close it settles the trades, saves the day's bars to `journal/bars/`
and writes `journal/days/<date>/summary.md`. See the Journal tab on the dashboard.

The journal paper-trades several strategies side by side (`strategies:` in config.yaml): the
current rules, the same with the market-structure filter, and morning-only entries. Each trade is
labelled with its strategy; the Journal tab compares them from `strategies_start`.
Run it by hand: `python -m pennybot.journal once` (one check) or `finalize` (settle today).

## Coming next
Premarket mode.
