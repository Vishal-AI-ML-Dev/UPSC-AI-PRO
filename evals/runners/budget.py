"""Live (T3) budget for the workflow evals (Phase 07, spec R4b).

Status: the ``utf8_bytes`` token bound is an EXPERIMENTAL reservation estimate, not a
validated provider-specific upper bound. ``evals/pricing.json`` ships every model as
``token_bound.status = "unvalidated"``, so ``validate_live_config`` refuses every
live run until the owner records validated bounds (separate approval).

Enforcement design (all checks happen BEFORE a request is sent):

* ``BudgetLedger``      one shared ledger: per-role counters + the total cost.
* ``LLMBudgetCallback`` ``raise_error=True`` handler attached to every model through
  ``src.core.llm.langchain_callbacks``. ``on_chat_model_start`` reserves the
  worst case - (1 + SDK retries) sends x (input bound + output bound) - and raises
  ``BudgetExceeded`` if a cap would be crossed. ``on_llm_end`` checks the
  provider-reported usage against the reservation (``BoundViolation`` when it is
  larger or missing).
* ``BudgetedEmbeddingsGuard`` wraps the RAW provider embeddings beneath the Phase 06
  cache, so cache hits are free and every miss is reserved first. It never relies
  on the LLM callback.

Production code swallows exceptions into "⚠️" messages, so the ledger also LATCHES
the first stop (``ledger.stop``): every later reservation is refused and the runner
checks the latch after each case.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # langchain-core is a project dependency; keep the import local to this module
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover
    BaseCallbackHandler = object  # type: ignore[assignment,misc]

PRICING_PATH = Path(__file__).resolve().parents[1] / "pricing.json"
ROLES = ("workflow", "judge", "embedding")
BOUND_METHOD = "utf8_bytes"
MESSAGE_OVERHEAD = 32
REQUEST_OVERHEAD = 64
EMBEDDING_TEXT_OVERHEAD = 16
JUDGE_TAG = "eval-judge"
BOUND_ASSUMPTIONS = (
    "EXPERIMENTAL ESTIMATE, UNVALIDATED: tokens <= UTF-8 bytes of the text sent "
    f"+ {MESSAGE_OVERHEAD}/message + {REQUEST_OVERHEAD}/request "
    f"(+{EMBEDDING_TEXT_OVERHEAD}/embedding text); output <= max_output_tokens; "
    "sends <= (1 + SDK max_retries) per model attempt. Holds only for tokenizers in "
    "which every text token covers >= 1 input byte; the Gemini tokenizer is not public."
)


class BudgetExceeded(RuntimeError):
    """A cap would be crossed; stopped before sending (runner exit 3)."""

    abort_run = True
    exit_code = 3


class BoundViolation(RuntimeError):
    """Provider usage exceeded the reservation or could not be checked (exit 4)."""

    abort_run = True
    exit_code = 4


class UndeclaredModel(RuntimeError):
    """A send names a model with no pricing / bound entry (config error, exit 2)."""

    abort_run = True
    exit_code = 2


class LiveRefused(RuntimeError):
    """Live mode refused before any client was built or request sent (exit 2)."""

    exit_code = 2

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("live mode refused: " + "; ".join(self.reasons))


# --------------------------------------------------------------------------- #
# Token bound (experimental)
# --------------------------------------------------------------------------- #
def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b if isinstance(b, str) else str(b.get("text", "")) if isinstance(b, dict) else ""
            for b in content
        )
    return "" if content is None else str(content)


def utf8_bound_messages(messages, extra: Any = None) -> int:
    """Experimental input-token bound for one chat request."""
    total = REQUEST_OVERHEAD
    for m in messages or []:
        content = m[1] if isinstance(m, tuple) else getattr(m, "content", m)
        total += len(_text_of(content).encode("utf-8")) + MESSAGE_OVERHEAD
    if extra:
        total += len(json.dumps(extra, default=str, sort_keys=True).encode("utf-8"))
    return total


def utf8_bound_text(text: str) -> int:
    return len((text or "").encode("utf-8")) + EMBEDDING_TEXT_OVERHEAD


# --------------------------------------------------------------------------- #
# Pricing / bound declarations
# --------------------------------------------------------------------------- #
def load_pricing(path: Path = PRICING_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise ValueError(f"{path}: expected an object with a 'models' map")
    return data


def normalize_model(name: str | None) -> str:
    name = (name or "").strip()
    return name[len("models/") :] if name.startswith("models/") else name


def bound_is_validated(entry: dict | None) -> bool:
    """Only an owner-recorded validation counts - never ``method`` alone."""
    tb = (entry or {}).get("token_bound") or {}
    return (
        tb.get("method") == BOUND_METHOD
        and tb.get("status") == "validated"
        and bool(tb.get("validated_on"))
        and bool(tb.get("evidence"))
    )


def price(entry: dict, kind: str) -> float | None:
    v = (entry or {}).get(f"{kind}_usd_per_million")
    return float(v) if isinstance(v, (int, float)) else None


@dataclass
class Caps:
    max_llm_sends: int | None = None
    max_llm_input_tokens: int | None = None
    max_embedding_requests: int | None = None
    max_embedding_tokens: int | None = None
    max_cost_usd: float | None = None
    max_judge_sends: int | None = None  # optional sub-cap

    REQUIRED = (
        "max_llm_sends",
        "max_llm_input_tokens",
        "max_embedding_requests",
        "max_embedding_tokens",
        "max_cost_usd",
    )

    def missing(self) -> list[str]:
        return [n for n in self.REQUIRED if getattr(self, n) is None or getattr(self, n) <= 0]


def run_models(settings, *, allow_fallback: bool, judge_model: str) -> dict[str, list[str]]:
    """Model ids each role of a live run can reach (no client is constructed)."""
    workflow = [settings.llm_model, settings.llm_fast_model]
    if allow_fallback:
        workflow += [settings.groq_model, settings.groq_fast_model]
    return {
        "workflow": list(dict.fromkeys(normalize_model(m) for m in workflow if m)),
        "judge": [normalize_model(judge_model)],
        "embedding": [normalize_model(settings.embedding_model)],
    }


def validate_live_config(
    pricing: dict,
    models: dict[str, list[str]],
    caps: Caps,
    *,
    sends_per_request: int,
    worst_single_input: int,
    output_bound: int,
) -> None:
    """Refuse (``LiveRefused``) unless every model is priced and owner-validated and
    the worst-case reservation can enforce every cap. Builds no client."""
    reasons = [f"required cap missing: --{n.replace('_', '-')}" for n in caps.missing()]
    table = pricing.get("models", {})
    for role, names in models.items():
        for name in names:
            entry = table.get(name)
            if entry is None:
                reasons.append(f"{role} model {name!r} is not declared in pricing.json")
                continue
            tb = entry.get("token_bound") or {}
            if tb.get("method") != BOUND_METHOD:
                reasons.append(f"{name}: token_bound.method must be {BOUND_METHOD!r}")
            if not bound_is_validated(entry):
                reasons.append(f"{name}: token bound is unvalidated ({role})")
            if role in ("workflow", "judge") and tb.get("provider_usage_check") != "required":
                reasons.append(f"{name}: provider_usage_check must be 'required' for an LLM")
            if price(entry, "input") is None:
                reasons.append(f"{name}: input price is null")
            if role != "embedding" and price(entry, "output") is None:
                reasons.append(f"{name}: output price is null")
    if not caps.missing():
        if sends_per_request > caps.max_llm_sends:
            reasons.append("one worst-case request alone exceeds --max-llm-sends")
        if sends_per_request * worst_single_input > caps.max_llm_input_tokens:
            reasons.append("one worst-case request alone exceeds --max-llm-input-tokens")
        llm_names = [n for r in ("workflow", "judge") for n in models.get(r, [])]
        costs = [
            sends_per_request
            * (
                worst_single_input * (price(table.get(n), "input") or 0.0)
                + output_bound * (price(table.get(n), "output") or 0.0)
            )
            / 1e6
            for n in llm_names
            if n in table
        ]
        if costs and max(costs) > caps.max_cost_usd:
            reasons.append("one worst-case request alone exceeds --max-cost-usd")
    if reasons:
        raise LiveRefused(reasons)


# --------------------------------------------------------------------------- #
# Client preflight (after construction, before any send)
# --------------------------------------------------------------------------- #
_USAGE_REPORTING_CLASSES = {"ChatGoogleGenerativeAI", "_GuardedGemini", "ChatGroq", "_GuardedGroq"}


def iter_chat_models(runnable) -> list:
    """Unwrap fallbacks / bindings to the concrete chat-model clients."""
    out, stack = [], [runnable]
    while stack:
        r = stack.pop()
        if r is None:
            continue
        if hasattr(r, "runnable") and hasattr(r, "fallbacks"):
            stack.append(r.runnable)
            stack.extend(r.fallbacks)
        elif hasattr(r, "bound"):
            stack.append(r.bound)
        else:
            out.append(r)
    return out


def check_clients(clients: list, *, sdk_max_retries: int, output_bound: int) -> None:
    """Every constructed client must report usage, have the pinned retry count and an
    output limit no larger than the budgeted bound."""
    reasons = []
    for c in clients:
        name = type(c).__name__
        if not any(cls.__name__ in _USAGE_REPORTING_CLASSES for cls in type(c).__mro__):
            reasons.append(f"{name}: cannot show the client returns usage metadata")
        retries = getattr(c, "max_retries", None)
        if not isinstance(retries, int) or retries != sdk_max_retries:
            reasons.append(f"{name}: max_retries {retries!r} != budgeted {sdk_max_retries}")
        limit = getattr(c, "max_output_tokens", None) or getattr(c, "max_tokens", None)
        if not isinstance(limit, int) or limit <= 0 or limit > output_bound:
            reasons.append(f"{name}: no enforced output limit <= {output_bound}")
    if reasons:
        raise LiveRefused(reasons)


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
@dataclass
class RoleUsage:
    calls: int = 0  # logical requests
    sends: int = 0  # worst-case sends reserved
    input_tokens: int = 0  # reserved (bound x sends)
    output_tokens: int = 0  # reserved
    cost_usd: float = 0.0  # reserved
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0


@dataclass
class Reservation:
    role: str
    model: str
    input_bound: int
    output_bound: int


@dataclass
class BudgetLedger:
    caps: Caps
    pricing: dict
    llm_sends_per_request: int
    embedding_sends_per_request: int
    output_bound: int
    usage: dict = field(default_factory=lambda: {r: RoleUsage() for r in ROLES})
    stop: BaseException | None = None  # latched first stop
    # Final budget review: ONE re-entrant lock guards every read-check-update of the
    # shared counters, the total cost and the stop latch, so concurrent LLM and
    # embedding reservations cannot both pass a cap that only one of them fits.
    # Re-entrant because ``reserve_*`` call ``_check_open`` / ``_halt`` /
    # ``total_cost`` while holding it. The ledger never takes any other lock, so a
    # caller holding its own lock (``LLMBudgetCallback._lock``) cannot deadlock.
    _lock: Any = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    @property
    def total_cost(self) -> float:
        with self._lock:
            return sum(u.cost_usd for u in self.usage.values())

    def _entry(self, model: str) -> dict:
        entry = self.pricing.get("models", {}).get(model) if model else None
        if entry is None:
            raise UndeclaredModel(
                f"model {model!r} is not declared in pricing.json; stopped before sending"
            )
        return entry

    def _halt(self, exc: BaseException) -> BaseException:
        with self._lock:
            if self.stop is None:
                self.stop = exc
        return exc

    def _check_open(self) -> None:
        with self._lock:
            if self.stop is not None:
                raise BudgetExceeded(f"run already stopped ({type(self.stop).__name__})")

    def reserve_llm(self, role: str, model: str, input_bound: int) -> Reservation:
        with self._lock:  # check-and-update is one atomic transaction
            return self._reserve_llm_locked(role, model, input_bound)

    def _reserve_llm_locked(self, role: str, model: str, input_bound: int) -> Reservation:
        self._check_open()
        try:
            entry = self._entry(model)
        except UndeclaredModel as exc:
            raise self._halt(exc) from None
        n = self.llm_sends_per_request
        add_in, add_out = n * input_bound, n * self.output_bound
        add_cost = (
            add_in * (price(entry, "input") or 0.0) + add_out * (price(entry, "output") or 0.0)
        ) / 1e6
        llm = [self.usage["workflow"], self.usage["judge"]]
        sends = sum(u.sends for u in llm) + n
        tokens = sum(u.input_tokens for u in llm) + add_in
        problems = []
        if sends > self.caps.max_llm_sends:
            problems.append(f"llm sends {sends} > {self.caps.max_llm_sends}")
        if tokens > self.caps.max_llm_input_tokens:
            problems.append(f"llm input tokens {tokens} > {self.caps.max_llm_input_tokens}")
        if self.total_cost + add_cost > self.caps.max_cost_usd:
            problems.append(
                f"total cost {self.total_cost + add_cost:.6f} > {self.caps.max_cost_usd}"
            )
        if (
            role == "judge"
            and self.caps.max_judge_sends is not None
            and self.usage["judge"].sends + n > self.caps.max_judge_sends
        ):
            problems.append("judge sends cap")
        if problems:
            raise self._halt(BudgetExceeded("; ".join(problems) + " - stopped before sending"))
        u = self.usage[role]
        u.calls += 1
        u.sends += n
        u.input_tokens += add_in
        u.output_tokens += add_out
        u.cost_usd += add_cost
        return Reservation(role, model, input_bound, self.output_bound)

    def reserve_embedding(self, model: str, texts: list[str]) -> None:
        with self._lock:  # check-and-update is one atomic transaction
            self._reserve_embedding_locked(model, texts)

    def _reserve_embedding_locked(self, model: str, texts: list[str]) -> None:
        self._check_open()
        try:
            entry = self._entry(model)
        except UndeclaredModel as exc:
            raise self._halt(exc) from None
        n = self.embedding_sends_per_request
        add_req = n * len(texts)
        add_tok = n * sum(utf8_bound_text(t) for t in texts)
        add_cost = add_tok * (price(entry, "input") or 0.0) / 1e6
        u = self.usage["embedding"]
        problems = []
        if u.sends + add_req > self.caps.max_embedding_requests:
            problems.append(f"embedding requests {u.sends + add_req} > cap")
        if u.input_tokens + add_tok > self.caps.max_embedding_tokens:
            problems.append(f"embedding tokens {u.input_tokens + add_tok} > cap")
        if self.total_cost + add_cost > self.caps.max_cost_usd:
            problems.append("total cost cap")
        if problems:
            raise self._halt(BudgetExceeded("; ".join(problems) + " - stopped before sending"))
        u.calls += len(texts)
        u.sends += add_req
        u.input_tokens += add_tok
        u.cost_usd += add_cost

    def record_llm_usage(self, res: Reservation, usage: dict | None) -> None:
        """Validate provider-reported usage of ONE attempt against its reservation.

        Detection, the actual-usage counters, the bound comparison and the stop latch
        are one ledger transaction (``_halt`` re-enters the RLock), so no reservation
        can pass between detecting invalid usage and latching the stop."""
        with self._lock:
            if not usage or "input_tokens" not in usage or "output_tokens" not in usage:
                raise self._halt(
                    BoundViolation(f"{res.model}: no usage metadata - bound unverifiable")
                )
            actual_in = int(usage.get("input_tokens") or 0)
            total = int(usage.get("total_tokens") or 0)
            actual_out = max(int(usage.get("output_tokens") or 0), total - actual_in)
            u = self.usage[res.role]
            u.actual_input_tokens += actual_in
            u.actual_output_tokens += actual_out
            if actual_in > res.input_bound or actual_out > res.output_bound:
                raise self._halt(
                    BoundViolation(
                        f"{res.model}: usage in={actual_in}/out={actual_out} exceeds reserved "
                        f"bound in={res.input_bound}/out={res.output_bound}"
                    )
                )

    def summary(self) -> dict:
        with self._lock:  # one consistent snapshot
            return {
                "by_role": {r: vars(u).copy() for r, u in self.usage.items()},
                "total_reserved_cost_usd": round(self.total_cost, 6),
                "stopped": (
                    None if self.stop is None else f"{type(self.stop).__name__}: {self.stop}"
                ),
                "assumptions": BOUND_ASSUMPTIONS,
            }


def _usage_from_result(response) -> dict | None:
    with_usage = None
    for gens in getattr(response, "generations", None) or []:
        for g in gens:
            msg = getattr(g, "message", None)
            meta = getattr(msg, "usage_metadata", None)
            if meta:
                with_usage = dict(meta)
    if with_usage:
        return with_usage
    tu = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    if tu:
        return {
            "input_tokens": tu.get("prompt_tokens"),
            "output_tokens": tu.get("completion_tokens"),
            "total_tokens": tu.get("total_tokens"),
        }
    return None


class LLMBudgetCallback(BaseCallbackHandler):
    """Pre-send reservation + post-call usage validation for every chat model."""

    raise_error = True
    # Round-3 review SF-2: run inline on async paths too, so a reservation is made
    # on the event-loop thread that then sends (``astream`` passes no run manager).
    run_inline = True

    def __init__(self, ledger: BudgetLedger):
        self.ledger = ledger
        self._open: dict = {}
        # Reserved runs not yet claimed by a send: run_id -> thread that reserved
        # it (final review S-2, round-3 N-2). ``BaseChatModel.stream`` / ``astream``
        # call ``_stream`` / ``_astream`` WITHOUT a run manager, so such a send
        # claims the latest unclaimed reservation made on its own thread. One
        # lock-protected map, so a claim from ANY thread removes the entry for all.
        self._unclaimed: dict = {}
        # Runs already claimed by an unmanaged send (round-4 N-C): a later check by
        # run id for the same run is a SECOND send and is refused.
        self._thread_claimed: set = set()
        # Managed sends (final budget review): run_id -> number of send scopes in
        # progress for that run. Nested checks of ONE send (``_agenerate`` ->
        # ``_generate``, ``_generate`` -> ``_stream``) happen while the outer scope is
        # open and are allowed; once the last scope exits the run is ``_sent`` and a
        # later, independent send by the same run id is refused.
        self._inflight: dict = {}
        self._sent: set = set()
        # Guards this callback's maps only. Shared budget state is protected by
        # ``BudgetLedger._lock``; lock order is always callback -> ledger.
        self._lock = threading.Lock()

    def _forget(self, run_id) -> None:
        with self._lock:
            self._unclaimed.pop(run_id, None)

    def _close(self, run_id):
        """Drop every trace of ``run_id``; return its reservation (or None)."""
        with self._lock:
            self._unclaimed.pop(run_id, None)
            self._thread_claimed.discard(run_id)
            self._inflight.pop(run_id, None)
            self._sent.discard(run_id)
            return self._open.pop(run_id, None)

    @staticmethod
    def _model(kwargs: dict, serialized: dict | None) -> str:
        meta = kwargs.get("metadata") or {}
        params = kwargs.get("invocation_params") or {}
        ser = (serialized or {}).get("kwargs") or {}
        return normalize_model(
            meta.get("ls_model_name")
            or params.get("model")
            or params.get("model_name")
            or ser.get("model")
            or ser.get("model_name")
        )

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        tags = kwargs.get("tags") or []
        role = "judge" if JUDGE_TAG in tags else "workflow"
        params = kwargs.get("invocation_params") or {}
        extra = {k: params[k] for k in ("tools", "functions", "response_format") if params.get(k)}
        bound = sum(utf8_bound_messages(batch) for batch in messages) + (
            utf8_bound_messages([], extra) - REQUEST_OVERHEAD if extra else 0
        )
        model = self._model(kwargs, serialized)
        # Check-reserve-register is one step: a run seen twice (duplicate handler,
        # even concurrently) is charged once. The ledger takes its own lock inside.
        with self._lock:
            if run_id in self._open:
                return
            self._open[run_id] = self.ledger.reserve_llm(role, model, bound)
            self._unclaimed[run_id] = threading.get_ident()

    def on_llm_end(self, response, *, run_id, **kwargs):
        res = self._close(run_id)
        if res is not None:
            self.ledger.record_llm_usage(res, _usage_from_result(response))

    def require_reservation(self, run_manager) -> _SendScope:
        """Send guard (review S1): a model may only send inside a run this callback
        reserved. Called from the patched client send methods in ``live_isolation``,
        which hold the returned scope open for the duration of the send.

        * With a run manager (``invoke`` / ``ainvoke``): its ``run_id`` must be an
          open reservation that has not already been sent. Nested checks of the
          same send while its scope is open (e.g. ``_agenerate`` -> ``_generate``)
          are fine; once the outermost scope exits, another send by that run id is
          an independent second send and is refused.
        * Without one (``stream`` / ``astream`` pass none): the send claims the
          latest UNCLAIMED reservation made on this thread; each reservation can be
          claimed once, from any thread.

        Anything else - a model built without the callback - is refused (latched).
        """
        run_id = getattr(run_manager, "run_id", None)
        if run_id is not None:
            with self._lock:  # one atomic check-and-claim (round-5 N-3)
                if (
                    run_id in self._open
                    and run_id not in self._thread_claimed
                    and run_id not in self._sent
                ):
                    self._unclaimed.pop(run_id, None)
                    self._inflight[run_id] = self._inflight.get(run_id, 0) + 1
                    return _SendScope(self, run_id)
        else:
            me = threading.get_ident()
            with self._lock:
                mine = [rid for rid, tid in self._unclaimed.items() if tid == me]
                for rid in reversed(mine):
                    del self._unclaimed[rid]
                    if rid in self._open:
                        self._thread_claimed.add(rid)
                        return _SendScope(self, None)
        raise self.ledger._halt(
            BudgetExceeded("model send without a budget reservation - refused before sending")
        )

    def _end_send(self, run_id) -> None:
        with self._lock:
            left = self._inflight.get(run_id, 0) - 1
            if left > 0:
                self._inflight[run_id] = left
            elif run_id in self._inflight:
                del self._inflight[run_id]
                if run_id in self._open:  # still open: a further send would be a second one
                    self._sent.add(run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._close(run_id)  # the reservation stays charged (conservative)


class _SendScope:
    """Returned by ``LLMBudgetCallback.require_reservation``; exiting it marks the end
    of one managed send. Unmanaged claims are already single-use (no-op exit).
    Exiting twice is harmless."""

    __slots__ = ("_cb", "_run_id")

    def __init__(self, cb: LLMBudgetCallback, run_id):
        self._cb, self._run_id = cb, run_id

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        run_id, self._run_id = self._run_id, None
        if run_id is not None:
            self._cb._end_send(run_id)
        return False


class BudgetedEmbeddingsGuard:
    """Wraps the RAW provider embeddings; every call is reserved before it is sent."""

    def __init__(self, inner, ledger: BudgetLedger, model: str):
        self._inner = inner
        self.ledger = ledger
        self.model = normalize_model(model)

    def embed_documents(self, texts, **kwargs):
        texts = list(texts)
        self.ledger.reserve_embedding(self.model, texts)
        return self._inner.embed_documents(texts, **kwargs)

    def embed_query(self, text, **kwargs):
        self.ledger.reserve_embedding(self.model, [text])
        return self._inner.embed_query(text, **kwargs)


# --------------------------------------------------------------------------- #
# Dry-run estimate (request-free; built from the offline scripted pass)
# --------------------------------------------------------------------------- #
def estimate(
    *,
    pricing: dict,
    models: dict[str, list[str]],
    workflow_prompt_bounds: list[int],
    worst_case_workflow_calls: int,
    judge_prompt_bounds: list[int],
    embedding_texts: list[str],
    uncached_texts: list[str],
    sdk_max_retries: int,
    chain_length: int,
    output_bound: int,
) -> dict:
    """Request-free estimate by role and model (all figures EXPERIMENTAL/UNVALIDATED)."""
    sends = (1 + sdk_max_retries) * chain_length
    table = pricing.get("models", {})
    # Extra worst-case calls (tool rounds the scripted pass did not make) are
    # charged at the LARGEST observed prompt: tool-loop prompts only grow.
    largest = max(workflow_prompt_bounds, default=0)
    extra_calls = max(0, worst_case_workflow_calls - len(workflow_prompt_bounds))
    wf_in = sum(workflow_prompt_bounds) + extra_calls * largest
    roles = {
        "workflow": {
            "calls": worst_case_workflow_calls,
            "worst_case_sends": worst_case_workflow_calls * sends,
            "input_token_bound": wf_in * sends,
            "output_token_bound": worst_case_workflow_calls * sends * output_bound,
        },
        "judge": {
            "calls": len(judge_prompt_bounds),
            "worst_case_sends": len(judge_prompt_bounds) * (1 + sdk_max_retries),
            "input_token_bound": sum(judge_prompt_bounds) * (1 + sdk_max_retries),
            "output_token_bound": len(judge_prompt_bounds) * (1 + sdk_max_retries) * output_bound,
        },
    }
    emb_tokens = sum(utf8_bound_text(t) for t in uncached_texts) * (1 + sdk_max_retries)
    roles["embedding"] = {
        "texts": len(embedding_texts),
        "cache_hits": len(embedding_texts) - len(uncached_texts),
        "requests": len(uncached_texts) * (1 + sdk_max_retries),
        "token_bound": emb_tokens,
    }

    def _cost(name, tin, tout):
        entry = table.get(name)
        pin, pout = price(entry, "input"), price(entry, "output")
        if pin is None or (tout and pout is None):
            return None
        return round((tin * pin + tout * (pout or 0.0)) / 1e6, 6)

    by_model = {}
    # Worst case per role: the most expensive model in that role carries all of it.
    for role, names in models.items():
        r = roles[role]
        tin = r.get("input_token_bound", r.get("token_bound", 0))
        tout = r.get("output_token_bound", 0)
        for name in names:
            by_model[f"{role}:{name}"] = _cost(name, tin, tout)
    total = None
    if all(v is not None for v in by_model.values()):
        total = round(
            sum(
                max((by_model[f"{role}:{n}"] for n in names), default=0.0)
                for role, names in models.items()
            ),
            6,
        )
    return {
        "models": models,
        "worst_case_sends_per_request": sends,
        "roles": roles,
        "cost_by_model_usd": {k: ("UNKNOWN" if v is None else v) for k, v in by_model.items()},
        "total_cost_upper_estimate_usd": "UNKNOWN" if total is None else total,
        "bound_status": {
            n: ("validated" if bound_is_validated(table.get(n)) else "unvalidated")
            for names in models.values()
            for n in names
        },
        "assumptions": BOUND_ASSUMPTIONS,
    }
