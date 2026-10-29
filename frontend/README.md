# UPSC AI Pro \u2014 Frontend (React + Vite + TypeScript + Tailwind)

Modern SPA — the single canonical frontend.
It talks to the same FastAPI backend (`/api/v1`), so nothing on the server
changes.

## What's in this foundation (Step 2, batch 1)

- \u2705 Vite + React 18 + TypeScript + Tailwind design system (dark theme)
- \u2705 Typed API client (`src/lib/api.ts`) \u2014 single base URL from env, Bearer
  auth, automatic refresh-token rotation on 401
- \u2705 Auth: sign in (OAuth2 form) + create account + email-verification aware
- \u2705 App shell with routing + nav tabs
- \u2705 **Mentor chat** with live token streaming + Markdown rendering
- \ud83d\udea7 Dashboard, History (placeholders \u2014 next batches)

## Run locally (Windows PowerShell)

```powershell
cd .\frontend

# 1) install deps (needs internet, one-time)
npm install

# 2) point the app at your backend
copy .env.example .env.local
# then edit .env.local:
#   local backend  -> VITE_API_BASE=http://localhost:8000/api/v1
#   Render (prod)  -> VITE_API_BASE=https://<your-backend>.onrender.com/api/v1

# 3) start the dev server
npm run dev
# open http://localhost:5173
```

Tip: you can also switch the backend URL at runtime from the \u2699\ufe0f button in the
top bar (stored in localStorage) \u2014 handy for testing local vs Render without a
rebuild.

## Build (optional \u2014 Vercel does this for you)

```powershell
npm run build     # outputs dist/
npm run preview   # serve the production build locally
```

## Backend CORS

Make sure the backend allows the frontend origin. For local dev that's
`http://localhost:5173`; for prod it's your Vercel domain. Set it via the
backend's CORS config / `FRONTEND_URL` env.

## Deploy on Vercel

- **Root Directory:** `frontend`
- **Framework preset:** Vite (build `npm run build`, output `dist`)
- **Environment variable:** `VITE_API_BASE = https://<your-backend>.onrender.com/api/v1`
- `vercel.json` already adds the SPA rewrite so refreshing a deep link works.

The old `upsc-frontend/` HTML has been removed; `frontend/` is now the only frontend and keeps
working until you switch Vercel's root directory to `frontend`.

## Structure

```
frontend/
\u251c\u2500 src/
\u2502  \u251c\u2500 lib/        api client, auth context, config
\u2502  \u251c\u2500 components/ Button/Card/Input/Spinner, Nav, Markdown
\u2502  \u251c\u2500 pages/      Login, AppLayout
\u2502  \u2514\u2500 features/   mentor/ (chat)  \u2014 dashboard/history/streaks next
\u2514\u2500 ...config
```
