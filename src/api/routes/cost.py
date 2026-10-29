"""Cost dashboard routes (admin-only, #16).

`/cost/access` is auth-only and returns {admin} so the UI can decide whether to
show the tab without catching a 403. `/cost/overview` is admin-guarded and
returns an estimated spend breakdown. Token/agent counters are not wired in this
build, so figures start at zero and the UI shows a friendly "no traffic" note
until real usage tracking is added; the shape is stable and correct.
"""

from fastapi import APIRouter, Depends

from src.api.admin_access import is_admin, require_admin
from src.api.deps import get_current_user

router = APIRouter(prefix="/cost", tags=["Cost"])


@router.get("/access")
async def cost_access(user: dict = Depends(get_current_user)):
    """Auth-only: report whether the caller may see the cost dashboard."""
    return {"admin": is_admin(user.get("email", ""))}


@router.get("/overview")
async def cost_overview(user: dict = Depends(require_admin)):
    """Admin-only: estimated spend, tier mix, and cache effectiveness."""
    from src.core.usage_tracker import snapshot

    return snapshot()
