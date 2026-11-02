"""Request-path benchmark for UPSC-AI-PRO (synthetic, local, single worker).

Measures what Phase 03 changed: whether slow sync work (an LLM call, a
blocking Upstash rate-limit check) stalls the single event loop. For each code
version and scenario it starts ``scripts/bench_server.py`` (the real app on one
uvicorn worker, offline, with a synthetic LLM and a synthetic Upstash), drives
closed-loop HTTP load, and probes ``GET /health`` in parallel.

These numbers are SYNTHETIC: the LLM and Upstash are fixed sleeps on
localhost. They show request-path behaviour (queueing, loop stalls, the
8-thread Upstash budget), NOT production latency. Render latency, real
Gemini/Groq latency and production throughput are not measured here.

Never reads ``.env`` and needs no API keys: see ``bench_server.py``. The server
gets an allow-listed environment (``bench_server.ENV_ALLOW``; OS credentials are
dropped) and a fresh work dir; all temp files of a run live under one directory
that is deleted after a successful run (kept, with the server log, on failure).

Usage (from the repo root; Windows PowerShell or Git Bash, Linux/macOS alike)::

    uv run python scripts/bench_request_path.py                       # working tree only
    uv run python scripts/bench_request_path.py --before-ref ad7d212  # before/after
    uv run python scripts/bench_request_path.py --scenarios A,D --runs 2 --out <path outside the repo>.json

Method: per (scenario, version, run) a fresh server; ``--concurrency`` clients
(scenario default) loop requests for ``--warmup`` + ``--duration`` seconds; a
request counts if it COMPLETES inside the measurement window, with its full
latency (including queueing); a separate client hits ``/health``, sleeping
``--probe-interval`` seconds after each response. Output: one JSON object per line on stdout, a
summary table at the end, and the full list as JSON with ``--out``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import allowed_environment  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = Path(__file__).resolve().parent / "bench_server.py"
EVAL_BODY = {"question": "What is federalism?", "answer": "Federalism divides power. " * 4}


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    method: str
    path: str
    concurrency: int
    upstash_rtt: float
    body: dict | None = None


SCENARIOS = {
    "A": Scenario(
        "A",
        "evaluator sync, synthetic LLM 0.5 s",
        "POST",
        "/api/v1/evaluator/evaluate/sync",
        10,
        0.0,
        EVAL_BODY,
    ),
    "B": Scenario("B", "light route, Upstash off", "GET", "/api/v1/cost/access", 20, 0.0),
    "C": Scenario("C", "light route, Upstash RTT 20 ms", "GET", "/api/v1/cost/access", 20, 0.02),
    "D": Scenario("D", "light route, Upstash RTT 200 ms", "GET", "/api/v1/cost/access", 20, 0.2),
}


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (same as numpy's default)."""
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(latencies_s: list[float], duration_s: float, errors: dict) -> dict:
    """Counts and latency percentiles in ms; ``None`` (JSON null) when there are no samples."""
    ms = [x * 1000 for x in latencies_s]

    def r1(value):
        return round(value, 1) if ms else None

    return {
        "n": len(ms),
        "errors": errors,
        "rps": round(len(ms) / duration_s, 2),
        "p50_ms": r1(percentile(ms, 50)),
        "p95_ms": r1(percentile(ms, 95)),
        "p99_ms": r1(percentile(ms, 99)),
        "max_ms": r1(max(ms) if ms else 0.0),
        "mean_ms": r1(statistics.fmean(ms) if ms else 0.0),
    }


def server_env() -> dict[str, str]:
    """OS basics only (``ENV_ALLOW``); the server adds its offline settings itself."""
    return allowed_environment(os.environ)


def export_ref(ref: str, parent: Path) -> Path:
    """``git archive`` src/, migrations/, alembic.ini of ``ref`` into ``parent``/ref."""
    if not ref or ref.startswith("-"):
        raise SystemExit(f"invalid --before-ref {ref!r}")
    dest = parent / "ref"
    dest.mkdir()
    archive = parent / "ref.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "-o",
            str(archive),
            ref,
            "src",
            "migrations",
            "alembic.ini",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    archive.unlink()
    return dest


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port_free(port: int, timeout: float) -> bool:
    """True once nothing listens on ``port``.

    On Windows a venv ``python.exe`` is a launcher whose child interpreter owns the
    socket; after the launcher is terminated the child exits a moment later, so the
    port stays busy briefly. Wait for that instead of failing or racing it.
    """
    deadline = time.time() + timeout
    while port_in_use(port):
        if time.time() >= deadline:
            return False
        time.sleep(0.2)
    return True


