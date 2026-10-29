"""Operational monitoring dashboard routes (admin-only, #18).

`/monitoring/access` is auth-only ({admin}) so the UI can hide the tab without a
403. `/monitoring/overview` is admin-guarded and returns live in-process request
metrics collected by HttpMetricsMiddleware. If the middleware is not installed,
an empty-but-valid snapshot is returned so the page still renders.
"""

from fastapi import APIRouter, Depends

from src.api.admin_access import is_admin, require_admin
from src.api.deps import get_current_user

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

_EMPTY = {
    "estimated": True,
    "uptime_seconds": 0,
    "total_requests": 0,
    "rps": 0.0,
    "error_rate": 0.0,
    "status_classes": {},
    "latency_ms": {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "max": 0.0},
    "endpoints": [],
    "hourly": [],
}


@router.get("/access")
async def monitoring_access(user: dict = Depends(get_current_user)):
    """Auth-only: report whether the caller may see the monitoring dashboard."""
    return {"admin": is_admin(user.get("email", ""))}


@router.get("/overview")
async def monitoring_overview(user: dict = Depends(require_admin)):
    """Admin-only: live latency / throughput / error-rate snapshot."""
    try:
        from src.api.http_metrics_mw import METRICS

        return METRICS.snapshot()
    except Exception:
        return dict(_EMPTY)
