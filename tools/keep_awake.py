"""Open the hosted dashboard so Streamlit Community Cloud doesn't put it to sleep.

Free Streamlit apps sleep after about 12 hours without visitors. Run by
.github/workflows/keep-awake.yml every few hours; wakes the app if it is asleep.
Usage: python tools/keep_awake.py https://pennybot-ali.streamlit.app
"""
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(url, timeout=120_000)
    page.wait_for_timeout(8000)
    wake = page.get_by_role("button", name="Yes, get this app back up!")
    if wake.count():
        print("app was asleep - waking it up")
        wake.click()
        page.wait_for_timeout(90_000)      # give it time to start
    else:
        print("app is awake")
    page.wait_for_timeout(20_000)          # stay a moment so the visit counts
    print("page title:", page.title())
    browser.close()
