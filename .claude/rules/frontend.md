# Frontend rules (`frontend/`)

## Structure
- Entry: `frontend/src/main.tsx` → `App.tsx` (public routes) → `pages/AppLayout.tsx` (lazy `/app/*` feature routes).
- One folder per feature in `frontend/src/features/<feature>/`; shared UI in `frontend/src/components/`;
  helpers in `frontend/src/lib/`.
- New feature pages are lazy-loaded with `lazyWithRetry` like the existing ones in `AppLayout.tsx`.

## API access
- All backend calls go through `frontend/src/lib/api.ts`:
  - JSON: `request()` (handles auth header, one refresh on 401, session-reason parsing).
  - Streaming text: `streamAgent(path, body, onChunk, signal)`.
  - Background jobs: `pollJob()`.
- Never call `fetch` directly from a component. Never hard-code the API base (use `lib/config.ts`).
- When a backend contract changes, update the TypeScript interface in `api.ts` in the same phase.

## Rendering & safety
- Render model output with `components/Markdown.tsx` (react-markdown + rehype-sanitize).
- Do not add new `dangerouslySetInnerHTML`. The two existing uses (`Lecture.tsx`, `Ncert.tsx`) rely on
  server-side `html.escape` in `src/core/study_aids.py`; keep that escaping if you touch it.
- Tokens currently live in `localStorage` (known risk, see `docs/PROJECT_STATE.md`); do not add more secrets
  to browser storage. Per-user UI state uses `lib/session.ts` namespacing.

## States
- Every async view handles loading, error and empty states (reuse `components/ui.tsx`, `Spinner`).
- Show backend error `detail` text as-is; do not invent client-side error copy for server failures.

## Style & checks
- TypeScript strict as configured in `tsconfig.json`; prettier config in `frontend/.prettierrc`
  (no semicolons, double quotes, width 88). The format hook runs prettier on edited `frontend/src` files.
- `cd frontend && npm run build` must pass before a phase that touched the frontend is closed.
- Do not edit `package.json` / `package-lock.json` without a spec + ADR (no `npm install` by Claude).
