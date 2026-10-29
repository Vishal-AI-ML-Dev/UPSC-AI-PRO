"""
Current Affairs ingestion - fetch REAL headlines from trusted RSS feeds.

Stdlib-only (urllib + xml.etree) so it adds no new dependencies.
Always degrades gracefully: returns an empty string on any failure, so the
caller can fall back to a clearly-flagged indicative digest.
"""

import logging
import ssl
import urllib.request
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Trusted, exam-relevant Indian news/government feeds.
FEEDS = [
    ("PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"),
    ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Indian Express", "https://indianexpress.com/section/india/feed/"),
    ("Down To Earth", "https://www.downtoearth.org.in/rss/news"),
]

_UA = "Mozilla/5.0 (compatible; UPSC-AI/1.0; +https://example.com)"


def _strip_html(text):
    """Remove tags from an RSS description without external deps."""
    out = []
    skip = False
    for ch in text:
        if ch == "<":
            skip = True
        elif ch == ">":
            skip = False
        elif not skip:
            out.append(ch)
    return "".join(out).strip()


def _fetch_one(name, url, per_feed, timeout):
    items = []
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None or not (title_el.text or "").strip():
                continue
            title = " ".join(title_el.text.split()).strip()
            desc = ""
            desc_el = item.find("description")
            if desc_el is not None and desc_el.text:
                desc = _strip_html(" ".join(desc_el.text.split()))[:240]
            line = "- [" + name + "] " + title
            if desc:
                line = line + " - " + desc
            items.append(line)
            if len(items) >= per_feed:
                break
    except Exception as e:
        logger.warning("RSS fetch failed for " + name + ": " + str(e))
    return items


def fetch_headlines(limit=25, per_feed=8, timeout=8):
    """Return real headlines as a newline-joined string, or '' if unavailable."""
    all_items = []
    for name, url in FEEDS:
        all_items.extend(_fetch_one(name, url, per_feed, timeout))
        if len(all_items) >= limit:
            break
    all_items = all_items[:limit]
    return chr(10).join(all_items)


if __name__ == "__main__":
    print(fetch_headlines())
