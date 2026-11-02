---
name: write-tests
description: Write offline pytest tests for UPSC-AI-PRO that follow the repo's existing fixtures and mocking patterns. Use when adding tests for a fix, endpoint, security rule or helper.
---

# Write tests

1. Read `.claude/rules/testing.md` and the closest existing test file for the area
   (table in that rule file). Copy its structure; don't invent new fixtures if `client` / `make_user` fit.
2. Pick the level:
   - Pure helper → plain function test (e.g. `tests/test_rrf_fusion.py`).
   - Route / auth / ownership → `client` + `make_user` from `tests/conftest.py`.
   - LLM path → monkeypatch the model getter with a `RunnableLambda` returning `AIMessage(...)`
     (e.g. `tests/test_tool_agent_turns.py`); never call a real provider.
   - Vector store → monkeypatch `load_vector_store` / `similarity_search_with_sources` or use the embedded
     Qdrant pattern in `tests/test_qdrant_local.py`.
3. For each behaviour, cover: happy path, invalid input, unauthenticated (401), other user's object
   (404/403), and the specific regression.
4. Assert on observable behaviour (response body text, DB row, key used), not only status codes.
5. Name tests after the behaviour: `test_<subject>_<expected_behaviour>`.
6. Run the new file first (`uv run pytest -q tests/test_x.py`), then the full suite. Paste the summary lines.
7. A regression test should fail before the fix. If you can't demonstrate that, say so.
