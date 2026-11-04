"""Answer Evaluator attempt recording (Phase 08 Slice C1, ADR-017).

One ``evaluation_attempts`` row per Basic / Mains evaluation: the authenticated
user id, the mode, the optional allow-listed subject, the validated score and
its maximum. **No question or answer text is ever stored here.**

Recording is best-effort: callers wrap ``record_attempt`` in ``try/except`` so a
database failure can never change the evaluator response. It uses its own short
session, so a failure cannot leave the request's session in a bad state.

Slice C2 adds read-side analytics, computed on read from the caller's rows by
pure functions (a fixed ``today`` in tests): Average Evaluation Score, subject
mastery, weak subjects and the subject-based revision schedule. Percentages are
fractions in [0, 1] (``pct = score / max_score``); unscored attempts (NULL
score) are excluded from every percentage. Untagged attempts (no subject) count
only towards the average.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal
from src.core.models import EvaluationAttempt
from src.schemas import SUBJECTS

MODES = ("basic", "mains")

# --- Slice C2 thresholds (spec R-C2-1) -------------------------------------
PASS_PCT = 0.60
WEAK_MIN_ATTEMPTS = 3
MASTERY_WINDOW = 10
REVISION_INTERVALS_DAYS = (1, 3, 7, 14)
# Read bound (plan C2 step 2): analytics use the caller's latest N attempts.
ANALYTICS_ATTEMPT_LIMIT = 500
# Float tolerance so a pct of exactly 0.60 (e.g. 9/15) always counts as 0.60.
_EPS = 1e-9


def valid_max_score(max_score: object) -> int | None:
    """``max_score`` if it is an integer > 0 (bools excluded), else None (no row)."""
    if isinstance(max_score, bool) or not isinstance(max_score, int) or max_score <= 0:
        return None
    return max_score


def validated_score(score: object, max_score: int) -> float | None:
    """``score`` as a float if finite and ``0 <= score <= max_score``, else None (unscored)."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    value = float(score)
    if not math.isfinite(value) or not 0 <= value <= max_score:
        return None
    return value


def record_attempt(
    *,
    user_id: str,
    mode: str,
    subject: str | None,
    score: object,
    max_score: object,
) -> bool:
    """Insert one attempt for ``user_id``; return True if a row was written.

    No row when ``max_score`` is not an integer > 0. An invalid score is stored
    as NULL. ``mode`` and ``subject`` are re-checked against their allow-lists
    (the request schema already enforces the subject). Database errors propagate
    to the caller, which must handle them without affecting its response.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if mode not in MODES:
        raise ValueError("unknown evaluation mode")
    if subject is not None and subject not in SUBJECTS:
        raise ValueError("unknown evaluation subject")
    max_value = valid_max_score(max_score)
    if max_value is None:
        return False
    row = EvaluationAttempt(
        user_id=user_id,
        mode=mode,
        subject=subject,
        score=validated_score(score, max_value),
        max_score=max_value,
    )
    with SessionLocal() as db:
        db.add(row)
        db.commit()
    return True


# ---------------------------------------------------------------------------
# Slice C2 analytics (spec R-C2-1 .. R-C2-5): pure functions over ``Attempt``s.
# ---------------------------------------------------------------------------
class Attempt(NamedTuple):
    """The analytics view of one ``evaluation_attempts`` row (the table holds no text)."""

    mode: str
    subject: str | None
    score: float | None
    max_score: int
    created_at: datetime


def attempt_pct(attempt: Attempt) -> float | None:
    """``score / max_score`` for a scored attempt, else None (unscored or invalid)."""
    max_value = valid_max_score(attempt.max_score)
    if max_value is None or attempt.score is None:
        return None
    score = validated_score(attempt.score, max_value)
    return None if score is None else score / max_value


def passes(pct: float) -> bool:
    """A scored attempt passes when ``pct >= PASS_PCT`` (exactly 0.60 passes)."""
    return pct >= PASS_PCT - _EPS


def _utc(value: datetime) -> datetime:
    """Naive timestamps (SQLite) are UTC; aware ones are converted to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _newest_first(attempts: Iterable[Attempt]) -> list[Attempt]:
    """Stable newest-first order (ties keep the input order)."""
    return sorted(attempts, key=lambda a: _utc(a.created_at), reverse=True)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summary(attempts: Sequence[Attempt]) -> dict:
    pcts = [p for p in (attempt_pct(a) for a in attempts) if p is not None]
    return {
        "attempts": len(attempts),
        "scored": len(pcts),
        "average_score_pct": round(_mean(pcts), 4),
    }


