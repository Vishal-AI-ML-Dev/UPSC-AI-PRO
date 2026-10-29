"""Lightweight student-profile extraction and merge helpers.

This module is deterministic and conservative: it extracts only explicit
student-provided signals, so personalization improves without inventing facts.
"""

from __future__ import annotations

import re
from typing import Any

_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_HOURS_RE = re.compile(r"\b(\d{1,2})\s*(?:hours?|hrs?|ghante|घंटे)\b", re.IGNORECASE)
_ATTEMPT_RE = re.compile(
    r"\b(?:attempt|attempts?)\s*(?:number|no\.?|#)?\s*(\d{1,2})\b", re.IGNORECASE
)
_WEAK_RE = re.compile(
    r"\b(?:weak|weakness|weak in|weak area|कमजोर)\s*(?:in|area)?\s*[:\-]?\s*([A-Za-z][A-Za-z\s&/-]{2,60})",
    re.IGNORECASE,
)
_OPTIONAL_RE = re.compile(
    r"\boptional\s*(?:subject)?\s*[:\-]?\s*([A-Za-z][A-Za-z\s&/-]{2,60})", re.IGNORECASE
)
_LANG_RE = re.compile(r"\b(hindi|hinglish|english)\b", re.IGNORECASE)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" .,!?;:\n\t"))[:80]


def _append_unique(existing: list[str], value: str) -> list[str]:
    value = _clean(value)
    if not value:
        return existing
    lowered = {x.lower() for x in existing}
    if value.lower() not in lowered:
        existing.append(value)
    return existing[:12]


def extract_student_profile_signals(text: str) -> dict[str, Any]:
    """Extract explicit UPSC-prep profile signals from a user message."""
    text = text or ""
    profile: dict[str, Any] = {}

    if match := _YEAR_RE.search(text):
        profile["target_year"] = match.group(1)
    if match := _HOURS_RE.search(text):
        profile["study_hours"] = int(match.group(1))
    if match := _ATTEMPT_RE.search(text):
        profile["attempts"] = int(match.group(1))
    if match := _OPTIONAL_RE.search(text):
        profile["optional"] = _clean(match.group(1))
    if match := _LANG_RE.search(text):
        profile["preferred_language"] = match.group(1).lower()
    if match := _WEAK_RE.search(text):
        profile["weak_areas"] = [_clean(match.group(1))]

    return profile


def merge_student_profile(
    existing: dict[str, Any] | None, updates: dict[str, Any]
) -> dict[str, Any]:
    """Merge extracted updates into an existing profile without dropping fields."""
    merged = dict(existing or {})
    for key, value in (updates or {}).items():
        if value in (None, "", []):
            continue
        if key == "weak_areas":
            current = list(merged.get("weak_areas") or [])
            for item in value:
                current = _append_unique(current, str(item))
            merged["weak_areas"] = current
        else:
            merged[key] = value
    return merged