def start_server(
    python: str, code_root: Path, port: int, sc: Scenario, llm_s: float, log: Path, workdir: Path
):
    """Start bench_server.py; return (process, log handle) once THIS server answers."""
    if not wait_port_free(port, timeout=15):
        raise SystemExit(f"port {port} is already in use - stop that process or pass --port")
    nonce = secrets.token_hex(8)
    cmd = [
        python,
        str(SERVER),
        "--code-root",
        str(code_root),
        "--port",
        str(port),
        "--upstash-rtt",
        str(sc.upstash_rtt),
        "--llm-seconds",
        str(llm_s),
        "--nonce",
        nonce,
        "--workdir",
        str(workdir),
    ]
    log_f = open(log, "ab")
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir.parent, env=server_env(), stdout=log_f, stderr=log_f
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"benchmark server exited early (code {proc.returncode}); see {log}"
                )
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                if r.status_code == 200 and r.headers.get("x-bench-nonce") == nonce:
                    return proc, log_f
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"benchmark server did not become healthy; see {log}")
    except BaseException:  # incl. Ctrl-C: never leave a server behind on the port
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        log_f.close()
        raise


async def drive(port: int, sc: Scenario, warmup: float, duration: float, probe_every: float):
    base = f"http://127.0.0.1:{port}"
    limits = httpx.Limits(
        max_connections=sc.concurrency + 5, max_keepalive_connections=sc.concurrency + 5
    )
    start = time.perf_counter()
    measure_from, stop_at = start + warmup, start + warmup + duration
    route_lat: list[float] = []
    health_lat: list[float] = []
    errors: dict[str, int] = {}
    health_errors: dict[str, int] = {}

    async with httpx.AsyncClient(base_url=base, limits=limits, timeout=300) as client:

        async def worker():
            while time.perf_counter() < stop_at:
                t = time.perf_counter()
                try:
                    r = await client.request(sc.method, sc.path, json=sc.body)
                    code = str(r.status_code)
                except httpx.HTTPError as exc:
                    code = type(exc).__name__
                done = time.perf_counter()
                if measure_from <= done <= stop_at:
                    if code == "200":
                        route_lat.append(done - t)
                    else:
                        errors[code] = errors.get(code, 0) + 1

        async def prober():
            async with httpx.AsyncClient(base_url=base, timeout=300) as probe:
                while time.perf_counter() < stop_at:
                    t = time.perf_counter()
                    try:
                        code = str((await probe.get("/health")).status_code)
                    except httpx.HTTPError as exc:
                        code = type(exc).__name__
                    done = time.perf_counter()
                    if measure_from <= done <= stop_at:
                        if code == "200":
                            health_lat.append(done - t)
                        else:
                            health_errors[code] = health_errors.get(code, 0) + 1
                    await asyncio.sleep(probe_every)

        await asyncio.gather(prober(), *(worker() for _ in range(sc.concurrency)))
    return summarize(route_lat, duration, errors), summarize(health_lat, duration, health_errors)


def environment_info(python: str) -> dict:
    probe = (
        "import sys; from importlib.metadata import version as v; "
        "print(v('uvicorn'), v('anyio'), v('fastapi'), sys.version.split()[0])"
    )
    done = subprocess.run([python, "-c", probe], capture_output=True, text=True, env=server_env())
    out = done.stdout.split() if done.returncode == 0 else []
    names = ("uvicorn", "anyio", "fastapi", "server_python")
    versions = dict(zip(names, out)) if len(out) == len(names) else dict.fromkeys(names)
    return {
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        **versions,
        "workers": 1,
        "host": "127.0.0.1",
    }


