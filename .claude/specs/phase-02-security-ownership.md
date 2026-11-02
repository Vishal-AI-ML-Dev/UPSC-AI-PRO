# Phase 02 — Security: ownership & abuse limits

Status: STUB  ·  Related audit sections: M (findings 1–8), K

## Goal
Enforce object-level authorization at every vector boundary and close the confirmed abuse gaps, without
breaking PDF chat, the PYQ bank or the AI Mentor.

## Candidate scope
- Uploaded-document ownership: per-user upload records/keys; `/upload/chat` checks the caller owns `pdf_hash`;
  remove cross-user `_processed_cache` reuse (`src/agents/upload/graph.py`).
- `knowledge_base_search` tool (`src/graph/tools.py:knowledge_base_tool_fn`): the model may not choose an
  arbitrary `persist_key` — restrict to an allow-list derived server-side from the authenticated user.
- Implement the configured auth-endpoint rate limit (`auth_rate_limit_*` in `src/core/config.py`, currently unused).
- `max_length` on free-text inputs (`src/schemas.py`: chat question, transcript, chat_history;
  `AgentChatRequest` in `src/api/routes/chat.py`).
- Upload size enforced while reading the body, not only via `Content-Length` (`src/api/upload_limit.py`);
  content-type sniffing.
- No raw exception text in `?sync=true` error responses (upload, lecture, pyq) or job errors.
- Stop logging emails / query text (`src/core/users.py`, `src/api/routes/auth.py`, `src/core/observability.py`).

## Candidate acceptance criteria
- [ ] Tests prove user B cannot read user A's upload or PYQ bank via route or mentor tool.
- [ ] Auth endpoints return 429 after the configured limit.
- [ ] Oversized inputs return 413/422.
- [ ] All 8 workflows still pass smoke checks.

## Open questions
- Is a migration needed for an uploads-ownership table? (ADR + user-run migration)
