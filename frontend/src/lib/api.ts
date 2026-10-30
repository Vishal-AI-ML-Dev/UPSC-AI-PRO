import { ApiConfigError, describeApiBase, getApiBase } from "./config"
import { randomId } from "./randomId"
import { sessionEpoch, sessionSignal } from "./session"

const LS_TOKEN = "upscai-token"
const LS_REFRESH = "upscai-refresh"

export interface TokenPair {
  access_token: string
  refresh_token?: string
  token_type?: string
}

export interface CurrentUser {
  id?: string
  sub?: string
  email?: string
  name?: string
  [k: string]: unknown
}

export interface RegisterResult {
  verification_required?: boolean
  message?: string
  access_token?: string
  refresh_token?: string
}

export interface Conversation {
  id: string
  agent: string
  title: string
  created_at: string | null
  updated_at: string | null
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  created_at: string | null
}

export interface TopicMastery {
  topic: string
  score: number
  weak: boolean
  attempts: number
  last_tested: string | null
}

export interface ActivityPoint {
  date: string
  count: number
}

export interface ProgressOverview {
  streak: {
    current: number
    longest: number
    active_today: boolean
    activity: ActivityPoint[]
  }
  topics: TopicMastery[]
  weak_topics: string[]
  revision: { due: number; total: number }
  questions: { total: number; correct: number; accuracy: number }
  totals: { conversations: number; active_days: number; questions_asked: number }
}

export interface CostAgentRow {
  agent: string
  input_tokens: number
  output_tokens: number
  tokens: number
  calls: number
  cost_inr: number
}

export interface CostOverview {
  estimated: boolean
  currency: string
  totals: {
    cost_inr: number
    input_tokens: number
    output_tokens: number
    tokens: number
    calls: number
    avg_cost_per_call_inr: number
  }
  agents: CostAgentRow[]
  tier_mix: { lite: number; strong: number; lite_share: number }
  cache: {
    hit_exact: number
    hit_semantic: number
    miss: number
    skip: number
    hit_rate: number
    estimated_savings_inr: number
  }
  rates_inr_per_1k: Record<string, { input: number; output: number }>
}

export interface MonitoringEndpoint {
  method: string
  path: string
  count: number
  errors: number
  error_rate: number
  avg_ms: number
  max_ms: number
}
export interface MonitoringHourPoint {
  hour: string
  count: number
  errors: number
}
export interface MonitoringOverview {
  estimated: boolean
  uptime_seconds: number
  total_requests: number
  rps: number
  error_rate: number
  status_classes: Record<string, number>
  latency_ms: {
    count: number
    p50: number
    p95: number
    p99: number
    avg: number
    max: number
  }
  endpoints: MonitoringEndpoint[]
  hourly: MonitoringHourPoint[]
}

export interface FeedbackInput {
  rating: "up" | "down"
  agent?: string
  question?: string
  answer?: string
  comment?: string
}

export interface AnswerEvaluation {
  score: number | null
  max_score: number
  did_well: string[]
  missing: string[]
  improvements: string[]
}

export interface MainsEvaluation {
  score: number | null
  max_marks: number
  verdict: string | null
  strengths: string[]
  gaps: string[]
  improvements: string[]
}

export interface ExperimentVariantResult {
  variant: string
  up: number
  down: number
  total: number
  up_rate: number
  wilson_lower: number
  lift_vs_baseline: number | null
  enough_data: boolean
}
export interface ExperimentComparison {
  experiment: string
  baseline: string
  min_sample: number
  leader: string | null
  confident: boolean
  variants: ExperimentVariantResult[]
}
export interface ExperimentInfo {
  key: string
  description: string
  unit: string
  enabled: boolean
  variants: Array<{
    name: string
    weight: number
    has_directive: boolean
    directive: string
  }>
  results: ExperimentComparison
}
export interface FeedbackTally {
  up: number
  down: number
  total: number
  up_rate: number
}
export interface ExperimentsOverview {
  estimated: boolean
  experiments: ExperimentInfo[]
  feedback: {
    overall: FeedbackTally
    by_agent: Record<string, FeedbackTally>
    sample_size: number
  }
}

// ── Agent feature schemas (Planner / PYQ / NCERT / Current Affairs / Upload / Lecture) ──

export interface StudyPlanMeta {
  attempt_year: number
  months_left: number
  timeline_msg: string
  prelims_date: string | null
}
export interface PlannerResult {
  plan: string
  meta: StudyPlanMeta | null
}

