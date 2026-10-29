"""In-process HTTP metrics middleware (admin-only Monitoring dashboard, #18).

Records per-request latency, status class, and per-endpoint counters entirely
in memory (no extra infra, Rs.0 on the free tier). Counters reset when the
backend restarts. Health-check pings are excluded so keep-alive traffic does
not skew the numbers.

The monitoring route reads `METRICS.snapshot()`.
"""

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_START = time.time()
_MAX_SAMPLES = 5000
_MAX_HOURLY = 48


def _pct(sorted_vals, q):
    """Linear-interpolated percentile of a pre-sorted list (empty -> 0)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


class _Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0
        self.errors = 0
        self.status_classes = defaultdict(int)
        self.latencies = deque(maxlen=_MAX_SAMPLES)
        self.endpoints = {}
        self.hourly = {}

    def record(self, method, path, status_code, dur_ms):
        cls = f"{status_code // 100}xx"
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        is_err = status_code >= 500
        with self.lock:
            self.total += 1
            self.status_classes[cls] += 1
            self.latencies.append(dur_ms)
            if is_err:
                self.errors += 1
            key = (method, path)
            ep = self.endpoints.get(key)
            if ep is None:
                ep = {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
                self.endpoints[key] = ep
            ep["count"] += 1
            ep["total_ms"] += dur_ms
            if dur_ms > ep["max_ms"]:
                ep["max_ms"] = dur_ms
            if is_err:
                ep["errors"] += 1
            hb = self.hourly.get(hour)
            if hb is None:
                hb = {"count": 0, "errors": 0}
                self.hourly[hour] = hb
            hb["count"] += 1
            if is_err:
                hb["errors"] += 1
            if len(self.hourly) > _MAX_HOURLY:
                for k in sorted(self.hourly.keys())[:-_MAX_HOURLY]:
                    self.hourly.pop(k, None)

    def snapshot(self):
        with self.lock:
            lat = sorted(self.latencies)
            endpoints = [
                {
                    "method": m,
                    "path": p,
                    "count": v["count"],
                    "errors": v["errors"],
                    "error_rate": (v["errors"] / v["count"]) if v["count"] else 0.0,
                    "avg_ms": (v["total_ms"] / v["count"]) if v["count"] else 0.0,
                    "max_ms": v["max_ms"],
                }
                for (m, p), v in self.endpoints.items()
            ]
            hourly = [
                {
                    "hour": h,
                    "count": self.hourly[h]["count"],
                    "errors": self.hourly[h]["errors"],
                }
                for h in sorted(self.hourly.keys())[-24:]
            ]
            total = self.total
            errors = self.errors
            status_classes = dict(self.status_classes)
        endpoints.sort(key=lambda e: e["count"], reverse=True)
        endpoints = endpoints[:20]
        uptime = max(1.0, time.time() - _START)
        avg = (sum(lat) / len(lat)) if lat else 0.0
        return {
            "estimated": True,
            "uptime_seconds": int(uptime),
            "total_requests": total,
            "rps": round(total / uptime, 3),
            "error_rate": (errors / total) if total else 0.0,
            "status_classes": status_classes,
            "latency_ms": {
                "count": len(lat),
                "p50": round(_pct(lat, 0.50), 1),
                "p95": round(_pct(lat, 0.95), 1),
                "p99": round(_pct(lat, 0.99), 1),
                "avg": round(avg, 1),
                "max": round(lat[-1], 1) if lat else 0.0,
            },
            "endpoints": endpoints,
            "hourly": hourly,
        }


METRICS = _Store()


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.endswith("/health") or path in ("/", "/health", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            dur_ms = (time.perf_counter() - start) * 1000.0
            route = request.scope.get("route")
            tmpl = getattr(route, "path", None) or path
            METRICS.record(request.method, tmpl, status_code, dur_ms)