def average_score(attempts: Iterable[Attempt]) -> dict:
    """Average Evaluation Score (R-C2-2): mean pct over every scored attempt.

    Both modes, tagged and untagged; unscored attempts are counted in
    ``attempts`` but not in the mean. ``average_score_pct`` is 0.0 when nothing
    is scored (``scored == 0`` tells the UI there is no score yet).
    """
    items = list(attempts)
    summary = _summary(items)
    summary["by_mode"] = {m: _summary([a for a in items if a.mode == m]) for m in MODES}
    return summary


def _scored_by_subject(attempts: Iterable[Attempt]) -> dict[str, list[float]]:
    """Subject -> scored pcts, newest first. Untagged and unscored attempts are dropped."""
    out: dict[str, list[float]] = {}
    for attempt in _newest_first(attempts):
        pct = attempt_pct(attempt)
        if attempt.subject and pct is not None:
            out.setdefault(attempt.subject, []).append(pct)
    return out


def _mastery_rows(attempts: Iterable[Attempt]) -> list[tuple[float, str, int]]:
    """(unrounded mastery, subject, scored attempts in window), mastery asc then subject."""
    rows = []
    for subject, pcts in _scored_by_subject(attempts).items():
        window = pcts[:MASTERY_WINDOW]
        rows.append((_mean(window), subject, len(window)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def _public(rows: Iterable[tuple[float, str, int]]) -> list[dict]:
    return [{"subject": s, "mastery": round(m, 4), "attempts": n} for m, s, n in rows]


def subject_mastery(attempts: Iterable[Attempt]) -> list[dict]:
    """Subject mastery (R-C2-3): mean pct of a subject's latest ``MASTERY_WINDOW`` scored attempts.

    ``attempts`` is the number of scored attempts in that window (<= 10).
    Sorted by mastery ascending, then subject name (deterministic).
    """
    return _public(_mastery_rows(attempts))


def weak_subjects(attempts: Iterable[Attempt]) -> list[dict]:
    """Weak subjects (R-C2-4): >= 3 scored attempts in the window and mastery < 0.60.

    Sorted by mastery ascending, then subject name.
    """
    return _public(
        r for r in _mastery_rows(attempts) if r[2] >= WEAK_MIN_ATTEMPTS and not passes(r[0])
    )


def revision_interval_days(consecutive_passes: int) -> int:
    """R-C2-5: k = 0 -> 1, 1 -> 1, 2 -> 3, 3 -> 7, >= 4 -> 14 days."""
    k = max(consecutive_passes, 0)
    return 1 if k == 0 else REVISION_INTERVALS_DAYS[min(k, 4) - 1]


def revision_schedule(attempts: Iterable[Attempt], today: date) -> dict:
    """Subject-based revision (R-C2-5) for every subject with >= 1 scored attempt.

    ``k`` = consecutive most-recent scored passes; ``next_due`` = UTC date of the
    latest scored attempt + interval; ``due`` when ``next_due <= today`` (UTC).
    Unscored attempts are ignored. Sorted by ``next_due``, then subject name.
    """
    anchor: dict[str, date] = {}
    streak: dict[str, int] = {}
    broken: set[str] = set()
    for attempt in _newest_first(attempts):
        pct = attempt_pct(attempt)
        subject = attempt.subject
        if not subject or pct is None:
            continue
        if subject not in anchor:
            anchor[subject] = _utc(attempt.created_at).date()
            streak[subject] = 0
        if subject in broken:
            continue
        if passes(pct):
            streak[subject] += 1
        else:
            broken.add(subject)
    rows = sorted(
        (day + timedelta(days=revision_interval_days(streak[subject])), subject)
        for subject, day in anchor.items()
    )
    subjects = [
        {"subject": subject, "next_due": next_due.isoformat(), "due": next_due <= today}
        for next_due, subject in rows
    ]
    return {
        "due": sum(1 for s in subjects if s["due"]),
        "total": len(subjects),
        "subjects": subjects,
    }


def load_attempts(db: Session, user_id: str, limit: int = ANALYTICS_ATTEMPT_LIMIT) -> list[Attempt]:
    """The caller's latest ``limit`` attempts, newest first; always filtered by ``user_id``."""
    if not user_id:
        raise ValueError("user_id is required")
    rows = db.execute(
        select(
            EvaluationAttempt.mode,
            EvaluationAttempt.subject,
            EvaluationAttempt.score,
            EvaluationAttempt.max_score,
            EvaluationAttempt.created_at,
        )
        .where(EvaluationAttempt.user_id == user_id)
        .order_by(EvaluationAttempt.created_at.desc(), EvaluationAttempt.id.desc())
        .limit(limit)
    ).all()
    return [Attempt(*row) for row in rows]


def weak_subject_names(user_id: str) -> list[str]:
    """The caller's weak subjects (weakest first), read in a short session of its own.

    Database errors propagate; the Planner autofill catches them and fails open.
    """
    with SessionLocal() as db:
        return [row["subject"] for row in weak_subjects(load_attempts(db, user_id))]