export interface NcertList {
  items: string[]
  available?: boolean
  message?: string | null
}
export interface NcertSession {
  notes: string
  mindmap_html: string
  questions_html: string
  chapter_path: string
}

export interface CaMonthsResult {
  months: Array<[string, string]>
}

export interface PyqBankStatus {
  exists: boolean
}
export interface PyqBankUploadResult {
  success: boolean
  filename: string
  hash: string
  chunks: number
  approx_questions: number
}

export interface UploadResult {
  success: boolean
  filename: string
  hash: string
  book_info: Record<string, unknown>
  notes: string
}

export interface LectureResult {
  notes: string
  topic_info: Record<string, unknown>
  video_id: string
  mindmap_html: string
  questions_html: string
}

// Background jobs: heavy endpoints enqueue work and return a job id; the client
// polls GET /jobs/{id} until the job is done (or errors).
export type JobState = "queued" | "running" | "done" | "error"
export interface JobStatus<T = unknown> {
  job_id: string
  type: string
  status: JobState
  result: T | null
  error: string | null
  created_at: string | null
  updated_at: string | null
}
interface EnqueueResponse {
  job_id: string
  status: JobState
}

export function getToken(): string {
  return localStorage.getItem(LS_TOKEN) || ""
}
export function getRefreshToken(): string {
  return localStorage.getItem(LS_REFRESH) || ""
}
export function setTokens(t: TokenPair): void {
  if (t.access_token) localStorage.setItem(LS_TOKEN, t.access_token)
  if (t.refresh_token) localStorage.setItem(LS_REFRESH, t.refresh_token)
}
export function clearTokens(): void {
  localStorage.removeItem(LS_TOKEN)
  localStorage.removeItem(LS_REFRESH)
}

/** localStorage key of the access token (other tabs watch it via `storage` events). */
export const TOKEN_STORAGE_KEY = LS_TOKEN
/** Non-secret hint telling other tabs why the session ended. */
export const AUTH_NOTICE_KEY = "upscai-auth-notice"

export interface TokenClaims {
  sub?: string
  sid?: string
  email?: string
  name?: string
}

/** Decode (NOT verify) a JWT payload; the server remains the authority. */
export function tokenClaims(token: string = getToken()): TokenClaims {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")
    const bin = atob(part.padEnd(Math.ceil(part.length / 4) * 4, "="))
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
    return JSON.parse(new TextDecoder().decode(bytes)) as TokenClaims
  } catch {
    return {}
  }
}

/** True if both tokens belong to the same server session (same sid). */
export function sameSession(a: string, b: string): boolean {
  if (!a || !b) return false
  const sa = tokenClaims(a).sid
  return sa ? sa === tokenClaims(b).sid : a === b
}

// ---- Session rejection -----------------------------------------------------
// The server enforces one active session per account and answers 401 with a
// reason. Terminal reasons end the session (no refresh); "invalid" usually means
// an expired access token and may be refreshed.
export type SessionEndReason =
  "session_superseded" | "session_ended" | "session_outdated" | "session_expired"

const TERMINAL_REASONS: SessionEndReason[] = [
  "session_superseded",
  "session_ended",
  "session_outdated",
]

// The reason is sent in WWW-Authenticate, which browsers hide cross-origin unless
// the API exposes it; the 401 detail text (src/api/deps.py) is the fallback.
const DETAIL_REASONS: Record<string, SessionEndReason> = {
  "Your account signed in on another device or browser. Please sign in again.":
    "session_superseded",
  "Your session has ended. Please sign in again.": "session_ended",
  "Please sign in again to continue.": "session_outdated",
}

async function rejection(res: Response): Promise<{ code: string; detail: string }> {
  const m = /error_description="([^"]+)"/.exec(
    res.headers.get("WWW-Authenticate") || "",
  )
  const detail = await parseError(res)
  return { code: m?.[1] ?? DETAIL_REASONS[detail] ?? "invalid", detail }
}

function isTerminal(code: string): code is SessionEndReason {
  return (TERMINAL_REASONS as string[]).includes(code)
}

type InvalidListener = (reason: SessionEndReason) => void
const invalidListeners = new Set<InvalidListener>()

/** Subscribe to definitive session rejections (returns an unsubscribe function). */
export function onSessionInvalid(fn: InvalidListener): () => void {
  invalidListeners.add(fn)
  return () => invalidListeners.delete(fn)
}