def _run(args, keys: list[str], run_dir: Path, log: Path):
    versions: list[tuple[str, Path]] = []
    if args.before_ref:
        versions.append((f"before({args.before_ref})", export_ref(args.before_ref, run_dir)))
    elif args.before_root:
        versions.append(("before", Path(args.before_root).resolve()))
    versions.append(("after" if versions else "current", Path(args.after_root).resolve()))

    env_info = environment_info(args.python)
    meta = {
        "label": "SYNTHETIC local single-worker benchmark - not production latency",
        "environment": env_info,
        "warmup_s": args.warmup,
        "duration_s": args.duration,
        "probe_interval_s": args.probe_interval,
        "llm_seconds": args.llm_seconds,
        "runs": args.runs,
        "server_log": "kept in the run dir only on failure (path printed)",
    }
    print(json.dumps({"meta": meta}), flush=True)

    rows = []
    for key in keys:
        sc = SCENARIOS[key]
        for run in range(1, args.runs + 1):
            for label, root in versions:
                workdir = run_dir / f"server-{len(rows)}"
                proc, log_f = start_server(
                    args.python, root, args.port, sc, args.llm_seconds, log, workdir
                )
                try:
                    route, health = asyncio.run(
                        drive(args.port, sc, args.warmup, args.duration, args.probe_interval)
                    )
                finally:
                    proc.terminate()
                    proc.wait(timeout=30)
                    log_f.close()
                    wait_port_free(
                        args.port, timeout=15
                    )  # child interpreter may outlive the launcher
                row = {
                    "scenario": f"{key} {sc.name}",
                    "concurrency": sc.concurrency,
                    "version": label,
                    "run": run,
                    "route": route,
                    "health": health,
                }
                rows.append(row)
                print(json.dumps(row), flush=True)
                if route["n"] == 0:
                    print(
                        f"WARNING: no {sc.path} request completed inside the {args.duration} s window "
                        "(queueing longer than the window?) - increase --duration",
                        file=sys.stderr,
                    )

    return rows, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic request-path benchmark (see module doc).")
    ap.add_argument("--before-ref", help="git ref to compare against (exported with git archive)")
    ap.add_argument("--before-root", help="existing dir with src/, migrations/, alembic.ini")
    ap.add_argument("--after-root", default=str(REPO_ROOT), help="default: this working tree")
    ap.add_argument("--scenarios", default="A,B,C,D", help="comma list of " + ",".join(SCENARIOS))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--warmup", type=float, default=1.0, help="seconds, not measured")
    ap.add_argument("--duration", type=float, default=8.0, help="measured seconds")
    ap.add_argument(
        "--probe-interval", type=float, default=0.05, help="sleep after each /health response"
    )
    ap.add_argument("--llm-seconds", type=float, default=0.5, help="synthetic LLM block time")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the server")
    ap.add_argument("--out", help="write all results as a JSON file")
    args = ap.parse_args(argv)

    keys = [k.strip().upper() for k in args.scenarios.split(",") if k.strip()]
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        ap.error(f"unknown scenario(s): {unknown}")
    if args.before_ref and args.before_root:
        ap.error("use --before-ref or --before-root, not both")

    if args.duration <= 0 or args.warmup < 0 or args.probe_interval <= 0 or args.runs < 1:
        ap.error("need --duration > 0, --warmup >= 0, --probe-interval > 0, --runs >= 1")
    if args.llm_seconds < 0:
        ap.error("--llm-seconds must be >= 0")

    run_dir = Path(tempfile.mkdtemp(prefix="upsc_bench_run_"))  # private to this user and run
    log = run_dir / "server.log"
    ok = False
    try:
        rows, meta = _run(args, keys, run_dir, log)
        ok = True
    finally:
        if ok:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            print(f"benchmark failed; server log and temp files kept in {run_dir}", file=sys.stderr)

    print("\nSYNTHETIC, local, single-worker results (not production latency):")
    print(
        f"{'scenario':42} {'version':22} {'run':>3} | {'route rps':>9} {'p50':>8} {'p95':>8} "
        f"{'p99':>8} {'err':>4} | {'/health p50':>11} {'p95':>8} {'p99':>8} {'n':>4}"
    )
    for r in rows:
        a = {k: ("-" if v is None else v) for k, v in r["route"].items()}
        h = {k: ("-" if v is None else v) for k, v in r["health"].items()}
        print(
            f"{r['scenario'][:42]:42} {r['version'][:22]:22} {r['run']:>3} | {a['rps']:>9} "
            f"{a['p50_ms']:>8} {a['p95_ms']:>8} {a['p99_ms']:>8} {sum(a['errors'].values()):>4} | "
            f"{h['p50_ms']:>11} {h['p95_ms']:>8} {h['p99_ms']:>8} {h['n']:>4}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps({"meta": meta, "results": rows}, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
