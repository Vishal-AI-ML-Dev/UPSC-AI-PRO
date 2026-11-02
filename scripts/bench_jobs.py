"""Job-reliability benchmark for UPSC-AI-PRO (Phase 04, offline, multi-process).

Exercises the durable-job guarantees with REAL separate OS processes sharing one
throwaway SQLite database (what two uvicorn workers would share):

  A. sibling boot   - worker A runs N live jobs longer than several leases while
                      worker B keeps running boot recovery (``register_all``).
                      Reports false reaps of live jobs (must be 0).
  B. dead worker    - worker C enqueues jobs (some running, some queued) and is
                      killed (``os._exit``). Measures the time until every job is
                      a definitive ``error``: (B1) by a live sibling's periodic
                      heartbeat recovery, (B2) lazily by ``get_job`` polling.
  C. heartbeat tick - cost of one heartbeat tick (deadline check + batched
                      refresh + recovery sweep) with K held jobs.
  D. retry policy   - transient-once jobs succeed on attempt 2; permanent errors
                      and ``retries=0`` jobs run exactly once.

Timings are SCALED DOWN (default heartbeat 0.2 s, lease 1.0 s) so a run takes
under a minute; production defaults are 30 s / 120 s. Results are reported both
in seconds and as a multiple of the lease. SQLite on local disk, not Postgres:
Postgres behaviour is not measured here.

Isolation (same as ``bench_server.py``): every process drops all environment
variables except OS basics, sets offline settings, and ``chdir``s into a fresh
temp dir before importing ``src`` so no ``.env`` is read. No network, no keys.

Usage (repo root)::

    uv run python scripts/bench_jobs.py
    uv run python scripts/bench_jobs.py --heartbeat 0.2 --lease 1.0 --jobs 8 --out <path outside the repo>.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import isolate_environment  # noqa: E402

MARK = "BENCHJOB "


def emit(obj: dict) -> None:
    print(MARK + json.dumps(obj), flush=True)


def _boot(workdir: str, args) -> None:
    """Isolate env, point settings at the shared DB, import nothing from .env."""
    isolate_environment(workdir)
    os.environ.update(
        {
            "JOBS_BACKEND": "thread",
            "JOB_HEARTBEAT_SECONDS": str(args.heartbeat),
            "JOB_LEASE_SECONDS": str(args.lease),
            "JOB_RETRY_BACKOFF_SECONDS": "0",
            "JOB_TIMEOUT_SECONDS": "3600",
            "LOG_LEVEL": "WARNING",
        }
    )
    os.chdir(workdir)
    sys.path.insert(0, str(REPO_ROOT))


def _statuses(ids) -> dict:
    """Raw row statuses (no lazy recovery side effect)."""
    from src.core.db import SessionLocal
    from src.core.models import Job

    db = SessionLocal()
    try:
        rows = db.query(Job.id, Job.status).filter(Job.id.in_(list(ids))).all()
        return {r.id: r.status for r in rows}
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# child roles
# --------------------------------------------------------------------------- #
def role_worker(args) -> None:
    """Run N live jobs of ``--work-seconds`` each, all concurrently."""
    from src.core import job_queue
    from src.core.config import settings

    settings.job_workers = args.jobs
    started = [threading.Event() for _ in range(args.jobs)]

    def make(ev):
        def work():
            ev.set()
            time.sleep(args.work_seconds)
            return {"ok": True}

        return work

    ids = [job_queue.enqueue("bench_live", make(ev), user_id="bench") for ev in started]
    for ev in started:
        ev.wait(30)
    emit({"event": "started", "ids": ids})
    deadline = time.monotonic() + args.work_seconds + 30
    while time.monotonic() < deadline:
        st = _statuses(ids)
        if all(s in ("done", "error") for s in st.values()):
            break
        time.sleep(0.05)
    emit({"event": "final", "statuses": _statuses(ids)})
    job_queue.shutdown()


def role_booter(args) -> None:
    """A sibling worker: own keep-alive job (heartbeat thread) + repeated boot recovery."""
    from src.core import job_queue
    from src.core.job_handlers import register_all

    stop = threading.Event()
    job_queue.enqueue("bench_keepalive", lambda: (stop.wait(args.duration + 5), {})[1])
    boots = 0
    end = time.monotonic() + args.duration
    emit({"event": "ready"})
    while time.monotonic() < end:
        if args.boot_interval > 0:
            register_all()
            boots += 1
            time.sleep(args.boot_interval)
        else:
            time.sleep(0.05)
    stop.set()
    emit({"event": "done", "boots": boots})
    job_queue.shutdown()


def role_crasher(args) -> None:
    """Enqueue jobs (some running, some queued), then die without cleanup."""
    from src.core import job_queue
    from src.core.config import settings

    settings.job_workers = max(1, args.jobs // 2)
    started = threading.Event()

    def work():
        started.set()
        time.sleep(3600)
        return {}

    ids = [job_queue.enqueue("bench_doomed", work, user_id="bench") for _ in range(args.jobs)]
    started.wait(30)
    time.sleep(args.heartbeat * 1.5)  # at least one heartbeat landed
    emit({"event": "crashing", "ids": ids, "statuses": _statuses(ids), "t": time.time()})
    os._exit(3)


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def spawn(role: str, workdir: str, args, **extra) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "--role",
        role,
        "--workdir",
        workdir,
        "--heartbeat",
        str(args.heartbeat),
        "--lease",
        str(args.lease),
        "--jobs",
        str(args.jobs),
    ]
    for key, value in extra.items():
        cmd += [f"--{key.replace('_', '-')}", str(value)]
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    )


def read_event(proc: subprocess.Popen, name: str, log: list, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    for line in proc.stdout:
        log.append(line.rstrip())
        if line.startswith(MARK):
            obj = json.loads(line[len(MARK) :])
            if obj.get("event") == name:
                return obj
        if time.monotonic() > deadline:
            break
    raise RuntimeError(f"no '{name}' event from child; last output:\n" + "\n".join(log[-20:]))


def scenario_sibling_boot(workdir, args) -> dict:
    log: list = []
    work_seconds = args.lease * 4
    a = spawn("worker", workdir, args, work_seconds=work_seconds)
    started = read_event(a, "started", log)
    b = spawn("booter", workdir, args, duration=work_seconds, boot_interval=args.lease / 4)
    read_event(b, "ready", log)
    b_done = read_event(b, "done", log, timeout=work_seconds + 60)
    final = read_event(a, "final", log, timeout=work_seconds + 60)
    a.wait(30)
    b.wait(30)
    statuses = list(final["statuses"].values())
    return {
        "scenario": "A_sibling_boot",
        "live_jobs": len(started["ids"]),
        "job_seconds": work_seconds,
        "job_seconds_in_leases": round(work_seconds / args.lease, 2),
        "sibling_boot_recoveries": b_done["boots"],
        "false_reaps": statuses.count("error"),
        "completed_done": statuses.count("done"),
        "pass": statuses.count("error") == 0 and statuses.count("done") == len(statuses),
    }


def _crash(workdir, args, log):
    c = spawn("crasher", workdir, args)
    crash = read_event(c, "crashing", log)
    c.wait(30)
    return crash


def scenario_dead_worker(workdir, args, lazy: bool) -> dict:
    from src.core import job_queue

    log: list = []
    b = None
    if not lazy:
        b = spawn("booter", workdir, args, duration=args.lease * 6, boot_interval=0)
        read_event(b, "ready", log)
    crash = _crash(workdir, args, log)
    ids = crash["ids"]
    t_crash = crash["t"]
    before = list(crash["statuses"].values())
    recovered: dict = {}
    deadline = time.time() + args.lease * 10
    while len(recovered) < len(ids) and time.time() < deadline:
        if lazy:
            current = {i: job_queue.get_job(i)["status"] for i in ids if i not in recovered}
        else:
            current = _statuses([i for i in ids if i not in recovered])
        for job_id, status in current.items():
            if status == "error":
                recovered[job_id] = time.time() - t_crash
        time.sleep(0.02)
    if b is not None:
        read_event(b, "done", log, timeout=args.lease * 10)
        b.wait(30)
    times = sorted(recovered.values())
    return {
        "scenario": "B2_dead_worker_lazy_get_job" if lazy else "B1_dead_worker_periodic",
        "jobs": len(ids),
        "running_at_crash": before.count("running"),
        "queued_at_crash": before.count("queued"),
        "recovered": len(recovered),
        "time_to_error_s_median": round(statistics.median(times), 3) if times else None,
        "time_to_error_s_max": round(max(times), 3) if times else None,
        "time_to_error_in_leases_max": round(max(times) / args.lease, 2) if times else None,
        "pass": len(recovered) == len(ids),
    }


def scenario_tick_cost(args) -> list:
    from src.core import job_queue
    from src.core.config import settings

    results = []
    for k in (1, 10, 50):
        job_queue.shutdown()
        settings.job_workers = k
        stop = threading.Event()
        ids = [
            job_queue.enqueue("bench_tick", lambda: (stop.wait(60), {})[1], user_id="bench")
            for _ in range(k)
        ]
        time.sleep(0.2)
        samples = []
        for _ in range(30):
            t0 = time.perf_counter()
            job_queue._tick()
            samples.append((time.perf_counter() - t0) * 1000)
        stop.set()
        deadline = time.monotonic() + 30
        while job_queue.held_job_ids() & set(ids) and time.monotonic() < deadline:
            time.sleep(0.02)
        results.append(
            {
                "scenario": "C_heartbeat_tick",
                "held_jobs": k,
                "tick_ms_median": round(statistics.median(samples), 2),
                "tick_ms_p95": round(sorted(samples)[int(len(samples) * 0.95) - 1], 2),
            }
        )
    job_queue.shutdown()
    return results


def scenario_retry(args) -> dict:
    from src.core import job_queue
    from src.core.config import settings
    from src.core.errors import UserFacingError
    from src.core.llm import LLMTimeoutError

    settings.jobs_backend = "inline"
    settings.job_max_retries = 1
    n = 50

    def run(make, **kw):
        calls, statuses = [], []
        for _ in range(n):
            box = {"c": 0}
            job_id = job_queue.enqueue("bench_retry", make(box), user_id="bench", **kw)
            calls.append(box["c"])
            statuses.append(job_queue.get_job(job_id)["status"])
        return calls, statuses

    def transient_once(box):
        def work():
            box["c"] += 1
            if box["c"] == 1:
                raise LLMTimeoutError("bench")
            return {}

        return work

    def permanent(box):
        def work():
            box["c"] += 1
            raise UserFacingError("bad input")

        return work

    t_calls, t_st = run(transient_once)
    p_calls, p_st = run(permanent)
    z_calls, z_st = run(transient_once, retries=0)
    settings.jobs_backend = "thread"
    ok = (
        set(t_calls) == {2}
        and set(t_st) == {"done"}
        and set(p_calls) == {1}
        and set(p_st) == {"error"}
        and set(z_calls) == {1}
        and set(z_st) == {"error"}
    )
    return {
        "scenario": "D_retry_policy",
        "jobs_per_case": n,
        "transient_once_attempts": sorted(set(t_calls)),
        "transient_once_status": sorted(set(t_st)),
        "permanent_attempts": sorted(set(p_calls)),
        "retries0_attempts": sorted(set(z_calls)),
        "pass": ok,
    }


def orchestrate(args) -> int:
    workdir = tempfile.mkdtemp(prefix="upsc_bench_jobs_")
    ok = False
    try:
        _boot(workdir, args)
        from src.core.db import init_db

        init_db()
        rows = [scenario_sibling_boot(workdir, args)]
        rows.append(scenario_dead_worker(workdir, args, lazy=False))
        rows.append(scenario_dead_worker(workdir, args, lazy=True))
        rows += scenario_tick_cost(args)
        rows.append(scenario_retry(args))
        meta = {
            "heartbeat_s": args.heartbeat,
            "lease_s": args.lease,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "db": "sqlite (local file, shared by all processes)",
        }
        for row in rows:
            print(json.dumps(row))
        print("\nscenario                        result")
        for row in rows:
            key = row["scenario"]
            shown = {k: v for k, v in row.items() if k != "scenario"}
            print(f"{key:<31} {shown}")
        ok = all(row.get("pass", True) for row in rows)
        print(f"\nmeta: {meta}\nALL PASS: {ok}")
        if args.out:
            Path(args.out).write_text(json.dumps({"meta": meta, "rows": rows}, indent=2))
        return 0 if ok else 1
    finally:
        os.chdir(REPO_ROOT)
        if ok:
            from src.core.db import engine

            engine.dispose()
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"kept work dir for inspection: {workdir}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--role", default="orchestrator")
    p.add_argument("--workdir", default="")
    p.add_argument("--heartbeat", type=float, default=0.2)
    p.add_argument("--lease", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--work-seconds", type=float, default=0)
    p.add_argument("--duration", type=float, default=0)
    p.add_argument("--boot-interval", type=float, default=0)
    p.add_argument("--out", default="")
    args = p.parse_args()
    if args.lease <= 2 * args.heartbeat:
        p.error("--lease must exceed 2x --heartbeat")
    if args.role == "orchestrator":
        return orchestrate(args)
    _boot(args.workdir, args)
    {"worker": role_worker, "booter": role_booter, "crasher": role_crasher}[args.role](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
