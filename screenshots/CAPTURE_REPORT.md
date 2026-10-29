# UPSC AI Pro — Screenshot Capture Report

**Tooling:** Playwright + system Chromium 127, viewport **1920×1080**, light + dark themes.
**Target:** the real production build (`frontend/dist`) served locally, with a same-origin stand-in API providing representative UPSC content through the app's real fetch / streaming / render paths.
**Per-page flow:** open route → wait for `#root` to mount (React hydration) → wait for network idle → wait for feature content + settle animations/charts → capture.

---

## Final output

### Number of screenshots captured
**60 / 60** — 30 feature states × 2 themes (light + dark). Every requested item was captured.

### Failed screenshots
**0** — no capture threw or produced a missing file.

### Missing routes
**0 missing**, but three requested items are not separate routes in this product (documented so nothing is silently remapped):
- **Register** is not a separate route — it is the "Create Account" tab on `/login` (captured as `register.png`).
- **Settings modal** and **Backend configuration** are the same dialog — the app's only settings modal is the **Backend URL** configuration modal (captured as `settings.png` and `backend-config.png`).
- **Theme switch** is a top-bar control, not a route (captured as `theme-switch.png`).

### Broken pages
**1 genuine bug found (dark theme only):**
- **`planner-plan-dark.png` — "Generated study plan" in dark mode hits the app's ErrorBoundary.**
  - **Error:** `TypeError: Cannot read properties of null (reading 'open')`
  - **Root cause:** `frontend/src/features/planner/Planner.tsx` (~line 246–252). Each plan section renders `<details open={printing || !collapsed[s.id]} onToggle={(e) => setCollapsed(c => ({ ...c, [s.id]: !(e.currentTarget as HTMLDetailsElement).open }))}>`. When the `toggle` event fires during mount, `e.currentTarget` can be `null`, so `.open` throws and the whole planner view is replaced by the error boundary.
  - **Reproducibility:** deterministic in dark mode across multiple runs, and also when reaching dark mode via the in-app theme toggle after the plan renders. The code is theme-agnostic, so this is a timing-sensitive latent bug that dark-mode timing exposes reliably.
  - **Suggested fix:** guard the handler, e.g. `onToggle={(e) => { const el = e.currentTarget as HTMLDetailsElement | null; if (!el) return; setCollapsed(c => ({ ...c, [s.id]: !el.open })) }}`.
  - **Note:** the light-theme `planner-plan.png` renders the full generated plan correctly.

### Console errors during capture
Only 3 shots produced console errors; all are expected or the bug above:
- `error-boundary.png` / `error-boundary-dark.png` — **intentional**. The dashboard was fed a deliberately malformed `progress.streak.activity = null` to trigger and document the real ErrorBoundary fallback.
- `planner-plan-dark.png` — the genuine dark-mode planner bug described under **Broken pages**.
- All other 57 shots captured with a **clean console** (external Google Fonts requests were stubbed locally since the sandbox is offline; they are not counted as page errors).

---

## Screenshot rules compliance
- ✅ 1920×1080 desktop resolution.
- ✅ Light + dark themes for every screen.
- ✅ Waited for network idle + content render before capture (except the two states where a transient state is *required*: `mentor-streaming` and `upload-processing`).
- ✅ No tokens/API keys on screen (auth tokens live only in `localStorage`); demo account uses a placeholder identity.
- ✅ Loading states captured only where explicitly required (streaming, processing).
- ✅ Fully-rendered pages otherwise.

## Notes on fidelity
The sandbox has no outbound network and cannot run the Python backend (LLMs, Supabase, Qdrant). Content shown is representative UPSC material served by a local stand-in API, rendered by the **real** production frontend build and its real code paths. This is disclosed rather than presented as live model output.
