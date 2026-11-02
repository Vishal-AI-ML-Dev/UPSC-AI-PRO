# Phase 03 — Request-path performance

Status: STUB  ·  Related audit sections: D, U#2

## Goal
No blocking work on the event loop, so one slow request cannot stall the API on a single worker.

## Candidate scope
- Convert `async def` handlers that do blocking I/O to `def` or offload them: `routes/upload.py` (sync branch),
  `routes/lecture.py` (sync branches), `routes/history.py`, `routes/auth.py` (`forgot_password`,
  `resend_verification`); verify `routes/feedback.py`, `routes/progress.py`, `routes/experiments.py`.
- Bound the checkpointed message history sent to the LLM per turn (`src/graph/tools.py:agent_node`).

## Candidate acceptance criteria
- [ ] No `async def` route performs sync DB/LLM/file/SMTP calls (static check documented in the report).
- [ ] Mentor prompt history is bounded; a test proves the cap.
