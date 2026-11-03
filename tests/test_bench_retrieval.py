"""Offline tests for the Phase 06 retrieval benchmark (scripts/bench_retrieval.py).

No network, no API key: embeddings are deterministic fakes and Qdrant runs in
embedded mode under pytest's tmp_path. Covers the gold dataset, the embedding
budget/cache guardrails, the preflight (R9 as approved) and the isolation of
the benchmark from any configured production Qdrant.
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "bench_retrieval", REPO_ROOT / "scripts" / "bench_retrieval.py"
)
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


@pytest.fixture(scope="module")
def dataset():
    return bench.load_dataset(bench.DEFAULT_DATASET)


@pytest.fixture(scope="module")
def docs_by_corpus(dataset):
    return {n: bench.corpus_documents(n, s) for n, s in bench.active_corpora(dataset).items()}


class _CountingInner:
    def __init__(self):
        self.calls = []
        self._fake = bench.FakeEmbeddings()

    def embed_documents(self, texts, **kwargs):
        self.calls.append(("documents", list(texts)))
        return self._fake.embed_documents(texts)

    def embed_query(self, text, **kwargs):
        self.calls.append(("query", text))
        return self._fake.embed_query(text)


def _budgeted(inner, **kw):
    return bench.BudgetedCachingEmbeddings(
        inner,
        model_id="m@64",
        cache_path=kw.get("cache_path"),
        max_requests=kw.get("max_requests", 500),
        max_tokens=kw.get("max_tokens", 1_000_000),
    )


# --------------------------------------------------------------------------- #
# Gold dataset
# --------------------------------------------------------------------------- #
def test_gold_dataset_size_coverage_and_validity(dataset, docs_by_corpus):
    queries = dataset["queries"]
    answerable = [q for q in queries if q["answerable"]]
    unanswerable = [q for q in queries if not q["answerable"]]
    assert len(answerable) >= 60
    assert len(unanswerable) >= 10
    # Every non-NCERT retrieval path has answerable and unanswerable queries.
    for corpus in ("mentor_kb", "upload_style", "lecture_style", "pyq_style"):
        assert any(q["corpus"] == corpus and q["answerable"] for q in queries)
        assert any(q["corpus"] == corpus and not q["answerable"] for q in queries)
    # NCERT is supported but owner-supplied (Q1 = b): no chapters, no queries yet.
    assert dataset["corpora"]["ncert"]["chapters"] == []
    assert not any(q["corpus"] == "ncert" for q in queries)
    assert "ncert" not in bench.active_corpora(dataset)
    assert bench.validate_dataset(dataset, docs_by_corpus) == []


def test_validator_rejects_bad_rows(dataset, docs_by_corpus):
    broken = json.loads(json.dumps(dataset))
    broken["queries"][0]["gold"][0]["quote"] = "this sentence is not in the corpus at all"
    broken["queries"].append(dict(broken["queries"][1]))  # duplicate id
    broken["queries"].append(
        {
            "id": "x-u",
            "corpus": "upload_style",
            "answerable": False,
            "question": "q",
            "gold": [{"quote": "a"}],
        }
    )
    errors = bench.validate_dataset(broken, docs_by_corpus)
    assert any("not inside any single chunk" in e for e in errors)
    assert any("duplicate id" in e for e in errors)
    assert any("unanswerable query must not have gold" in e for e in errors)


# --------------------------------------------------------------------------- #
# Embedding guardrails
# --------------------------------------------------------------------------- #
def test_cache_reuses_by_text_and_kind():
    inner = _CountingInner()
    emb = _budgeted(inner)
    emb.embed_documents(["alpha", "beta", "alpha"])
    emb.embed_documents(["alpha"])
    emb.embed_query("alpha")  # queries are cached separately from documents
    emb.embed_query("alpha")
    assert inner.calls == [("documents", ["alpha", "beta"]), ("query", "alpha")]
    assert emb.requests == 3 and emb.hits == 3


def test_budget_stops_before_sending():
    inner = _CountingInner()
    emb = _budgeted(inner, max_requests=2)
    emb.embed_documents(["one"])
    with pytest.raises(bench.BudgetExceeded):
        emb.embed_documents(["two", "three"])
    assert inner.calls == [("documents", ["one"])]  # nothing sent for the refused call
    emb_tok = _budgeted(_CountingInner(), max_tokens=5)
    with pytest.raises(bench.BudgetExceeded):
        emb_tok.embed_query("x" * 100)


def test_cache_persists_between_runs(tmp_path):
    path = tmp_path / "cache.json"
    first = _budgeted(_CountingInner(), cache_path=path)
    first.embed_documents(["persisted text"])
    first.save()
    inner = _CountingInner()
    second = _budgeted(inner, cache_path=path)
    second.embed_documents(["persisted text"])
    assert inner.calls == [] and second.run_requests == 0
    # The usage ledger is cumulative across runs that share the cache.
    assert second.requests == 1 and second.tokens == first.tokens


def test_budget_is_cumulative_across_runs(tmp_path):
    path = tmp_path / "cache.json"
    first = _budgeted(_CountingInner(), cache_path=path, max_requests=2)
    first.embed_documents(["a", "b"])
    first.save()
    inner = _CountingInner()
    second = _budgeted(inner, cache_path=path, max_requests=2)
    with pytest.raises(bench.BudgetExceeded):
        second.embed_query("c")
    assert inner.calls == []


def test_pacing_keeps_texts_per_minute_under_the_limit():
    now = [0.0]
    sleeps = []

    def _sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    inner = _CountingInner()
    emb = bench.BudgetedCachingEmbeddings(
        inner,
        model_id="m",
        cache_path=None,
        max_requests=500,
        max_tokens=10**6,
        max_texts_per_minute=3,
        sleep=_sleep,
        clock=lambda: now[0],
    )
    emb.embed_documents(["d1", "d2", "d3", "d4", "d5"])  # split into batches of <= 3
    assert [c[1] for c in inner.calls] == [["d1", "d2", "d3"], ["d4", "d5"]]
    assert sleeps and now[0] >= 60.0
    emb.embed_query("q1")  # 2 texts in the current window + 1 = 3 -> no wait
    assert len(sleeps) == 1


def test_plan_counts_only_uncached_texts(dataset, docs_by_corpus):
    emb = _budgeted(_CountingInner())
    plan = bench.plan_embeddings(dataset, docs_by_corpus, emb)
    assert plan["requests"] == plan["uncached_documents"] + plan["uncached_queries"]
    assert plan["requests"] <= bench.DEFAULT_MAX_REQUESTS
    assert plan["estimated_tokens"] <= bench.DEFAULT_MAX_TOKENS
    first_doc = next(iter(docs_by_corpus.values()))[0].page_content
    emb.embed_documents([first_doc])
    assert bench.plan_embeddings(dataset, docs_by_corpus, emb)["uncached_documents"] == (
        plan["uncached_documents"] - 1
    )


# --------------------------------------------------------------------------- #
# Preflight (R9 as approved)
# --------------------------------------------------------------------------- #
def test_preflight_missing_qdrant_library_is_actionable(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        bench.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "qdrant_client" else real(name, *a),
    )
    with pytest.raises(bench.PreflightError, match="uv sync"):
        bench.preflight(live=False)


def test_preflight_live_requires_gemini_and_key_without_leaking_it(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "google_api_key", "SECRET-KEY-VALUE")
    with pytest.raises(bench.PreflightError) as exc:
        bench.preflight(live=True)
    assert "Gemini" in str(exc.value) and "SECRET-KEY-VALUE" not in str(exc.value)

    monkeypatch.setattr(settings, "embedding_provider", "gemini")
    monkeypatch.setattr(settings, "google_api_key", "")
    with pytest.raises(bench.PreflightError, match="never paste it into chat"):
        bench.preflight(live=True)

    monkeypatch.setattr(settings, "google_api_key", "SECRET-KEY-VALUE")
    info = bench.preflight(live=True)
    assert info["api_key_configured"] is True
    assert "SECRET-KEY-VALUE" not in json.dumps(info)


# --------------------------------------------------------------------------- #
# End to end, offline, isolated
# --------------------------------------------------------------------------- #
def _subset(dataset, per_corpus=3):
    out = json.loads(json.dumps(dataset))
    picked, counts = [], {}
    for q in out["queries"]:
        key = (q["corpus"], q["answerable"])
        if counts.get(key, 0) < per_corpus:
            counts[key] = counts.get(key, 0) + 1
            picked.append(q)
    out["queries"] = picked
    return out


def test_run_benchmark_offline_isolated_and_consistent(dataset, monkeypatch, tmp_path):
    # A configured production Qdrant must be ignored; any socket use fails the test.
    monkeypatch.setattr(settings, "qdrant_url", "http://production-qdrant.invalid:6333")
    monkeypatch.setattr(settings, "qdrant_path", "")

    def _no_network(*args, **kwargs):
        raise AssertionError("benchmark attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    import src.core.vector_store as vs

    before = (vs._qdrant_client_instance, vs._embeddings_instance)
    emb = _budgeted(bench.FakeEmbeddings())
    qdir = tmp_path / "qdrant"
    rows = bench.run_benchmark(
        _subset(dataset), embeddings=emb, qdrant_dir=qdir, dim=bench.FAKE_DIM, repeats=1
    )

    assert {r["corpus"] for r in rows} == {
        "mentor_kb",
        "upload_style",
        "lecture_style",
        "pyq_style",
    }
    # The reconstructed production ordering equals the real production function.
    assert all(r["production_consistent"] for r in rows)
    assert all(r["topk_matches_pool"] for r in rows)
    # Review Q2: the independently fetched 2k pool equals the shared pool prefix.
    assert all(r["overfetch_matches_pool"] for r in rows)
    for r in rows:
        assert set(r["variants"]) == {
            "dense_k",
            "production",
            "rrf_same_k",
            "overfetch_2k",
            "dense_10",
            "rrf_10",
        }
        if r["answerable"]:
            k = r["k"]
            # Re-ordering the same k candidates never changes recall@k.
            assert (
                r["variants"]["rrf_same_k"][f"recall@{k}"]
                == r["variants"]["dense_k"][f"recall@{k}"]
            )
    assert {r["k"] for r in rows if r["corpus"] == "mentor_kb"} == {4}
    assert {r["k"] for r in rows if r["corpus"] == "pyq_style"} == {8}
    # Only the temp store was written; settings and singletons are restored.
    assert qdir.exists() and any(qdir.iterdir())
    assert settings.qdrant_url == "http://production-qdrant.invalid:6333"
    assert settings.qdrant_path == ""
    assert (vs._qdrant_client_instance, vs._embeddings_instance) == before

    summary = bench.summarize(rows, resamples=200)
    assert set(summary) == {
        "ALL",
        "SHARED_PIPELINE",
        "mentor_kb",
        "upload_style",
        "lecture_style",
        "pyq_style",
    }
    # Review Q4: pooled metrics say they mix different k; single corpora do not.
    assert summary["ALL"]["k"] == [4, 5, 8] and "different k" in summary["ALL"]["k_note"]
    assert summary["mentor_kb"]["k"] == [4] and summary["mentor_kb"]["k_note"] == ""
    assert "headroom_recall10_minus_recall_at_production_k" in summary["ALL"]
    labels = {c["label"] for c in summary["ALL"]["comparisons"]}
    # Review Q3: the over-fetch effect is isolated from the RRF re-order.
    assert "over-fetch effect only: RRF 2k->k vs RRF k" in labels
    assert "RRF 2k->k vs production (combined for mentor KB)" in labels
    assert 0.0 <= summary["ALL"]["no_answer_false_positive_rate"] <= 1.0
    meta = {
        "date": "d",
        "commit": "c",
        "embeddings": "fake",
        "dataset": "x",
        "dataset_sha256": "0" * 64,
        "corpus_sha256": {"a": "0" * 64},
        "embedding": "e",
        "label_status": "Claude-authored, NOT yet human-verified",
    }
    report = bench.render_report(meta, summary)
    assert "Recall@k" in report and "NOT yet human-verified" in report
    assert "false-grounding" not in report.lower()


def test_cli_live_dry_run_sends_nothing_and_never_prints_the_key(tmp_path):
    env = {
        **os.environ,
        "GOOGLE_API_KEY": "SECRET-KEY-VALUE",
        "EMBEDDING_PROVIDER": "gemini",
        "QDRANT_URL": "http://production-qdrant.invalid:6333",
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMPDIR": str(tmp_path),
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "bench_retrieval.py"),
            "--cache",
            str(tmp_path / "c.json"),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "Embedding plan (live): model=" in output and "estimated_input_tokens" in output
    assert "Dry run only (no request sent)" in output
    assert "SECRET-KEY-VALUE" not in output
    assert not (tmp_path / "c.json").exists()  # nothing embedded, nothing cached


# --------------------------------------------------------------------------- #
# Review S1: corpus path validation (before any read or network request)
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A throwaway repo tree; ``bench.REPO_ROOT`` points at it for the test."""
    repo = tmp_path / "repo"
    (repo / "src" / "eval" / "corpora").mkdir(parents=True)
    (repo / "data" / "mentor_kb").mkdir(parents=True)
    (repo / "data" / "ncert").mkdir(parents=True)
    (repo / "src" / "eval" / "corpora" / "ok.md").write_text("fine", encoding="utf-8")
    (repo / "data" / "mentor_kb" / "facts.md").write_text("kb", encoding="utf-8")
    (repo / "data" / "ncert" / "ch1.md").write_text("ncert", encoding="utf-8")
    (repo / "src" / "eval" / "corpora" / ".env").write_text("KEY=x", encoding="utf-8")
    (repo / "src" / "eval" / "corpora" / ".env.local").write_text("KEY=x", encoding="utf-8")
    (repo / "src" / "eval" / "corpora" / "api_token.md").write_text("x", encoding="utf-8")
    (repo / "src" / "eval" / "corpora" / "creds.json").write_text("{}", encoding="utf-8")
    (repo / "src" / "eval" / "corpora" / "server.pem").write_text("x", encoding="utf-8")
    (repo / ".env").write_text("KEY=x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("private", encoding="utf-8")
    monkeypatch.setattr(bench, "REPO_ROOT", repo)
    return repo, outside


def test_corpus_path_accepts_only_files_inside_approved_roots(fake_repo):
    repo, _ = fake_repo
    assert (
        bench.resolve_corpus_path("src/eval/corpora/ok.md")
        == (repo / "src" / "eval" / "corpora" / "ok.md").resolve()
    )
    assert bench.resolve_corpus_path("data/mentor_kb/facts.md").name == "facts.md"
    assert bench.resolve_corpus_path("data/mentor_kb", allow_dir=True).is_dir()


@pytest.mark.parametrize(
    "rel",
    [
        "../outside/secret.md",  # traversal
        "src/eval/corpora/../../../.env",  # traversal back into the repo root
        ".env",  # outside the approved roots
        "src/eval/corpora/.env",  # .env variant inside a root
        "src/eval/corpora/.env.local",
        "src/eval/corpora/api_token.md",  # credential-like name
        "src/eval/corpora/creds.json",  # config/credential file type
        "src/eval/corpora/server.pem",
        "data/ncert/ch1.md",  # protected folder
        "data/mentor_kb",  # a directory where a file is required
        "src/eval/corpora/missing.md",
        "",
    ],
)
def test_corpus_path_rejects_unsafe_paths(fake_repo, rel):
    with pytest.raises(bench.CorpusPathError):
        bench.resolve_corpus_path(rel)


def test_corpus_path_rejects_absolute_paths(fake_repo):
    repo, outside = fake_repo
    with pytest.raises(bench.CorpusPathError, match="relative"):
        bench.resolve_corpus_path(str(repo / "src" / "eval" / "corpora" / "ok.md"))
    with pytest.raises(bench.CorpusPathError, match="relative"):
        bench.resolve_corpus_path(str(outside / "secret.md"))


def _link_dir(link, target):
    """Directory symlink, or an NTFS junction on Windows without symlink privilege."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return "junction"


def test_corpus_path_rejects_directory_link_escape(fake_repo):
    repo, outside = fake_repo
    dir_link = repo / "src" / "eval" / "corpora" / "linkdir"
    try:
        _link_dir(dir_link, outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory links not available here: {exc}")
    assert (dir_link / "secret.md").exists()  # the escape is real
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench.resolve_corpus_path("src/eval/corpora/linkdir/secret.md")


def test_corpus_path_rejects_file_symlink_escape(fake_repo):
    repo, outside = fake_repo
    file_link = repo / "src" / "eval" / "corpora" / "link.md"
    try:
        os.symlink(outside / "secret.md", file_link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks need extra privileges here: {exc}")
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench.resolve_corpus_path("src/eval/corpora/link.md")


def test_validate_corpus_paths_reports_every_bad_corpus(fake_repo):
    dataset = {
        "corpora": {
            "good": {"kind": "upload", "path": "src/eval/corpora/ok.md"},
            "env": {"kind": "upload", "path": ".env"},
            "kb_elsewhere": {"kind": "mentor_kb", "path": "src/eval/corpora"},
            "weird": {"kind": "shell", "path": "src/eval/corpora/ok.md"},
            "ncert": {"kind": "ncert", "chapters": []},
        },
        "queries": [],
    }
    errors = bench.validate_corpus_paths(dataset)
    assert len(errors) == 3
    assert any("'env'" in e for e in errors)
    assert any("exactly 'data/mentor_kb'" in e for e in errors)
    assert any("unknown kind" in e for e in errors)


# --------------------------------------------------------------------------- #
# CLI helpers for in-process main() runs (env, cwd and temp dir restored)
# --------------------------------------------------------------------------- #
@pytest.fixture
def cli(monkeypatch, tmp_path):
    for key in [*bench._FORCED_ENV, "QDRANT_PATH", "GOOGLE_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.setenv(key, os.environ.get(key, ""))
    monkeypatch.chdir(REPO_ROOT)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(bench.tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(bench, "DEFAULT_OUT_DIR", tmp_path / "out")
    return tmp_path, temp_root


def _write_dataset(tmp_path, dataset, corpus="upload_style", per_corpus=2, mutate=None):
    data = json.loads(json.dumps(dataset))
    data["queries"] = _subset(data, per_corpus)["queries"]
    data["queries"] = [q for q in data["queries"] if q["corpus"] == corpus]
    if mutate:
        mutate(data)
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _work_dirs(temp_root):
    return sorted(p.name for p in temp_root.glob(bench.WORK_DIR_PREFIX + "*"))


def _fake_args(tmp_path, dataset_path, *extra):
    return [
        "--fake-embeddings",
        "--dataset",
        str(dataset_path),
        "--repeats",
        "1",
        "--resamples",
        "50",
        "--report",
        str(tmp_path / "r.md"),
        "--json",
        str(tmp_path / "r.json"),
        *extra,
    ]


# --------------------------------------------------------------------------- #
# Review S2: temp work dir cleanup on success, failure and setup errors
# --------------------------------------------------------------------------- #
def test_cleanup_after_successful_run(cli, dataset):
    tmp_path, temp_root = cli
    keep = temp_root / (bench.WORK_DIR_PREFIX + "pre_existing")
    keep.mkdir()
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds)) == 0
    assert (tmp_path / "r.md").exists()
    assert _work_dirs(temp_root) == [keep.name]  # ours removed, the pre-existing one kept


def test_cleanup_after_failed_run(cli, dataset, monkeypatch):
    tmp_path, temp_root = cli
    ds = _write_dataset(tmp_path, dataset)
    seen = {}

    def _boom(*args, qdrant_dir, **kwargs):
        qdrant_dir.mkdir(parents=True)
        (qdrant_dir / "partial.bin").write_bytes(b"x")
        seen["dir"] = qdrant_dir
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(bench, "run_benchmark", _boom)
    assert bench.main(_fake_args(tmp_path, ds)) == 6
    assert not seen["dir"].exists()
    assert _work_dirs(temp_root) == []


def test_cleanup_after_setup_exception(cli, dataset, monkeypatch):
    tmp_path, temp_root = cli
    ds = _write_dataset(tmp_path, dataset)

    def _crash(**kwargs):
        raise KeyError("unexpected setup error")

    monkeypatch.setattr(bench, "preflight", _crash)
    with pytest.raises(KeyError):
        bench.main(_fake_args(tmp_path, ds))
    assert _work_dirs(temp_root) == []


def test_remove_work_dir_refuses_foreign_directories(cli, tmp_path):
    _, temp_root = cli
    foreign = tmp_path / "project_dir"
    foreign.mkdir()
    bench._remove_work_dir(foreign)  # not under the temp root
    other = temp_root / "someone_elses_dir"
    other.mkdir()
    bench._remove_work_dir(other)  # under the temp root, but not our prefix
    assert foreign.exists() and other.exists()


# --------------------------------------------------------------------------- #
# Review Q6: guardrail exit codes (offline, deterministic)
# --------------------------------------------------------------------------- #
def test_exit_3_invalid_dataset_and_path_checked_before_anything(cli, dataset, monkeypatch):
    tmp_path, temp_root = cli

    def _not_called(*args, **kwargs):
        raise AssertionError("must not run before the corpus paths are validated")

    monkeypatch.setattr(bench, "preflight", _not_called)
    monkeypatch.setattr(bench, "corpus_documents", _not_called)
    ds = _write_dataset(
        tmp_path,
        dataset,
        mutate=lambda d: d["corpora"]["upload_style"].update(path=".env"),
    )
    assert bench.main(_fake_args(tmp_path, ds)) == 3
    assert _work_dirs(temp_root) == []  # rejected before the temp dir was created


def test_exit_3_bad_gold_quote(cli, dataset):
    tmp_path, _ = cli

    def _break(d):
        d["queries"][0]["gold"][0]["quote"] = "not in the corpus at all"

    ds = _write_dataset(tmp_path, dataset, mutate=_break)
    assert bench.main(_fake_args(tmp_path, ds)) == 3


def test_exit_4_plan_over_budget_sends_nothing(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    monkeypatch.setattr(
        bench, "run_benchmark", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds, "--max-requests", "1")) == 4


def test_exit_5_budget_reached_mid_run_saves_ledger(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    cache = tmp_path / "cache.json"

    def _spend(*args, embeddings, **kwargs):
        embeddings.embed_query("one text")
        raise bench.BudgetExceeded("simulated mid-run limit")

    monkeypatch.setattr(bench, "run_benchmark", _spend)
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds, "--cache", str(cache))) == 5
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["ledger"]["requests"] == 1


def test_exit_6_provider_error_saves_ledger_and_never_retries(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    cache = tmp_path / "cache.json"
    calls = []

    def _provider_error(*args, embeddings, **kwargs):
        calls.append(1)
        embeddings.embed_query("charged text")
        raise RuntimeError("429 RESOURCE_EXHAUSTED key=SECRET-KEY-VALUE")

    monkeypatch.setattr(bench, "run_benchmark", _provider_error)
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds, "--cache", str(cache))) == 6
    assert calls == [1]
    assert json.loads(cache.read_text(encoding="utf-8"))["ledger"]["requests"] == 1


def test_exit_6_message_never_contains_provider_text(cli, dataset, monkeypatch, capsys):
    tmp_path, _ = cli

    def _provider_error(*args, **kwargs):
        raise RuntimeError("provider body with SECRET-KEY-VALUE")

    monkeypatch.setattr(bench, "run_benchmark", _provider_error)
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds)) == 6
    captured = capsys.readouterr()
    assert "SECRET-KEY-VALUE" not in captured.out + captured.err


def test_exit_7_cache_only_miss_never_calls_the_api(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    cache = tmp_path / "empty_cache.json"
    ds = _write_dataset(tmp_path, dataset)
    args = [
        "--cache-only",
        "--dataset",
        str(ds),
        "--cache",
        str(cache),
        "--report",
        str(tmp_path / "r.md"),
        "--json",
        str(tmp_path / "r.json"),
    ]
    assert bench.main(args) == 7
    assert not cache.exists()  # cache-only never writes the cache


def test_exit_7_cache_miss_during_run(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    ds = _write_dataset(tmp_path, dataset)
    monkeypatch.setattr(bench, "plan_embeddings", lambda *a, **k: _empty_plan())

    def _miss(*args, embeddings, **kwargs):
        embeddings.embed_query("never cached")  # CacheOnlyInner raises

    monkeypatch.setattr(bench, "run_benchmark", _miss)
    args = ["--cache-only", "--dataset", str(ds), "--cache", str(tmp_path / "c.json")]
    assert bench.main(args) == 7


def _empty_plan():
    return {
        "unique_documents": 0,
        "unique_queries": 0,
        "uncached_documents": 0,
        "uncached_queries": 0,
        "requests": 0,
        "estimated_tokens": 0,
        "estimated_cost_usd": 0.0,
    }


def test_offline_modes_block_sockets_and_restore_them(cli, dataset, monkeypatch):
    tmp_path, _ = cli
    original = socket.socket.connect
    observed = {}

    def _probe(*args, **kwargs):
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", 9))
        except ConnectionRefusedError as exc:
            observed["error"] = str(exc)
        finally:
            s.close()
        return []

    monkeypatch.setattr(bench, "run_benchmark", _probe)
    monkeypatch.setattr(bench, "summarize", lambda rows, **k: {})
    ds = _write_dataset(tmp_path, dataset)
    assert bench.main(_fake_args(tmp_path, ds)) == 0
    assert "network disabled" in observed["error"]
    assert socket.socket.connect is original


# --------------------------------------------------------------------------- #
# Review Q6: the consistency check really detects a production divergence
# --------------------------------------------------------------------------- #
def test_consistency_check_detects_production_divergence(dataset, monkeypatch, tmp_path):
    import src.core.mentor_kb as mentor_kb
    import src.core.vector_store as vs

    monkeypatch.setattr(vs, "similarity_search", lambda *a, **k: "tampered context")
    monkeypatch.setattr(mentor_kb, "search_kb", lambda *a, **k: {"context": "tampered"})
    sub = _subset(dataset, per_corpus=1)
    sub["queries"] = [q for q in sub["queries"] if q["corpus"] in ("upload_style", "mentor_kb")]
    rows = bench.run_benchmark(
        sub,
        embeddings=_budgeted(bench.FakeEmbeddings()),
        qdrant_dir=tmp_path / "qdrant",
        dim=bench.FAKE_DIM,
        repeats=1,
    )
    assert rows and not any(r["production_consistent"] for r in rows)
    summary = bench.summarize(rows, resamples=50)
    assert summary["ALL"]["checks"]["production_consistent"] is False


def test_production_check_uses_the_real_function_per_workflow(dataset, monkeypatch, tmp_path):
    """Review Q1: upload/lecture/PYQ use similarity_search, not the _with_sources variant."""
    import src.core.vector_store as vs

    called = []
    real = vs.similarity_search

    def _spy(*args, **kwargs):
        called.append(1)
        return real(*args, **kwargs)

    def _wrong(*args, **kwargs):
        raise AssertionError("the PDF/Lecture/PYQ workflows do not call this function")

    monkeypatch.setattr(vs, "similarity_search", _spy)
    monkeypatch.setattr(vs, "similarity_search_with_sources", _wrong)
    sub = _subset(dataset, per_corpus=1)
    sub["queries"] = [
        q for q in sub["queries"] if q["corpus"] in ("upload_style", "lecture_style", "pyq_style")
    ]
    rows = bench.run_benchmark(
        sub,
        embeddings=_budgeted(bench.FakeEmbeddings()),
        qdrant_dir=tmp_path / "qdrant",
        dim=bench.FAKE_DIM,
        repeats=1,
    )
    assert called and all(r["production_consistent"] for r in rows)


# --------------------------------------------------------------------------- #
# Review SF-1: every mentor-KB file is validated individually before it is read
# --------------------------------------------------------------------------- #
_TOPPER = "---\ntopper_name: Test Topper\nrank: AIR 9\n---\n\nRevise polity daily.\n"


@pytest.fixture
def mentor_repo(fake_repo):
    """fake_repo + a copy of the real topper-ingest script and a small mentor KB."""
    repo, outside = fake_repo
    (repo / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "ingest_topper_md.py", repo / "scripts")
    toppers = repo / "data" / "mentor_kb" / "toppers"
    toppers.mkdir()
    (toppers / "test-topper.md").write_text(_TOPPER, encoding="utf-8")
    (repo / "data" / "mentor_kb" / "upsc_facts.md").write_text("Prelims has 2 papers.", "utf-8")
    (outside / "leak.md").write_text("---\ntopper_name: Leak\n---\n\nPRIVATE", encoding="utf-8")
    return repo, outside


def _kb_dataset():
    return {
        "corpora": {"kb": {"kind": "mentor_kb", "path": "data/mentor_kb"}},
        "queries": [{"id": "q", "corpus": "kb", "answerable": False, "question": "x", "gold": []}],
    }


def test_mentor_kb_valid_regular_files_are_ingested_unchanged(mentor_repo):
    sources = bench._mentor_sources()
    labels = [s["metadata"]["source"] for s in sources]
    assert labels == ["Test Topper (AIR 9)", "Verified UPSC facts"]
    assert "Revise polity daily." in sources[0]["text"]
    assert set(bench._corpus_hashes(_kb_dataset())) == {"kb"}


def test_mentor_kb_real_repo_sources_match_the_unguarded_ingest_script():
    import contextlib
    import io

    spec = importlib.util.spec_from_file_location(
        "_ingest_ref", REPO_ROOT / "scripts" / "ingest_topper_md.py"
    )
    module = importlib.util.module_from_spec(spec)
    cwd = os.getcwd()
    try:
        os.chdir(REPO_ROOT)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
            reference = module.collect_sources()
    finally:
        os.chdir(cwd)
    assert bench._mentor_sources() == reference


def test_mentor_kb_rejects_forbidden_file_names(mentor_repo):
    repo, _ = mentor_repo
    (repo / "data" / "mentor_kb" / "toppers" / "api_token.md").write_text(_TOPPER, "utf-8")
    with pytest.raises(bench.CorpusPathError, match="forbidden"):
        bench._mentor_sources()
    with pytest.raises(bench.CorpusPathError, match="forbidden"):
        bench._corpus_hashes(_kb_dataset())


def test_mentor_kb_file_must_stay_in_the_mentor_root(mentor_repo):
    repo, outside = mentor_repo
    # Another approved corpus root is still not the mentor-KB root.
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench.mentor_kb_file("src/eval/corpora/ok.md")
    with pytest.raises(bench.CorpusPathError, match="outside the repo"):
        bench.mentor_kb_file(outside / "leak.md")
    with pytest.raises(bench.CorpusPathError, match="'..'"):
        bench.mentor_kb_file("data/mentor_kb/../../outside/leak.md")
    assert bench.mentor_kb_file(repo / "data" / "mentor_kb" / "upsc_facts.md").name == (
        "upsc_facts.md"
    )


def test_mentor_kb_rejects_a_linked_directory_escape(mentor_repo):
    repo, outside = mentor_repo
    toppers = repo / "data" / "mentor_kb" / "toppers"
    shutil.rmtree(toppers)
    try:
        _link_dir(toppers, outside)  # toppers -> outside (symlink or Windows junction)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory links not available here: {exc}")
    assert (toppers / "leak.md").exists()  # the escape is real
    real_open = open
    opened = []

    def _spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    import builtins

    original = builtins.open
    builtins.open = _spy
    try:
        with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
            bench._mentor_sources()
    finally:
        builtins.open = original
    assert not any("leak.md" in f for f in opened)  # validated before any open
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench._corpus_hashes(_kb_dataset())


def test_mentor_kb_rejects_a_file_symlink_escape(mentor_repo):
    repo, outside = mentor_repo
    link = repo / "data" / "mentor_kb" / "toppers" / "zz-link.md"
    try:
        os.symlink(outside / "leak.md", link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks need extra privileges here: {exc}")
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench._mentor_sources()
    with pytest.raises(bench.CorpusPathError, match="outside the approved roots"):
        bench._corpus_hashes(_kb_dataset())
