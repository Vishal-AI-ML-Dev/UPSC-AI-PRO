# Testing rules (`tests/`)

## Defaults
- Tests run offline: no API keys, no network, no real LLM. `tests/conftest.py` sets env vars before any
  `src.*` import and boots the real FastAPI app on a throwaway SQLite DB.
- Run: `uv run pytest -q` (all) or `uv run pytest -q tests/test_<name>.py -k <expr>` (focused).

## Reuse existing patterns
| Need | Pattern to copy |
|---|---|
| Real app + HTTP | `client` fixture (`tests/conftest.py`) |
| Verified user with auth headers | `make_user` fixture (`tests/conftest.py`) |
| Fake LLM | `RunnableLambda` returning `AIMessage` (e.g. `tests/test_mentor_routes.py`, `tests/test_tool_agent_turns.py`) |
| Patch module dependencies | `monkeypatch.setattr(module, "name", fake)` |
| Ownership / IDOR | `tests/test_security_ownership.py`, `tests/test_chat_isolation.py` |
| Streaming responses | `tests/test_stream_citations.py`, `tests/test_stream_guard.py` |
| Pure helpers | `tests/test_rrf_fusion.py`, `tests/test_grounding.py` |

## Requirements
- Every bug fix ships a regression test that fails before the fix.
- Every new endpoint: tests for unauthenticated (401), other user's resource (404/403), happy path, invalid input (422/400).
- Every security change: a test that demonstrates the attack is now refused.
- Assert on behaviour, not just status codes (e.g. check the returned text, the DB row, the vector key used).
- Do not test dead code as if it were live (e.g. `src/api/routes/mentor.py` is not registered).
- Tests needing external services must be opt-in (skip unless an env var is set) and never run in the default suite.
- Evals that need live keys live in `src/eval/` today (future: `evals/`), not in `tests/`.

## Evidence
- Never state that tests pass without running them in this session; paste the summary line
  (e.g. `574 passed, 6 skipped in 42s`) into the phase report.
- If a test fails for a reason outside the phase scope, record it in `docs/PROJECT_STATE.md` → Backlog;
  do not "fix" it by weakening assertions or adding skips.
