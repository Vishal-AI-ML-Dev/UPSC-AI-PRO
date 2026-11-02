"""
Planner routes - Study plan generation
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.agents.planner.graph import generate_plan, get_plan_meta
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.schemas import PlannerRequest, PlannerResponse

router = APIRouter(prefix="/planner", tags=["Planner"])


@router.post("/generate")
async def generate(request: PlannerRequest, user: dict = Depends(get_current_user)):
    """Generate study plan (streaming)."""

    def gen():
        for chunk in generate_plan(
            goal=request.goal,
            hours=request.hours,
            optional=request.optional,
            weak=request.weak,
            attempt_number=request.attempt_number,
        ):
            yield chunk

    return StreamingResponse(guard_stream(gen(), user), media_type="text/plain")


@router.post("/generate/sync", response_model=PlannerResponse)
def generate_sync(request: PlannerRequest):
    """Generate study plan (non-streaming)."""
    response = ""
    for chunk in generate_plan(
        goal=request.goal,
        hours=request.hours,
        optional=request.optional,
        weak=request.weak,
        attempt_number=request.attempt_number,
    ):
        response += chunk
    return {"plan": response, "meta": get_plan_meta(request.goal).model_dump()}
