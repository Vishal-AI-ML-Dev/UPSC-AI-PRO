"""
Planner routes - Study plan generation and the caller's saved (latest) plan
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.agents.planner.graph import generate_plan, get_plan_meta
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.core import evaluation_history, study_plans
from src.core.db import get_db
from src.graph.memory import get_store, load_student_profile
from src.schemas import (
    SHORT_TEXT_MAX,
    PlannerRequest,
    PlannerResponse,
    StudyPlanEnvelope,
    StudyPlanProgressRequest,
    StudyPlanProgressResponse,
    StudyPlanSaveRequest,
    StudyPlanSaveResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planner", tags=["Planner"])

_UNSPECIFIED = "not specified"


def _resolve_weak(weak: str | None, user_id: str) -> str | None:
    """Return the weak areas to plan for.

    An explicit value always wins and reads nothing. A blank value or "Not
    specified" is filled, for the authenticated caller only, from:
    1. their weak subjects (Answer Evaluator analytics, weakest first), then
    2. their Mentor profile ``weak_areas``,
    de-duplicated case-insensitively and bounded like the request field
    (Phase 08 Slice C2, R-C2-8 / Amendment C2-1). Each source fails open on its
    own; if neither yields anything the original value is returned and the
    planner falls back to "Not specified".
    """
    if weak and weak.strip() and weak.strip().lower() != _UNSPECIFIED:
        return weak
    seen: set[str] = set()
    cleaned: list[str] = []
    for area in _weak_subjects(user_id) + _profile_weak_areas(user_id):
        key = area.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(area)
    if not cleaned:
        return weak
    return ", ".join(cleaned)[:SHORT_TEXT_MAX]


def _weak_subjects(user_id: str) -> list[str]:
    """The caller's weak subjects; fail-open to [] (exception type logged only)."""
    try:
        return evaluation_history.weak_subject_names(user_id)
    except Exception as exc:
        logger.warning("Planner weak-subject autofill unavailable (%s)", type(exc).__name__)
        return []


def _profile_weak_areas(user_id: str) -> list[str]:
    """The caller's Mentor profile ``weak_areas``; fail-open to []."""
    try:
        profile = load_student_profile(get_store(), user_id)
        areas = profile.get("weak_areas") if isinstance(profile, dict) else None
    except Exception as exc:
        logger.warning("Planner weak-area autofill unavailable (%s)", type(exc).__name__)
        return []
    if not isinstance(areas, list):
        return []
    return [str(a).strip() for a in areas if str(a).strip()]


@router.post("/generate")
async def generate(request: PlannerRequest, user: dict = Depends(get_current_user)):
    """Generate study plan (streaming)."""

    def gen():
        # Runs in the stream guard's worker thread, so the store read never
        # blocks the event loop.
        weak = _resolve_weak(request.weak, user["id"])
        for chunk in generate_plan(
            goal=request.goal,
            hours=request.hours,
            optional=request.optional,
            weak=weak,
            attempt_number=request.attempt_number,
        ):
            yield chunk

    return StreamingResponse(guard_stream(gen(), user), media_type="text/plain")


@router.post("/generate/sync", response_model=PlannerResponse)
def generate_sync(request: PlannerRequest, user: dict = Depends(get_current_user)):
    """Generate study plan (non-streaming)."""
    response = ""
    for chunk in generate_plan(
        goal=request.goal,
        hours=request.hours,
        optional=request.optional,
        weak=_resolve_weak(request.weak, user["id"]),
        attempt_number=request.attempt_number,
    ):
        response += chunk
    return {"plan": response, "meta": get_plan_meta(request.goal).model_dump()}


# ---------------------------------------------------------------------------
# Saved study plan (Phase 08 Slice B, ADR-017). One plan per user, addressed
# only by the authenticated user id; no path or body carries a user id.
# ---------------------------------------------------------------------------
PLAN_SAVE_UNAVAILABLE = "Could not save your study plan right now. Please try again."
PLAN_NOT_FOUND = "No saved study plan."
PLAN_REPLACED = "This study plan was replaced by a newer one. Reload to see it."


def _plan_meta(goal: str) -> dict | None:
    """Deterministic plan metadata; optional, so any failure stores no meta."""
    try:
        return get_plan_meta(goal).model_dump()
    except Exception as exc:
        logger.warning("Study plan meta unavailable (%s)", type(exc).__name__)
        return None


@router.get("/plan", response_model=StudyPlanEnvelope)
def get_saved_plan(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """The caller's saved plan, or ``{"plan": null}``."""
    return {"plan": study_plans.get_plan(db, user["id"])}


@router.put("/plan", response_model=StudyPlanSaveResponse)
def save_plan(
    request: StudyPlanSaveRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save the caller's plan, replacing any previous one (new id, progress reset)."""
    meta = _plan_meta(request.goal)
    try:
        return study_plans.upsert_plan(
            db,
            user["id"],
            goal=request.goal,
            inputs=request.inputs.model_dump(),
            plan_md=request.plan_md,
            meta=meta,
        )
    except study_plans.PlanStoreUnavailable:
        raise HTTPException(status_code=503, detail=PLAN_SAVE_UNAVAILABLE)


@router.patch("/plan/progress", response_model=StudyPlanProgressResponse)
def save_plan_progress(
    request: StudyPlanProgressRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace the checklist progress of the caller's current plan."""
    try:
        return study_plans.update_progress(db, user["id"], request.plan_id, request.progress)
    except study_plans.PlanNotFound:
        raise HTTPException(status_code=404, detail=PLAN_NOT_FOUND)
    except study_plans.PlanReplaced:
        raise HTTPException(status_code=409, detail=PLAN_REPLACED)


@router.delete("/plan", status_code=204)
def delete_saved_plan(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete the caller's saved plan; idempotent."""
    study_plans.delete_plan(db, user["id"])
    return Response(status_code=204)
