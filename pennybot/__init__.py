"""Pennybot: penny stock paper-trading helper.

Step 1 is the screener (pennybot.screener). Later modules (catalyst check,
journal, alerts) read the same ScanResult object and add to it, so they
never need to touch the screener code.
"""
