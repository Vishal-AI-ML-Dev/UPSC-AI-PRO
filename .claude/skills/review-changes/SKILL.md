---
name: review-changes
description: Review the working-tree diff of the active UPSC-AI-PRO phase against its spec, plan and rules before the phase is closed. Use after implement-phase, or when the user asks for a phase review.
---

# Review phase changes

1. Collect the diff: `git status --porcelain` and `git diff` (plus new untracked files).
2. Scope check: every changed file must appear in the plan's **Files to touch** table. List any extras as
   BLOCKING.
3. Spec check: walk each acceptance criterion; mark MET / NOT MET / UNVERIFIED with evidence (file:line or
   test name).
4. Rules check, for the touched areas:
   - backend: no blocking calls in `async def`; reuse of `src/core` helpers; no raw exception text in responses;
     migration present for schema changes.
   - frontend: calls go through `frontend/src/lib/api.ts`; no new `dangerouslySetInnerHTML`; TS interfaces updated.
   - security: ownership enforced at DB and vector boundaries; inputs bounded; no secrets/PII in logs.
   - testing: regression tests exist and assert behaviour.
5. Workflow check: for each of the 8 workflows, state whether the diff touches it and how it stays working.
6. Delegate in parallel when relevant: `code-quality-reviewer` agent for quality, `security-reviewer` agent if
   auth, ownership, uploads, prompts or the vector store changed, `test-engineer` to run the suite.
7. Output a findings list ranked BLOCKING → SHOULD-FIX → NIT, each with file:line and a concrete fix.
   Do not fix anything yourself unless the user asks.
