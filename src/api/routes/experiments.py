"""Prompt A/B experiments dashboard routes (admin-only, #12).

`/experiments/access` is auth-only ({admin}) so the UI can hide the tab without a
403. `/experiments/overview` is admin-guarded and aggregates the human thumbs
up/down feedback into overall and per-agent win-rates. No A/B prompt experiments
are configured in this build, so `experiments` is an empty list; the real
feedback tallies still render.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.admin_access import is_admin, require_admin
from src.api.deps import get_current_user
from src.core.db import get_db
from src.core.models import Feedback

router = APIRouter(prefix="/experiments", tags=["Experiments"])


def _tally(up: int, down: int) -> dict:
    total = up + down
    return {
        "up": up,
        "down": down,
        "total": total,
        "up_rate": (up / total) if total else 0.0,
    }


@router.get("/access")
async def experiments_access(user: dict = Depends(get_current_user)):
    """Auth-only: report whether the caller may see the experiments dashboard."""
    return {"admin": is_admin(user.get("email", ""))}


@router.get("/overview")
async def experiments_overview(
    user: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: overall + per-agent thumbs up/down feedback tallies."""
    up = 0
    down = 0
    by_agent_raw: dict = {}
    for row in db.query(Feedback.agent, Feedback.rating).all():
        agent = row.agent or "mentor"
        rating = (row.rating or "").strip().lower()
        bucket = by_agent_raw.setdefault(agent, [0, 0])
        if rating == "up":
            up += 1
            bucket[0] += 1
        elif rating == "down":
            down += 1
            bucket[1] += 1
    by_agent = {a: _tally(v[0], v[1]) for a, v in by_agent_raw.items()}
    overall = _tally(up, down)
    return {
        "estimated": True,
        "experiments": [],
        "feedback": {
            "overall": overall,
            "by_agent": by_agent,
            "sample_size": overall["total"],
        },
    }
