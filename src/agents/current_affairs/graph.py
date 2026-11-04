"""
Current Affairs Agent - Daily CA, Editorials, Monthly Digest
"""

import logging
from datetime import date, datetime, timedelta, timezone

from src.agents.current_affairs.prompts import DAILY_CA_PROMPT, EDITORIAL_PROMPT, MONTHLY_PROMPT
from src.core.llm import get_llm, message_text
from src.core.stream_notices import stream_notice

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# TIMEZONE HELPER
# ─────────────────────────────────────────

try:
    import pytz

    IST = pytz.timezone("Asia/Kolkata")

    def _today_ist() -> date:
        return datetime.now(IST).date()
except ImportError:

    def _today_ist() -> date:
        return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


DATE_FORMAT = "%d %B %Y"  # same format as get_available_dates()


def today_label() -> str:
    """Today's date (IST) in the daily-CA date format."""
    return _today_ist().strftime(DATE_FORMAT)


def _is_today(selected_date: str) -> bool:
    try:
        return datetime.strptime(selected_date.strip(), DATE_FORMAT).date() == _today_ist()
    except ValueError:
        return False


# ─────────────────────────────────────────
# EDITORIAL TOPICS
# ─────────────────────────────────────────

EDITORIAL_TOPICS = [
    "Electoral Reforms in India",
    "Judicial Reforms and Pendency Crisis",
    "Urban Governance — Making Cities Work",
    "Rethinking India's Fiscal Federalism",
    "Right to Information — Promises and Gaps",
    "India's Semiconductor Mission",
    "India's Trade Diversification Push",
    "Rebuilding India's Agriculture",
    "Infrastructure and Logistics — The Missing Link",
    "Transforming India's E-Commerce Sector",
    "India–EU FTA and Strategic Realignment",
    "MSMEs — The Backbone Under Stress",
    "Atmanirbharta and Alignment — India's Strategic Autonomy",
    "Recalibrating India's Act East Policy",
    "The India–UAE Strategic Arc",
    "India's Neighbourhood First Policy",
    "India at the UN — Reforms and Relevance",
    "Climate Finance and India's Commitments",
    "Saving India's Wetlands",
    "India's Transition Towards Natural Farming",
    "Biodiversity Loss — India's Response",
    "Water Security — The Coming Crisis",
    "Building India's Deep-Tech Stack",
    "India's Space Economy — The Next Frontier",
    "Strengthening India's Cyber Security",
    "AI Governance — India's Framework",
    "India's Nutritional Security Push",
    "Transforming India's Healthcare Landscape",
    "Reimagining Higher Education in India",
    "Redesigning India for Inclusion of PwDs",
    "Women in Workforce — Closing the Gap",
    "India's Defence Modernisation Drive",
    "Internal Security — Left Wing Extremism",
    "Border Management — Challenges and Solutions",
    "Transforming Indian Railways",
    "Tourism — India's New Economic Frontier",
]


# ─────────────────────────────────────────
# STREAMING FUNCTIONS
# ─────────────────────────────────────────


def get_daily_ca(selected_date: str):
    """Generate today's current affairs (streaming), grounded ONLY in real RSS headlines.

    The feeds return only the latest headlines (no archive), so another date is
    not generated (today's news must not be labelled as that date), and with no
    headlines nothing is generated (no model-made "news"). Blank date = today.
    """
    selected_date = (selected_date or "").strip() or today_label()
    if not _is_today(selected_date):
        yield (
            "> ⚠️ **Current affairs for "
            + selected_date
            + " are not available.**"
            + chr(10)
            + chr(10)
            + "The live news feeds only provide today's headlines ("
            + today_label()
            + "), so they can't be shown as news for another date. Select today's date, "
            + "or revise that day from PIB / the newspaper."
            + chr(10)
        )
        return

    news_context = ""
    try:
        from src.agents.current_affairs.ingest import fetch_headlines

        headlines = fetch_headlines(limit=25)
        if headlines:
            # RSS titles/descriptions are UNTRUSTED external text - fence them
            # before they enter the daily-CA prompt.
            from src.core.prompt_safety import harden_untrusted

            headlines = harden_untrusted(headlines, label="news headlines")
            news_context = (
                "REAL HEADLINES + SHORT SNIPPETS (this is the ENTIRE source text available — there are no full articles; ground every topic strictly in these and invent nothing beyond them):"
                + chr(10)
                + headlines
            )
    except Exception as e:
        logger.warning(f"CA headline grounding unavailable: {e}")

    if not news_context:
        yield (
            "> ⚠️ Live news feed unavailable right now, so no current affairs were generated "
            "- AI-written items could be mistaken for real news. Please try again later, or "
            "use PIB / today's newspaper." + chr(10)
        )
        return

    yield (
        "> 📡 Grounded in today's news feeds (PIB, The Hindu, Indian Express, Down To Earth). Cross-verify before the exam."
        + chr(10)
        + chr(10)
    )

    try:
        chain = DAILY_CA_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream({"date": selected_date, "news_context": news_context}):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                produced = True
                yield text
        if not produced:
            raise ValueError("model returned no text")
    except Exception as e:
        logger.error(f"Daily CA failed: {e}")
        yield stream_notice("Could not generate current affairs. Please retry.")


