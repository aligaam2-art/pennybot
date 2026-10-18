"""Phone alerts via ntfy (https://ntfy.sh): free app, no account.

The channel ("topic") name comes from the NTFY_TOPIC environment variable,
set as a GitHub secret, so it never appears in the public repository.
Without it, alerts are silently skipped (e.g. on your laptop).
"""
from __future__ import annotations

import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


def send(cfg: dict, title: str, message: str, tags: str = "", priority: str = "default") -> bool:
    a = cfg.get("alerts", {})
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or not a.get("phone", True):
        return False
    req = urllib.request.Request(
        f"{a.get('ntfy_server', 'https://ntfy.sh').rstrip('/')}/{topic}",
        data=message.encode("utf-8"), method="POST",
        headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": tags, "Priority": priority})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        return True
    except Exception as e:                      # an alert failing must never stop the journal
        print(f"phone alert failed: {e!r}", flush=True)
        return False


def local_hhmm(day: str, hhmm_et: str, cfg: dict) -> str:
    """'10:50' New York time on `day` -> '17:50' in the display time zone."""
    et = datetime.fromisoformat(f"{day} {hhmm_et}").replace(tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(ZoneInfo(cfg["display_timezone"])).strftime("%H:%M")
