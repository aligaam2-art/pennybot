"""Take screenshots of every dashboard section (for the user guide / reports).

Needs the dashboard running on http://localhost:8501 and Microsoft Edge installed.
Usage:  .venv\\Scripts\\python tools\\dashboard_screenshots.py <output folder> [TICKER for the chart]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"


def main(out: Path, chart_ticker: str | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        page.goto(URL)
        page.get_by_text("Top 3 for day trading").wait_for(timeout=180_000)
        page.get_by_text("Updated", exact=False).first.wait_for(timeout=180_000)
        page.wait_for_timeout(3000)

        def shot(name: str):
            page.mouse.move(5, 5)          # no hover tooltips in the picture
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / f"{name}.png"))
            print("saved", name)

        def show(text: str, offset: int = 120):
            el = page.get_by_text(text, exact=False).first
            el.evaluate("e => e.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(500)
            page.mouse.move(900, 450)
            page.mouse.wheel(0, -offset)

        shot("01_overview")
        page.locator('[data-testid="stSidebar"]').screenshot(path=str(out / "02_sidebar.png"))
        print("saved 02_sidebar")
        show("Top 3 for day trading", 60)
        shot("03_top_picks")

        # charts & signals tab (selected by default)
        show("Rules:", 80)
        shot("04_signal_board")
        if chart_ticker:
            box = page.locator('[data-testid="stSelectbox"]').filter(has_text="Chart").first
            box.click()
            page.keyboard.type(chart_ticker)
            page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
        page.locator(".js-plotly-plot").first.evaluate("e => e.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(500)
        page.mouse.move(900, 450)
        page.mouse.wheel(0, -150)
        shot("05_chart")

        page.get_by_role("tab", name="🔎 Screener").click()
        show("Stocks passing", 60)
        shot("06_screener")

        page.get_by_role("tab", name="📰 Catalysts & filings").click()
        page.locator('[data-testid="stExpander"]').nth(1).click()
        show("unclear / market round-up", 40)
        shot("07_catalysts")

        page.get_by_role("tab", name="🧪 Backtest").click()
        page.get_by_role("button", name="▶ Run backtest").click()
        page.get_by_text("Fair test: earlier days").wait_for(timeout=300_000)
        show("Fair test: earlier days", 40)
        shot("08_backtest")

        page.get_by_role("tab", name="🧹 Which rules cut the most").click()
        show("If each rule were used on its own", 60)
        shot("09_filter_log")
        browser.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
