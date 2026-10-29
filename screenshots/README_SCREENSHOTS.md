# UPSC AI Pro — Documentation Screenshots

Auto-generated with **Playwright** driving the **real production build** (`frontend/dist`) of UPSC AI Pro at **1920×1080**, in both **light** and **dark** themes.

Every screenshot below is the light-theme file. A dark-theme counterpart exists for each with a `-dark` suffix (e.g. `login.png` → `login-dark.png`).

> **How these were produced (transparency):** This sandbox has **no outbound network** and the Python backend (Gemini/Groq/Supabase/Qdrant) cannot run offline. To show fully-rendered, populated feature states, the real frontend was pointed at a **local stand-in API** that returns representative UPSC content through the app's *real* fetch / streaming / markdown-render code paths. The UI, routing, theming, streaming behaviour, and error handling are 100% the real product build — only the API *content* is representative rather than live LLM output. Auth tokens live only in `localStorage` and never appear on screen; the demo account shows a placeholder name/email (no real user data or API keys are rendered).

---

## Mapping: file → feature → description → README section

| Screenshot file | Feature | Description | README section |
|---|---|---|---|
| `login.png` | Authentication — Login | Sign-in tab of the auth card (email + password), theme toggle, links to Forgot password / Home. | Getting Started → Sign in |
| `register.png` | Authentication — Register | "Create Account" tab of the same auth card (name + email + password). | Getting Started → Create an account |
| `forgot-password.png` | Authentication — Forgot password | Password-reset request screen with email field and confirmation message. | Getting Started → Reset password |
| `dashboard.png` | Dashboard — Main | Study dashboard: streak, questions accuracy, revision due, topic mastery chart, 30-day activity. | Features → Dashboard |
| `sidebar-expanded.png` | Dashboard — Sidebar expanded | Full-width left navigation (Study Tools + My Space + Account) alongside the dashboard. | Features → Navigation |
| `sidebar-collapsed.png` | Dashboard — Sidebar collapsed | Icon-only collapsed navigation rail. | Features → Navigation |
| `mentor-empty.png` | AI Mentor — Empty state | First-run mentor screen with suggested prompts and empty composer. | Features → AI Mentor |
| `mentor-chat.png` | AI Mentor — Conversation | A completed Q&A on the doctrine of basic structure, rendered markdown answer + feedback controls. | Features → AI Mentor |
| `mentor-streaming.png` | AI Mentor — Streaming | Mid-stream assistant response (token-by-token streaming in progress). | Features → AI Mentor |
| `planner.png` | Planner — Input | Planner form: goal, daily hours, optional subject, weak areas, attempt count. | Features → Study Planner |
| `planner-plan.png` | Planner — Generated plan | Structured, collapsible study plan with interactive checklist and progress bar. | Features → Study Planner |
| `ncert.png` | NCERT — Search screen | Class / subject / chapter selectors for grounded NCERT study. | Features → NCERT Library |
| `ncert-answer.png` | NCERT — Generated answer | Generated NCERT notes, mind-map, practice questions and a RAG answer. | Features → NCERT Library |
| `pyq-generator.png` | PYQ — Question generation | Topic / mode selectors to generate previous-year-style questions. | Features → PYQ Practice |
| `pyq-answer.png` | PYQ — Answer generation | Generated MCQs with correct answers and explanations. | Features → PYQ Practice |
| `evaluator.png` | Evaluator — Input | Question + answer input for AI answer evaluation. | Features → Answer Evaluator |
| `evaluator-output.png` | Evaluator — Output | Structured evaluation: score, what went well, gaps, improvements. | Features → Answer Evaluator |
| `upload-rag.png` | Upload RAG — Upload page | PDF upload / drag-and-drop screen for personal-document RAG. | Features → Notes & Uploads |
| `upload-processing.png` | Upload RAG — Processing | Spinner state while an uploaded PDF is being read and summarised. | Features → Notes & Uploads |
| `upload-results.png` | Upload RAG — Results | Generated study notes from the uploaded document + chat over it. | Features → Notes & Uploads |
| `current-affairs.png` | Current Affairs — Daily | Daily current-affairs digest (Polity, Economy, Environment) with prelims pointers. | Features → Current Affairs |
| `current-affairs-article.png` | Current Affairs — Article detail | Editorial / article analysis view (context, arguments, way forward). | Features → Current Affairs |
| `history.png` | History — Conversation history | List of previous sessions across all agents with timestamps. | Features → History |
| `history-restore.png` | History — Session restore | A restored previous session showing its full message thread. | Features → History |
| `settings.png` | Settings — Settings modal | The settings dialog (Backend URL configuration modal). | Configuration → Settings |
| `backend-config.png` | Settings — Backend configuration | Backend API URL configuration modal (admin). | Configuration → Backend URL |
| `theme-switch.png` | Settings — Theme switch | Dashboard after toggling the top-bar light/dark theme control. | Configuration → Theme |
| `404.png` | Error States — 404 | Not-found page for unknown routes. | Troubleshooting → 404 |
| `offline.png` | Error States — Offline | Offline banner shown when connectivity drops. | Troubleshooting → Offline mode |
| `error-boundary.png` | Error States — Error boundary | App-level error boundary fallback ("Something went wrong"). | Troubleshooting → Error boundary |

### Dark theme
Each row above has a matching dark-theme file with the `-dark` suffix, e.g. `dashboard-dark.png`, `mentor-chat-dark.png`, `error-boundary-dark.png`.

> ⚠️ **Known issue captured honestly:** `planner-plan-dark.png` does **not** show the generated plan — in dark mode the "Generated study plan" view reliably trips the app's own error boundary. See `CAPTURE_REPORT.md` ("Broken pages") for the root cause. The light-theme `planner-plan.png` renders correctly.
