"""Structured, free-first live web search for the Mentor.

Every provider is normalised into one ``SearchResult`` shape (title, URL,
publisher, publication date, snippet, provider, official flag) so citations can
carry real provenance instead of a bare URL mined out of text.

Provider order (free-first; no paid service is required):

1. Tavily            - only when TAVILY_API_KEY is configured (free tier)
2. DDGS news         - keyless; carries publication dates and publisher
3. DDGS text         - keyless; general web pages (no dates)
4. Google News RSS   - keyless; dated news items via a search feed
5. PIB RSS           - keyless; latest official PIB releases, keyword-matched

The chain accumulates de-duplicated results and stops once it has
``MIN_RESULTS`` usable ones. One overall time budget bounds the whole chain,
and every provider call runs under a per-call timeout, so a hung provider can
never stall an answer. ``WEB_SEARCH_ENABLED=false`` contacts nothing.

Only the standard library is imported at module load; provider packages and
settings are imported lazily inside the functions that need them.
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
TOTAL_BUDGET_SECONDS = 15.0  # whole provider chain
PROVIDER_TIMEOUT_SECONDS = 8.0  # one provider call
MAX_RESULTS = 5  # results handed to the model
MIN_RESULTS = 2  # stop the chain once this many usable results exist
_MAX_FEED_BYTES = 2 * 1024 * 1024
TITLE_MAX = 200
SNIPPET_MAX = 300

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
PIB_RSS = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

# --------------------------------------------------------------------------- #
# Domain knowledge
# --------------------------------------------------------------------------- #
# Government / intergovernmental domains. A host is official only when it IS
# one of these or a subdomain of one ("pib.gov.in.evil.com" is not).
OFFICIAL_DOMAINS = (
    "gov.in",
    "nic.in",
    "mygov.in",
    "rbi.org.in",
    "un.org",
    "who.int",
    "worldbank.org",
    "imf.org",
    "wto.org",
    "unesco.org",
    "undp.org",
    "unep.org",
    "ipcc.ch",
)

# Display names for well-known publishers (longest matching suffix wins).
PUBLISHERS = {
    "pib.gov.in": "Press Information Bureau",
    "upsc.gov.in": "Union Public Service Commission",
    "sci.gov.in": "Supreme Court of India",
    "eci.gov.in": "Election Commission of India",
    "isro.gov.in": "ISRO",
    "niti.gov.in": "NITI Aayog",
    "mea.gov.in": "Ministry of External Affairs",
    "india.gov.in": "National Portal of India",
    "indiacode.nic.in": "India Code",
    "newsonair.gov.in": "All India Radio",
    "mygov.in": "MyGov",
    "rbi.org.in": "Reserve Bank of India",
    "sebi.gov.in": "SEBI",
    "un.org": "United Nations",
    "who.int": "World Health Organization",
    "worldbank.org": "World Bank",
    "imf.org": "International Monetary Fund",
    "thehindu.com": "The Hindu",
    "indianexpress.com": "The Indian Express",
    "livemint.com": "Mint",
    "economictimes.indiatimes.com": "The Economic Times",
    "timesofindia.indiatimes.com": "The Times of India",
    "business-standard.com": "Business Standard",
    "hindustantimes.com": "Hindustan Times",
    "downtoearth.org.in": "Down To Earth",
    "prsindia.org": "PRS Legislative Research",
    "drishtiias.com": "Drishti IAS",
    "reuters.com": "Reuters",
    "bbc.com": "BBC",
    "ndtv.com": "NDTV",
}

# Query-string parameters that only track clicks; dropped from cited URLs.
_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid"}


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    publisher: str
    published: str  # ISO date "YYYY-MM-DD", or "" when unknown (never guessed)
    snippet: str
    provider: str
    official: bool
    # Publisher's own site when ``url`` is an aggregator link (Google News);
    # "" when ``url`` already is the publisher's page.
    source_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "SearchResult | None":
        """Rebuild (and re-validate) a result; None for anything malformed.

        ``publisher`` falls back to, and ``official`` is always recomputed
        from, the publisher's site - never trusted from the payload.
        """
        if not isinstance(data, dict):
            return None
        url = clean_url(str(data.get("url") or ""))
        if not url:
            return None
        source_url = clean_url(str(data.get("source_url") or ""))
        origin = source_url or url
        return cls(
            title=clean_text(data.get("title"), TITLE_MAX) or publisher_for(origin),
            url=url,
            publisher=clean_text(data.get("publisher"), 80) or publisher_for(origin),
            published=parse_date(data.get("published")),
            snippet=clean_text(data.get("snippet"), SNIPPET_MAX),
            provider=clean_text(data.get("provider"), 40),
            official=is_official(origin),
            source_url=source_url,
        )


def make_result(
    *,
    title,
    url,
    snippet="",
    provider: str,
    published=None,
    publisher=None,
    source_url=None,
) -> SearchResult | None:
    """Normalise one provider hit; None when it has no usable http(s) URL.

    ``source_url`` is the publisher's own site when ``url`` is an aggregator
    link (Google News); it decides the publisher name and the official flag.
    """
    link = clean_url(str(url or ""))
    if not link:
        return None
    site = clean_url(str(source_url or ""))
    origin = site or link
    return SearchResult(
        title=clean_text(title, TITLE_MAX) or publisher_for(origin),
        url=link,
        publisher=clean_text(publisher, 80) or publisher_for(origin),
        published=parse_date(published),
        snippet=clean_text(snippet, SNIPPET_MAX),
        provider=provider,
        official=is_official(origin),
        source_url=site,
    )


# --------------------------------------------------------------------------- #
# Normalisation helpers (pure)
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_SOURCE_MARKER_RE = re.compile(r"\(\s*source\s*:", re.IGNORECASE)


def clean_text(value, limit: int) -> str:
    """Untrusted provider text -> one safe line: no tags, fences or fake markers."""
    from src.core.prompt_safety import sanitize_untrusted

    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    text = sanitize_untrusted(text)
    # A "(Source: ...)" marker inside a title/snippet must not look like a
    # citation to the legacy URL extractor.
    text = _SOURCE_MARKER_RE.sub("(source -", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _bare_host(url: str) -> str:
    host = _host(url)
    return host[4:] if host.startswith("www.") else host


def clean_url(url: str) -> str:
    """Validated http(s) URL with tracking params / fragment removed, or "".

    Characters that would break a Markdown link destination or the legacy
    "(Source: url)" marker (spaces, quotes, angle brackets, parentheses) are
    percent-encoded.
    """
    url = (url or "").strip()
    if not url or any(ord(ch) < 32 for ch in url):
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    if not parts.netloc.isascii():
        return ""  # never percent-encode a hostname into a broken link
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    # Re-encode the query only when something was removed; otherwise keep it
    # byte-for-byte so the link still resolves exactly as the provider gave it.
    query = parts.query if len(kept) == len(pairs) else urllib.parse.urlencode(kept)
    netloc = parts.netloc.lower()
    cleaned = urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))
    return urllib.parse.quote(cleaned, safe=":/?&=%#+,;@!$*~-._'[]")


def url_key(url: str) -> str:
    """Duplicate-detection key: host without www, path without trailing '/'."""
    cleaned = clean_url(url)
    if not cleaned:
        return ""
    parts = urllib.parse.urlsplit(cleaned)
    path = parts.path.rstrip("/")
    key = _bare_host(cleaned) + path
    return key + ("?" + parts.query if parts.query else "")


def _domain_match(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_official(url: str) -> bool:
    host = _host(url)
    return bool(host) and any(_domain_match(host, d) for d in OFFICIAL_DOMAINS)


def publisher_for(url: str) -> str:
    host = _bare_host(url)
    best = ""
    for domain in PUBLISHERS:
        if _domain_match(host, domain) and len(domain) > len(best):
            best = domain
    return PUBLISHERS[best] if best else host


def _today_ist() -> date:
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


def parse_date(value) -> str:
    """ISO "YYYY-MM-DD" from ISO / RFC-2822 input, or "" - never a guess.

    Unparseable values, dates before 1950 and dates more than a day in the
    future (clock skew aside, those are bogus) all yield "".
    """
    if value is None or value == "":
        return ""
    parsed = None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime

                parsed = parsedate_to_datetime(text).date()
            except (TypeError, ValueError, IndexError):
                return ""
    if parsed is None or parsed.year < 1950 or parsed > _today_ist() + timedelta(days=1):
        return ""
    return parsed.isoformat()


def display_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "date not available"


def dedupe(results) -> list:
    """Drop repeated URLs (by ``url_key``), keeping the first occurrence."""
    seen, out = set(), []
    for r in results or []:
        if r is None:
            continue
        key = url_key(r.url)
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def rank(results) -> list:
    """Official first, then dated before undated; otherwise provider order."""
    indexed = list(enumerate(results or []))
    indexed.sort(key=lambda p: (not p[1].official, not p[1].published, p[0]))
    return [r for _, r in indexed]


# --------------------------------------------------------------------------- #
# Provider adapters (each returns list[SearchResult]; raise on provider error)
# --------------------------------------------------------------------------- #
def _tavily(query: str, timeout: float) -> list:
    from src.agents.mentor.graph import _get_search_tool

    tool = _get_search_tool()  # None when no TAVILY_API_KEY
    if not tool:
        return []
    raw = tool.invoke(query)
    rows = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
    return [
        make_result(
            title=r.get("title"),
            url=r.get("url"),
            snippet=r.get("content"),
            published=r.get("published_date"),
            provider="tavily",
        )
        for r in rows
        if isinstance(r, dict)
    ]


def _ddgs(kind: str, query: str, timeout: float) -> list:
    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("ddgs not installed; DuckDuckGo search disabled. Run: uv add ddgs")
        return []
    with DDGS(timeout=max(1, int(timeout))) as client:
        search = client.news if kind == "news" else client.text
        rows = search(query, region="in-en", max_results=MAX_RESULTS) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if kind == "news":
            out.append(
                make_result(
                    title=r.get("title"),
                    url=r.get("url"),
                    snippet=r.get("body"),
                    published=r.get("date"),
                    publisher=r.get("source"),
                    provider="ddgs_news",
                )
            )
        else:
            out.append(
                make_result(
                    title=r.get("title"),
                    url=r.get("href"),
                    snippet=r.get("body"),
                    provider="ddgs_text",
                )
            )
    return out


def _ddgs_news(query: str, timeout: float) -> list:
    return _ddgs("news", query, timeout)


def _ddgs_text(query: str, timeout: float) -> list:
    return _ddgs("text", query, timeout)


def _http_get(url: str, timeout: float) -> bytes:
    import ssl

    headers = dict(_REQUEST_HEADERS)
    parts = urllib.parse.urlsplit(url)
    headers["Referer"] = f"{parts.scheme}://{parts.netloc}/"
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read(_MAX_FEED_BYTES)


def _rss_items(data: bytes):
    """Yield (title, link, pub_date, description, source_name, source_url)."""
    root = ET.fromstring(data)
    for item in root.iter("item"):

        def text(tag, _item=item):
            el = _item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        source_el = item.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        yield (
            text("title"),
            text("link"),
            text("pubDate"),
            text("description"),
            source_name,
            source_url,
        )


def _google_news_rss(query: str, timeout: float) -> list:
    data = _http_get(GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(query)), timeout)
    out = []
    for title, link, pub, desc, source_name, source_url in _rss_items(data):
        # Google News titles end with " - <Publisher>"; keep the headline only.
        if source_name and title.endswith(" - " + source_name):
            title = title[: -len(" - " + source_name)]
        out.append(
            make_result(
                title=title,
                url=link,
                snippet=desc,
                published=pub,
                publisher=source_name,
                source_url=source_url,
                provider="google_news_rss",
            )
        )
        if len(out) >= MAX_RESULTS:
            break
    return out


_STOPWORDS = frozenset(
    "the and for with from that this what when where which about into over under "
    "latest current recent news today india indian upsc govt government scheme".split()
)


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4} - _STOPWORDS


def _pib_rss(query: str, timeout: float) -> list:
    """Latest PIB releases sharing enough keywords with the query.

    The feed is not searchable (it only lists recent releases), so items are
    matched locally; older topics simply yield nothing.
    """
    wanted = _keywords(query)
    if not wanted:
        return []
    need = 1 if len(wanted) <= 2 else 2
    out = []
    for title, link, pub, desc, _name, _src in _rss_items(_http_get(PIB_RSS, timeout)):
        if len(wanted & _keywords(title + " " + desc)) < need:
            continue
        out.append(
            make_result(
                title=title,
                url=link,
                snippet=desc,
                published=pub,
                publisher="Press Information Bureau",
                provider="pib_rss",
            )
        )
        if len(out) >= MAX_RESULTS:
            break
    return out


# Provider functions are looked up by name at call time so tests can patch them.
PROVIDER_ORDER = ("_tavily", "_ddgs_news", "_ddgs_text", "_google_news_rss", "_pib_rss")


# --------------------------------------------------------------------------- #
# Chain
# --------------------------------------------------------------------------- #
def _call_with_timeout(fn, timeout: float):
    """Run ``fn()`` in a worker thread; raise ``TimeoutError`` past ``timeout``.

    The worker is abandoned (not joined) on timeout so a hung provider can
    never block the request path.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _FutureTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=timeout)
    except _FutureTimeout as exc:
        raise TimeoutError(f"provider timed out after {timeout:g}s") from exc
    finally:
        pool.shutdown(wait=False)


