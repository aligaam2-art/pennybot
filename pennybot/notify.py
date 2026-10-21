"""Alerts: phone push via ntfy (https://ntfy.sh) and email via GitHub.

* ntfy: free app, no account. The channel ("topic") name comes from the
  NTFY_TOPIC environment variable (a GitHub secret), so it never appears in
  the public repository.
* Email: on GitHub Actions each alert is posted as a comment on a daily
  "Pennybot alerts" issue in the repository. GitHub emails every comment to
  the repository owner, so alerts arrive in your inbox with no extra account.

Alerts are silently skipped where neither is available (e.g. on your laptop).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ISSUE_PREFIX = "Pennybot alerts"


def _ntfy(cfg: dict, title: str, message: str, tags: str, priority: str) -> bool:
    a = cfg.get("alerts", {})
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or not a.get("phone", True):
        return False
    req = urllib.request.Request(
        f"{a.get('ntfy_server', 'https://ntfy.sh').rstrip('/')}/{topic}",
        data=message.encode("utf-8"), method="POST",
        headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": tags, "Priority": priority})
    urllib.request.urlopen(req, timeout=15).close()
    return True


def _github(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _email(cfg: dict, title: str, message: str) -> bool:
    """Comment on today's alerts issue; GitHub emails it to the repository owner."""
    if not cfg.get("alerts", {}).get("email", True):
        return False
    if not (os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_REPOSITORY")):
        return False
    today = datetime.now(ZoneInfo("America/New_York"))
    issue_title = f"{ISSUE_PREFIX} – {today:%a %d %b %Y}"
    issues = _github("GET", "/issues?state=open&per_page=50&creator=github-actions%5Bbot%5D")
    number = next((i["number"] for i in issues if i["title"] == issue_title), None)
    if number is None:
        for i in issues:                        # tidy: close earlier days' alert issues
            if i["title"].startswith(ISSUE_PREFIX):
                _github("PATCH", f"/issues/{i['number']}", {"state": "closed"})
        number = _github("POST", "/issues", {
            "title": issue_title,
            "body": "Today's Pennybot paper-trading alerts. Each comment below is one alert "
                    "(you get each by email). Paper trading only."})["number"]
    _github("POST", f"/issues/{number}/comments", {"body": f"**{title}**\n\n{message}"})
    return True


def send(cfg: dict, title: str, message: str, tags: str = "", priority: str = "default") -> bool:
    """Send an alert everywhere available. Never raises: alerts must not stop the journal."""
    sent = False
    for name, fn in (("phone", lambda: _ntfy(cfg, title, message, tags, priority)),
                     ("email", lambda: _email(cfg, title, message))):
        try:
            sent = fn() or sent
        except Exception as e:
            print(f"{name} alert failed: {e!r}", flush=True)
    return sent


def local_hhmm(day: str, hhmm_et: str, cfg: dict) -> str:
    """'10:50' New York time on `day` -> '17:50' in the display time zone."""
    et = datetime.fromisoformat(f"{day} {hhmm_et}").replace(tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(ZoneInfo(cfg["display_timezone"])).strftime("%H:%M")