def get_editorial(topic: str):
    """Generate editorial analysis (streaming)."""
    yield (
        "> ⚠️ AI-generated analytical editorial - the framework and arguments are the real value. Cross-check every specific fact, figure, date, and scheme detail before using it in the exam."
        + chr(10)
        + chr(10)
    )
    try:
        chain = EDITORIAL_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream({"topic": topic}):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                produced = True
                yield text
        if not produced:
            raise ValueError("model returned no text")
    except Exception as e:
        logger.error(f"Editorial failed: {e}")
        yield stream_notice("Could not generate editorial. Please retry.")


def get_monthly_summary(month: str, year: str):
    """Generate monthly digest (streaming), GROUNDED in REAL month-specific news
    from free sources (Google News RSS + Tavily + DuckDuckGo). If no reliable
    news is found, show an honest message instead of hallucinating a digest."""
    news_context = ""
    count = 0
    try:
        from src.agents.current_affairs.monthly_ingest import fetch_month_news

        news_context, count = fetch_month_news(month, year)
    except Exception as e:
        logger.warning(f"Monthly news grounding unavailable: {e}")

    if count == 0:
        yield (
            "> ⚠️ **Verified news archive not available for "
            + str(month)
            + " "
            + str(year)
            + ".**"
            + chr(10)
            + chr(10)
            + "Free news sources returned nothing reliable for this month (common for older months, or when the network/feeds are temporarily unavailable). "
            + "To avoid giving you fabricated current affairs, no digest was generated."
            + chr(10)
            + chr(10)
            + "**What you can do:** pick a more recent month, or revise this month from a monthly magazine / PIB / The Hindu."
            + chr(10)
        )
        return

    yield (
        "> 📡 Grounded in real news retrieved from free sources (Google News, Tavily, DuckDuckGo) for "
        + str(month)
        + " "
        + str(year)
        + " - "
        + str(count)
        + " items organised below. Still cross-verify figures against PIB / The Hindu before the exam."
        + chr(10)
        + chr(10)
    )

    # Retrieved month news is UNTRUSTED external text - fence it before the prompt.
    if news_context:
        from src.core.prompt_safety import harden_untrusted

        news_context = harden_untrusted(news_context, label="monthly news")

    try:
        chain = MONTHLY_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream({"month": month, "year": year, "news_context": news_context}):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                produced = True
                yield text
        if not produced:
            raise ValueError("model returned no text")
    except Exception as e:
        logger.error(f"Monthly summary failed: {e}")
        yield stream_notice("Could not generate monthly digest. Please retry.")


# ─────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────


def get_editorial_topics() -> list:
    """Get available editorial topics."""
    return EDITORIAL_TOPICS


def get_available_dates() -> list:
    """Get available dates for daily CA (last 30 days, excluding Sundays)."""
    dates = []
    current = _today_ist()
    count = 0
    while count < 30:
        if current.weekday() != 6:  # Skip Sunday
            dates.append(current.strftime("%d %B %Y"))
            count += 1
        current -= timedelta(days=1)
    return dates


def get_available_months() -> list:
    """Get available months for monthly digest (last 12 months)."""
    months = []
    today = _today_ist()
    for i in range(12):
        month_num = today.month - i
        year_num = today.year
        while month_num <= 0:
            month_num += 12
            year_num -= 1
        d = date(year_num, month_num, 1)
        months.append((d.strftime("%B"), d.strftime("%Y")))
    return months