def web_search_enabled() -> bool:
    from src.core.config import settings

    return bool(settings.web_search_enabled)


def search_web(query: str, *, max_results: int = MAX_RESULTS) -> list:
    """Free-first provider chain -> ranked, de-duplicated ``SearchResult`` list.

    Returns [] when search is disabled, the query is empty, or every provider
    fails - callers must then say live data is unavailable.
    """
    query = " ".join((query or "").split())
    if not query or not web_search_enabled():
        return []
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    collected: list = []
    for name in PROVIDER_ORDER:
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            logger.warning("web search budget exhausted before provider %s", name)
            break
        provider = globals()[name]
        timeout = min(PROVIDER_TIMEOUT_SECONDS, remaining)
        try:
            rows = _call_with_timeout(lambda p=provider, t=timeout: p(query, t), timeout)
        except Exception as exc:  # noqa: BLE001 - one provider failing is expected
            # Log the provider and error type only; never the student's query.
            logger.warning(
                "web search provider %s failed: %s", name.lstrip("_"), type(exc).__name__
            )
            continue
        collected = dedupe(collected + [r for r in (rows or []) if isinstance(r, SearchResult)])
        if len(collected) >= MIN_RESULTS:
            break
    return rank(collected)[:max_results]


# --------------------------------------------------------------------------- #
# Model-facing digest
# --------------------------------------------------------------------------- #
def format_digest(results, numbers=None) -> str:
    """Numbered, one-result-per-entry digest the model reads.

    Each entry keeps a "(Source: url)" marker so legacy URL extraction (RAG web
    fallback) keeps working.
    """
    results = list(results or [])
    numbers = list(numbers) if numbers is not None else list(range(1, len(results) + 1))
    lines = []
    for n, r in zip(numbers, results):
        kind = "official source" if r.official else "secondary source"
        when = f"published {display_date(r.published)}" if r.published else "date not available"
        lines.append(f"[{n}] {r.title} — {r.publisher} — {kind} — {when} (Source: {r.url})")
        if r.snippet:
            lines.append(f"    {r.snippet}")
    return "\n".join(lines)