// The server rejected the session that `rejectedToken` belongs to. Act only if
// that session is still the stored one: a delayed 401 or failed refresh from an
// old session must never erase a newer login (in this or another tab).
function sessionInvalidated(reason: SessionEndReason, rejectedToken: string): void {
  if (!sameSession(rejectedToken, getToken())) return
  // Write the (non-secret) reason hint BEFORE removing the tokens: other tabs
  // react to the token removal and must already see why the session ended.
  try {
    localStorage.setItem(AUTH_NOTICE_KEY, reason)
  } catch {
    /* best-effort */
  }
  clearTokens()
  invalidListeners.forEach((fn) => fn(reason))
}

function url(path: string): string {
  return getApiBase() + path
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = "ApiError"
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const d = (await res.json()) as { detail?: string }
    return d.detail || res.statusText
  } catch {
    return res.statusText || "Request failed"
  }
}

// Combine a caller's signal with the session signal, so ending/switching the
// account session aborts every request and stream it started.
function withSession(signal?: AbortSignal | null): AbortSignal {
  const s = sessionSignal()
  if (!signal) return s
  const c = new AbortController()
  if (s.aborted || signal.aborted) c.abort()
  else {
    s.addEventListener("abort", () => c.abort(), { once: true })
    signal.addEventListener("abort", () => c.abort(), { once: true })
  }
  return c.signal
}

// ---- Coordinated refresh ---------------------------------------------------
// One refresh at a time per tab (single flight) and, where the Web Locks API is
// available, across all tabs of this origin. After acquiring the lock the
// current tokens are re-read: if another tab already refreshed or a new login
// replaced them, the caller simply retries with what is stored now.
//
// Fallback without Web Locks: tabs may refresh concurrently and the loser gets
// "invalid" (its token was just rotated by the winner, whose response may still be
// in flight). Each tab therefore advertises an in-flight refresh with a per-tab
// timestamp marker (no credentials) that it removes only AFTER storing the new
// tokens. The loser waits only while another tab's fresh marker exists, polling
// for the refresh token to change - bounded by the marker TTL / deadline - and
// never sends a second refresh. With no other refresh in flight, "invalid" ends
// the session after one short re-check.
type RefreshOutcome =
  | { kind: "retry" } // tokens are fresh now: retry once
  | { kind: "terminal"; reason: SessionEndReason }
  | { kind: "transient" } // 503 / network: keep the session, report an error
  | { kind: "gone" } // signed out or switched meanwhile: nothing to do

const REFRESH_LOCK = "upscai-refresh"
const PENDING_PREFIX = "upscai-refresh-pending:"
const PENDING_TTL_MS = 30_000 // a marker older than this is from a dead tab
const FALLBACK_POLL_MS = 200
const FALLBACK_RECHECK_MS = 250
const TAB_ID = randomId()
let refreshing: Promise<RefreshOutcome> | null = null

function markRefreshPending(on: boolean): void {
  try {
    if (on) localStorage.setItem(PENDING_PREFIX + TAB_ID, String(Date.now()))
    else localStorage.removeItem(PENDING_PREFIX + TAB_ID)
  } catch {
    /* storage unavailable: fall back to the short re-check */
  }
}

function otherTabRefreshing(): boolean {
  try {
    const now = Date.now()
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (!k || !k.startsWith(PENDING_PREFIX) || k === PENDING_PREFIX + TAB_ID) continue
      const at = Number(localStorage.getItem(k))
      if (Number.isFinite(at) && now - at < PENDING_TTL_MS) return true
    }
  } catch {
    /* ignore */
  }
  return false
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function doRefresh(
  staleAccess: string,
  locked: boolean,
): Promise<RefreshOutcome> {
  const epoch = sessionEpoch()
  const current = getToken()
  if (!current) return { kind: "gone" }
  if (current !== staleAccess) return { kind: "retry" } // refreshed or replaced already
  const rt = getRefreshToken()
  if (!rt) return { kind: "gone" }
  let res: Response
  if (!locked) markRefreshPending(true)
  try {
    try {
      res = await fetch(url("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
        signal: sessionSignal(),
      })
    } catch {
      return epoch !== sessionEpoch() ? { kind: "gone" } : { kind: "transient" }
    }
    if (res.ok) {
      const pair = (await res.json()) as TokenPair
      // Never overwrite a newer login or revive tokens after logout/switch.
      if (epoch !== sessionEpoch() || getRefreshToken() !== rt) return { kind: "gone" }
      setTokens(pair)
      return { kind: "retry" }
    }
  } finally {
    if (!locked) markRefreshPending(false) // only after the new tokens are stored
  }
  if (res.status !== 401) return { kind: "transient" }
  const { code } = await rejection(res)
  if (isTerminal(code)) return { kind: "terminal", reason: code }
  // "invalid": this refresh token was already rotated. Another tab may have done it.
  if (getRefreshToken() !== rt) return { kind: "retry" }
  if (!locked) {
    const deadline = Date.now() + PENDING_TTL_MS
    await sleep(FALLBACK_RECHECK_MS)
    while (getRefreshToken() === rt && otherTabRefreshing() && Date.now() < deadline) {
      if (epoch !== sessionEpoch()) return { kind: "gone" }
      await sleep(FALLBACK_POLL_MS)
    }
    if (getRefreshToken() !== rt) return { kind: "retry" }
  }
  return { kind: "terminal", reason: "session_expired" }
}

