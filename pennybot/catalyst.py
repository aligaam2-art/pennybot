"""Phase 2a: catalyst check - why is each stock moving?

Two free sources:
  * Yahoo Finance RSS headlines (company news)
  * SEC EDGAR filings (dilution red flags: share sales, delisting notices, reverse splits)

check_catalysts() returns one CatalystInfo per ticker. It does not change the
screener; the selection step and the dashboard read these results.
"""
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ElementTree
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .config import ROOT

CACHE_DIR = ROOT / "cache"
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=US&lang=en-US"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{:010d}.json"

OFFERING_FORMS = ("S-1", "S-3", "F-1", "F-3", "424B", "EFFECT")
PROXY_FORMS = ("PRE 14A", "DEF 14A", "PRE 14C", "DEF 14C")

TAG_ICON = {"strong": "🟢", "weak": "🟡", "danger": "🔴", "ignore": "⚪", "none": "⚪"}


@dataclass
class Headline:
    title: str
    link: str
    published: datetime          # UTC
    tag: str                     # strong / weak / danger / ignore / none

    @property
    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.published).total_seconds() / 3600


@dataclass
class Filing:
    form: str
    filed: date
    items: str
    url: str
    flag: str | None             # offering_filing / unregistered_sale / delisting_notice / reverse_split_signal


@dataclass
class CatalystInfo:
    ticker: str
    headlines: list[Headline] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    sec_page: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def flags(self) -> set[str]:
        return {f.flag for f in self.filings if f.flag}

    def best_headline(self, fresh_hours: float, recent_hours: float) -> Headline | None:
        """The most important real headline: recent danger first, then fresh strong,
        fresh weak, recent strong, then the newest of anything else."""
        real = [h for h in self.headlines if h.tag != "ignore"]
        for tag, hours in (("danger", recent_hours), ("strong", fresh_hours),
                           ("weak", fresh_hours), ("strong", recent_hours)):
            hits = [h for h in real if h.tag == tag and h.age_hours <= hours]
            if hits:
                return hits[0]
        return real[0] if real else None


# ---------------------------------------------------------------- headline tagging

def _compile(keywords: dict[str, list[str]]) -> dict[str, re.Pattern]:
    return {tag: re.compile(r"\b(" + "|".join(re.escape(str(w).strip().lower()) for w in words) + r")\b")
            for tag, words in keywords.items()}


def tag_headline(title: str, patterns: dict[str, re.Pattern]) -> str:
    text = title.lower()
    for tag in ("ignore", "danger", "strong", "weak"):
        if tag in patterns and patterns[tag].search(text):
            return tag
    return "none"


# ---------------------------------------------------------------- sources

def _get(url: str, user_agent: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def fetch_headlines(ticker: str, patterns: dict[str, re.Pattern]) -> list[Headline]:
    root = ElementTree.fromstring(_get(YAHOO_RSS.format(ticker), "Mozilla/5.0"))
    out = []
    for item in root.findall(".//item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        title = (item.findtext("title") or "").strip()
        out.append(Headline(title, item.findtext("link") or "", published, tag_headline(title, patterns)))
    return sorted(out, key=lambda h: h.published, reverse=True)


def sec_cik_map(user_agent: str) -> dict[str, int]:
    """Ticker -> SEC company number, cached for the day."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"sec_tickers_{date.today().isoformat()}.json"
    if not path.exists():
        data = json.loads(_get(SEC_TICKERS, user_agent))
        path.write_text(json.dumps({v["ticker"]: v["cik_str"] for v in data.values()}))
    return json.loads(path.read_text())


def _filing_flag(form: str, items: str) -> str | None:
    if form.startswith(OFFERING_FORMS):
        return "offering_filing"
    if form.startswith("8-K") or form.startswith("6-K"):
        if "3.02" in items:
            return "unregistered_sale"
        if "3.01" in items:
            return "delisting_notice"
    if form in PROXY_FORMS:
        return "reverse_split_signal"
    return None


def fetch_filings(cik: int, lookback_days: int, user_agent: str) -> list[Filing]:
    recent = json.loads(_get(SEC_SUBMISSIONS.format(cik), user_agent))["filings"]["recent"]
    since = date.today() - timedelta(days=lookback_days)
    out = []
    for form, filed, items, acc, doc in zip(recent["form"], recent["filingDate"], recent["items"],
                                            recent["accessionNumber"], recent["primaryDocument"]):
        filed_d = date.fromisoformat(filed)
        if filed_d < since:
            break  # newest first
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        out.append(Filing(form, filed_d, items or "", url, _filing_flag(form, items or "")))
    return out


# ---------------------------------------------------------------- main entry

def check_catalysts(tickers: list[str], cfg: dict) -> dict[str, CatalystInfo]:
    c = cfg["catalyst"]
    ua = c.get("sec_user_agent", "pennybot personal research tool")
    patterns = _compile(c["keywords"])
    try:
        ciks = sec_cik_map(ua)
    except Exception as e:
        ciks, cik_error = {}, f"SEC company list unavailable: {e}"
    else:
        cik_error = None

    def one(t: str) -> CatalystInfo:
        info = CatalystInfo(t)
        try:
            info.headlines = fetch_headlines(t, patterns)
        except Exception as e:
            info.errors.append(f"news: {e}")
        cik = ciks.get(t)
        if cik:
            info.sec_page = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                             f"&CIK={cik}&owner=include&count=40")
            try:
                info.filings = fetch_filings(cik, c.get("filing_lookback_days", 90), ua)
            except Exception as e:
                info.errors.append(f"SEC: {e}")
        else:
            info.errors.append(cik_error or "not found in SEC database (filings not checked)")
        return info

    # SEC allows max 10 requests/second; 4 workers stays well under that.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {i.ticker: i for i in pool.map(one, tickers)}
