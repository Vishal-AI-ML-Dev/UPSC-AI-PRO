"""
Monthly Current Affairs ingestion - fetch REAL, month-specific news from
multiple FREE sources so the monthly digest is GROUNDED, never hallucinated.

Sources (all free, best-effort, graceful degradation):
  1. Google News RSS  - stdlib urllib + xml, date-filtered (after:/before:)
  2. Tavily Search API - only if TAVILY_API_KEY is set (free tier)
  3. DuckDuckGo (ddgs) - only if the package is installed
  4. PIB / The Hindu / Indian Express / Down To Earth RSS - reuses the daily
     feeds, strictly filtered by pubDate to the requested month (so old
     months naturally return nothing instead of leaking current headlines)

Every source is wrapped in try/except and may return nothing. The caller
decides whether enough real material was gathered; if not, it shows an
honest "archive not available" message instead of inventing facts.

Stdlib-only for the core path (urllib + xml.etree) so it adds no hard
dependency. ddgs/Tavily are optional and skipped silently if unavailable.
"""

import calendar
import json
import logging
import ssl
import urllib.parse
import urllib.request
import warnings
from datetime import date
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Silence very noisy third-party DEBUG logs (DuckDuckGo's Rust HTTP engine:
# h2 / rustls / hickory / reqwest etc.) so this ingest does not flood the
# console with TLS-handshake and DNS spam. Purely cosmetic - no behaviour change.
for _noisy in (
    "h2",
    "hpack",
    "hickory_net",
    "hickory_resolver",
    "rustls",
    "reqwest",
    "hyper_util",
    "primp",
    "httpx",
    "httpcore",
    "urllib3",
    "duckduckgo_search",
    "ddgs",
):
    try:
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    except Exception:
        pass

# duckduckgo_search emits a RuntimeWarning about being renamed to `ddgs`;
# silence it (cosmetic). To remove fully: pip install ddgs
warnings.filterwarnings("ignore", category=RuntimeWarning, module="duckduckgo_search")

# Browser-like UA + Accept headers. A bare/identifying UA gets 403 from some
# sites (e.g. PIB), so we present a normal browser fingerprint.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_REQUEST_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

# Category-wise query seeds for an exam-relevant monthly digest.
_CATEGORY_QUERIES = [
    ("National Affairs", "India government policy scheme parliament supreme court"),
    ("International Relations", "India foreign relations summit bilateral agreement diplomacy"),
    ("Economy & Finance", "India economy RBI GST inflation budget trade"),
    ("Environment & Ecology", "India environment climate wildlife pollution conservation"),
    ("Science & Technology", "India ISRO technology AI defence research space"),
    ("Sports, Awards & Honours", "India sports award championship medal honour"),
    ("Reports, Indices & Appointments", "India report index ranking appointment appointed"),
]


def _month_bounds(month, year):
    """Return (start_iso, end_exclusive_iso, month_num, year_int) for a month name + year."""
    name_to_num = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    m = name_to_num.get(str(month).strip().lower())
    if not m:
        # allow numeric month too
        try:
            m = int(str(month).strip())
        except Exception:
            raise ValueError("unrecognised month: " + str(month))
    y = int(str(year).strip())
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start.isoformat(), end.isoformat(), m, y


def _strip_html(text):
    out = []
    skip = False
    for ch in text:
        if ch == "<":
            skip = True
        elif ch == ">":
            skip = False
        elif not skip:
            out.append(ch)
    return " ".join("".join(out).split()).strip()


def _google_news(query, start_iso, end_iso, per_query, timeout):
    """Source 1: Google News RSS - fully free, no key, supports date filtering."""
    items = []
    try:
        q = query + " after:" + start_iso + " before:" + end_iso
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(q)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None or not (title_el.text or "").strip():
                continue
            title = " ".join(title_el.text.split()).strip()
            items.append(title)
            if len(items) >= per_query:
                break
    except Exception as e:
        logger.warning("Google News fetch failed for '" + query + "': " + str(e))
    return items


def _tavily(query, start_iso, end_iso, max_results, timeout):
    """Source 2: Tavily news search - only if TAVILY_API_KEY configured (free tier)."""
    items = []
    try:
        from src.core.config import get_settings

        if not get_settings().web_search_enabled:  # WEB_SEARCH_ENABLED=false
            return items
        key = (get_settings().tavily_api_key or "").strip()
        if not key:
            return items
        payload = {
            "api_key": key,
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max_results,
            "start_date": start_iso,
            "end_date": end_iso,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=body,
            headers={"User-Agent": _UA, "Content-Type": "application/json"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for r in data.get("results") or []:
            title = (r.get("title") or "").strip()
            if not title:
                continue
            content = _strip_html(r.get("content") or "")[:200]
            line = title + (" - " + content if content else "")
            items.append(" ".join(line.split()))
            if len(items) >= max_results:
                break
    except Exception as e:
        logger.warning("Tavily monthly fetch failed: " + str(e))
    return items


def _duckduckgo(query, month, year, max_results, timeout):
    """Source 3: DuckDuckGo news - only if the ddgs/duckduckgo_search package exists."""
    items = []
    try:
        from src.core.config import get_settings

        if not get_settings().web_search_enabled:  # WEB_SEARCH_ENABLED=false
            return items
        try:
            from ddgs import DDGS
        except Exception:
            from duckduckgo_search import DDGS  # older package name
        q = query + " " + str(month) + " " + str(year)
        with DDGS(timeout=timeout) as ddgs:
            for r in ddgs.news(q, region="in-en", max_results=max_results):
                title = (r.get("title") or "").strip()
                if not title:
                    continue
                body = _strip_html(r.get("body") or "")[:200]
                line = title + (" - " + body if body else "")
                items.append(" ".join(line.split()))
    except Exception as e:
        logger.warning("DuckDuckGo monthly fetch failed: " + str(e))
    return items


def _rss_feeds(start_iso, end_iso, per_feed, timeout):
    """Source 4: PIB + The Hindu + Indian Express + Down To Earth RSS.

    Reuses the same trusted feeds as the daily CA ingest. Keeps ONLY items
    whose pubDate falls inside the requested month; items without a parseable
    date are skipped (to avoid leaking wrong-month / current headlines into an
    older month's digest). Older months therefore yield nothing here, which is
    correct - these feeds only carry recent items.
    """
    items = []
    try:
        from email.utils import parsedate_to_datetime

        try:
            from src.agents.current_affairs.ingest import FEEDS
        except Exception:
            FEEDS = [
                ("PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"),
                ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
                ("Indian Express", "https://indianexpress.com/section/india/feed/"),
                ("Down To Earth", "https://www.downtoearth.org.in/rss/news"),
            ]
        start_d = date.fromisoformat(start_iso)
        end_d = date.fromisoformat(end_iso)
        ctx = ssl.create_default_context()
        for name, url in FEEDS:
            try:
                feed_headers = dict(_REQUEST_HEADERS)
                try:
                    parts = urllib.parse.urlsplit(url)
                    feed_headers["Referer"] = parts.scheme + "://" + parts.netloc + "/"
                except Exception:
                    pass
                req = urllib.request.Request(url, headers=feed_headers)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    data = resp.read()
                root = ET.fromstring(data)
                kept = 0
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not (title_el.text or "").strip():
                        continue
                    pub_el = item.find("pubDate")
                    if pub_el is None or not (pub_el.text or "").strip():
                        continue  # no date -> cannot confirm month -> skip
                    try:
                        d = parsedate_to_datetime(pub_el.text.strip()).date()
                    except Exception:
                        continue
                    if d < start_d or d >= end_d:
                        continue
                    title = " ".join(title_el.text.split()).strip()
                    desc = ""
                    desc_el = item.find("description")
                    if desc_el is not None and desc_el.text:
                        desc = _strip_html(desc_el.text)[:200]
                    line = "[" + name + "] " + title + (" - " + desc if desc else "")
                    items.append(" ".join(line.split()))
                    kept += 1
                    if kept >= per_feed:
                        break
            except Exception as e:
                logger.warning("RSS feed failed for " + name + ": " + str(e))
    except Exception as e:
        logger.warning("RSS feeds source failed: " + str(e))
    return items


def fetch_month_news(month, year, per_category=6, timeout=10):
    """Gather REAL, month-specific headlines from up to 3 free sources.

    Returns (context_string, item_count). Returns ("", 0) when nothing
    reliable was found, so the caller can show an honest message instead
    of fabricating a digest.
    """
    try:
        start_iso, end_iso, _m, _y = _month_bounds(month, year)
    except Exception as e:
        logger.warning("month bounds failed: " + str(e))
        return "", 0

    seen = set()
    sections = []
    total = 0

    for label, query in _CATEGORY_QUERIES:
        lines = []

        def _add(text):
            key = text.lower()[:80]
            if key and key not in seen:
                seen.add(key)
                lines.append("- " + text)

        # Source 1: Google News RSS (primary, fully free)
        for t in _google_news(query, start_iso, end_iso, per_category, timeout):
            _add(t)
        # Source 2: Tavily (if API key configured)
        for t in _tavily(
            query + " " + str(month) + " " + str(year), start_iso, end_iso, 4, timeout
        ):
            _add(t)
        # Source 3: DuckDuckGo (if package available)
        for t in _duckduckgo(query, month, year, 4, timeout):
            _add(t)

        if lines:
            sections.append("### " + label + chr(10) + chr(10).join(lines))
            total += len(lines)

    # Source 4: PIB + The Hindu + Indian Express + Down To Earth RSS
    # (date-filtered by pubDate; recent months only).
    rss_lines = []
    for t in _rss_feeds(start_iso, end_iso, 8, timeout):
        key = t.lower()[:80]
        if key and key not in seen:
            seen.add(key)
            rss_lines.append("- " + t)
    if rss_lines:
        sections.append(
            "### Official & Newspaper Feeds (PIB / The Hindu / Indian Express / Down To Earth)"
            + chr(10)
            + chr(10).join(rss_lines)
        )
        total += len(rss_lines)

    if total == 0:
        return "", 0

    header = (
        "REAL NEWS RETRIEVED for "
        + str(month)
        + " "
        + str(year)
        + " (use ONLY these items; do not add anything else):"
    )
    context = header + chr(10) + chr(10) + (chr(10) + chr(10)).join(sections)
    return context, total


if __name__ == "__main__":
    ctx, n = fetch_month_news("June", "2026")
    print("items:", n)
    print(ctx[:2000])