function refreshAfter401(staleAccess: string): Promise<RefreshOutcome> {
  if (!refreshing) {
    const locks = "locks" in navigator ? navigator.locks : undefined
    // locks.request resolves with the callback's (awaited) result.
    const run: Promise<RefreshOutcome> = locks
      ? (locks.request(REFRESH_LOCK, () =>
          doRefresh(staleAccess, true),
        ) as unknown as Promise<RefreshOutcome>)
      : doRefresh(staleAccess, false)
    refreshing = run.finally(() => {
      refreshing = null
    })
  }
  return refreshing as Promise<RefreshOutcome>
}

/** Handle a 401 for a request sent with `sentToken`. Returns true to retry once. */
async function after401(
  res: Response,
  sentToken: string,
  canRetry: boolean,
): Promise<{ retry: boolean; detail: string; status?: number }> {
  const { code, detail } = await rejection(res)
  if (isTerminal(code)) {
    sessionInvalidated(code, sentToken)
    return { retry: false, detail }
  }
  if (!canRetry) return { retry: false, detail }
  const out = await refreshAfter401(sentToken)
  if (out.kind === "retry") return { retry: true, detail }
  if (out.kind === "terminal") sessionInvalidated(out.reason, sentToken)
  if (out.kind === "transient")
    return {
      retry: false,
      detail: "Could not renew your session right now. Please try again.",
      status: 503,
    }
  return { retry: false, detail }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const epoch = sessionEpoch()
  const sentToken = getToken()
  const res = await fetch(url(path), {
    ...init,
    headers: {
      ...(init.headers || {}),
      ...(sentToken ? { Authorization: "Bearer " + sentToken } : {}),
    },
    signal: withSession(init.signal),
  })
  // The session ended/switched while waiting: never hand a stale response back.
  if (epoch !== sessionEpoch())
    throw new ApiError(0, "Request cancelled: session ended.")
  if (res.status === 401 && sentToken) {
    const r = await after401(res, sentToken, retry)
    if (r.retry && epoch === sessionEpoch()) return request<T>(path, init, false)
    throw new ApiError(r.status ?? 401, r.detail)
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// Poll a background job to completion. onStatus fires on each status change so
// the UI can show "queued" vs "running". Resolves with the job result, or throws
// an ApiError if the job errors or exceeds the timeout.
async function pollJob<T>(
  jobId: string,
  onStatus?: (status: JobState) => void,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const intervalMs = opts.intervalMs ?? 2000
  const timeoutMs = opts.timeoutMs ?? 10 * 60 * 1000
  const started = Date.now()
  let last: JobState | null = null
  for (;;) {
    const job = await request<JobStatus<T>>("/jobs/" + encodeURIComponent(jobId))
    if (job.status !== last) {
      last = job.status
      onStatus?.(job.status)
    }
    if (job.status === "done") {
      if (job.result == null)
        throw new ApiError(500, "The job finished but returned no result.")
      return job.result
    }
    if (job.status === "error")
      throw new ApiError(500, job.error || "The background job failed. Please retry.")
    if (Date.now() - started > timeoutMs)
      throw new ApiError(504, "This is taking longer than expected. Please try again.")
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

export const api = {
  async login(email: string, password: string): Promise<TokenPair> {
    // Backend uses OAuth2PasswordRequestForm: form-encoded, field name 'username'.
    const body = new URLSearchParams()
    body.set("username", email)
    body.set("password", password)
    const res = await fetch(url("/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    })
    if (!res.ok) throw new ApiError(res.status, await parseError(res))
    const data = (await res.json()) as TokenPair
    setTokens(data)
    return data
  },

  async register(
    email: string,
    password: string,
    name: string,
  ): Promise<RegisterResult> {
    const data = await request<RegisterResult>("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    })
    if (data.access_token) setTokens(data as TokenPair)
    return data
  },

  me(): Promise<CurrentUser> {
    return request<CurrentUser>("/auth/me")
  },

  async verifyEmail(token: string): Promise<TokenPair & { message?: string }> {
    const data = await request<TokenPair & { message?: string }>("/auth/verify-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
    if (data.access_token) setTokens(data)
    return data
  },

  resendVerification(email: string): Promise<{ message: string }> {
    return request("/auth/resend-verification", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    })
  },

  forgotPassword(email: string): Promise<{ message: string }> {
    return request("/auth/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    })
  },

  resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
    return request("/auth/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: newPassword }),
    })
  },

  /** Logs out this session. Returns false if a newer login replaced it meanwhile
   * (e.g. in another tab), in which case the newer tokens are left untouched. */
  async logout(): Promise<boolean> {
    const at = getToken()
    const rt = getRefreshToken()
    if (rt) {
      try {
        await request("/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: rt }),
        })
      } catch {
        // best-effort; clear local tokens regardless
      }
    }
    if (getToken() && !sameSession(at, getToken())) return false
    clearTokens()
    return true
  },

  conversations(agent?: string): Promise<{ conversations: Conversation[] }> {
    const q = agent ? "?agent=" + encodeURIComponent(agent) : ""
    return request("/history/conversations" + q)
  },

  conversationMessages(id: string): Promise<{ messages: ChatMessage[] }> {
    return request("/history/conversations/" + encodeURIComponent(id) + "/messages")
  },

  progress(): Promise<ProgressOverview> {
    return request("/progress/overview")
  },

  // Admin-only cost dashboard. `costAccess` is auth-only (returns {admin})
  // so the UI can decide whether to show the tab without catching a 403.
  costAccess(): Promise<{ admin: boolean }> {
    return request("/cost/access")
  },

  cost(): Promise<CostOverview> {
    return request("/cost/overview")
  },

  // Admin-only operational monitoring dashboard (#18). `monitoringAccess` is
  // auth-only (returns {admin}) so the UI can hide the tab without a 403.
  monitoringAccess(): Promise<{ admin: boolean }> {
    return request("/monitoring/access")
  },

  monitoring(): Promise<MonitoringOverview> {
    return request("/monitoring/overview")
  },

  // Admin-only prompt A/B experiments dashboard (#12). `experimentsAccess` is
  // auth-only (returns {admin}) so the UI can hide the tab without a 403.
  experimentsAccess(): Promise<{ admin: boolean }> {
    return request("/experiments/access")
  },

  experiments(): Promise<ExperimentsOverview> {
    return request("/experiments/overview")
  },

  submitFeedback(input: FeedbackInput): Promise<{ id: string; rating: string }> {
    return request("/feedback/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  saveMessage(input: {
    role: "user" | "assistant"
    content: string
    agent?: string
    conversation_id?: string | null
    title?: string
  }): Promise<{ id: string; conversation_id: string }> {
    return request("/history/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  evaluateAnswer(input: {
    question: string
    answer: string
  }): Promise<{ response: string; structured: AnswerEvaluation }> {
    return request("/evaluator/evaluate/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  evaluateMains(input: {
    question: string
    answer: string
    marks?: number
    keywords?: string[]
    word_limit?: number
  }): Promise<{ response: string; structured: MainsEvaluation }> {
    return request("/evaluator/mains/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  modelAnswer(input: {
    question: string
    marks?: number
    keywords?: string[]
    word_limit?: number
  }): Promise<{ response: string }> {
    return request("/evaluator/model-answer/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  // ── Planner (study plan) ──
  plannerSync(input: {
    goal: string
    hours?: string
    optional?: string
    weak?: string
    attempt_number?: string
  }): Promise<PlannerResult> {
    return request("/planner/generate/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  // ── NCERT (class -> subject -> chapter study) ──
  ncertClasses(): Promise<NcertList> {
    return request<NcertList>("/ncert/classes")
  },
  ncertSubjects(className: string): Promise<NcertList> {
    return request<NcertList>("/ncert/subjects/" + encodeURIComponent(className))
  },
  ncertChapters(className: string, subject: string): Promise<NcertList> {
    return request<NcertList>(
      "/ncert/chapters/" +
        encodeURIComponent(className) +
        "/" +
        encodeURIComponent(subject),
    )
  },
  ncertStudy(input: {
    class_name: string
    subject: string
    chapter: string
  }): Promise<NcertSession> {
    return request<NcertSession>("/ncert/study", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  },

  // ── Current Affairs (daily / editorial / monthly) ──
  caTopics(): Promise<{ topics: string[] }> {
    return request("/current-affairs/topics")
  },
  caDates(): Promise<{ dates: string[] }> {
    return request("/current-affairs/dates")
  },
  caMonths(): Promise<CaMonthsResult> {
    return request<CaMonthsResult>("/current-affairs/months")
  },

  // ── PYQ (practice questions + personal bank) ──
  pyqTopics(questionType: string): Promise<{ topics: string[] }> {
    return request("/pyq/topics/" + encodeURIComponent(questionType))
  },
  pyqBankStatus(): Promise<PyqBankStatus> {
    return request<PyqBankStatus>("/pyq/bank/status")
  },
  async pyqBankUpload(
    file: File,
    onStatus?: (status: JobState) => void,
  ): Promise<PyqBankUploadResult> {
    const form = new FormData()
    form.append("file", file)
    const { job_id } = await request<EnqueueResponse>("/pyq/bank/upload", {
      method: "POST",
      body: form,
    })
    return pollJob<PyqBankUploadResult>(job_id, onStatus)
  },
  pyqBankClear(): Promise<Record<string, unknown>> {
    return request("/pyq/bank/clear", { method: "POST" })
  },

  // ── Upload (PDF study notes) — runs as a background job, then polls ──
  async uploadProcess(
    file: File,
    onStatus?: (status: JobState) => void,
  ): Promise<UploadResult> {
    const form = new FormData()
    form.append("file", file)
    const { job_id } = await request<EnqueueResponse>("/upload/process", {
      method: "POST",
      body: form,
    })
    return pollJob<UploadResult>(job_id, onStatus)
  },

  // ── Lecture (YouTube / pasted transcript) — runs as a background job, then polls ──
  async lectureProcess(
    input: { youtube_url: string; medium?: string },
    onStatus?: (status: JobState) => void,
  ): Promise<LectureResult> {
    const { job_id } = await request<EnqueueResponse>("/lecture/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
    return pollJob<LectureResult>(job_id, onStatus)
  },
  async lectureProcessText(
    input: { transcript: string; medium?: string },
    onStatus?: (status: JobState) => void,
  ): Promise<LectureResult> {
    const { job_id } = await request<EnqueueResponse>("/lecture/process-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
    return pollJob<LectureResult>(job_id, onStatus)
  },
}

export interface MentorMessage {
  role: "user" | "assistant"
  content: string
}

// Generic text/plain token streamer for the agent endpoints that stream their
// output (planner, pyq, ncert, current affairs, and the upload/lecture chats).
// Takes an arbitrary path + JSON body and streams the text/plain response.
// Ending/switching the account session aborts the stream; no chunk is delivered
// after that, so a late response cannot repopulate cleared state.
export async function streamAgent(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const sig = withSession(signal)
  const emit = (text: string) => {
    if (!sig.aborted) onChunk(text)
  }
  let res: Response | null = null
  for (let attempt = 0; attempt < 2; attempt++) {
    const sentToken = getToken()
    try {
      res = await fetch(url(path), {
        method: "POST",
        headers: {
          ...(sentToken ? { Authorization: "Bearer " + sentToken } : {}),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: sig,
      })
    } catch (e) {
      if (sig.aborted) return
      if (e instanceof ApiConfigError) emit("\n\n⚠️ " + e.message)
      else
        emit(
          "\n\n⚠️ Backend connection error. Is the API reachable at " +
            describeApiBase() +
            "?",
        )
      return
    }
    if (res.status !== 401 || !sentToken) break
    // Rejected before the stream started: refresh once if allowed, else stop.
    const r = await after401(res, sentToken, attempt === 0)
    if (sig.aborted) return
    if (!r.retry) {
      emit("\n\n⚠️ " + r.detail)
      return
    }
  }
  if (!res) return
  if (!res.ok || !res.body) {
    if (res.status === 401) emit("\n\n⚠️ Session expired. Please sign in again.")
    else emit("\n\n⚠️ Error " + res.status + ": " + (await parseError(res)))
    return
  }
  const reader = res.body.getReader()
  const dec = new TextDecoder("utf-8")
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done || sig.aborted) break
      const piece = dec.decode(value, { stream: true })
      if (piece) emit(piece)
    }
  } catch (e) {
    if (!sig.aborted) throw e
  }
}
