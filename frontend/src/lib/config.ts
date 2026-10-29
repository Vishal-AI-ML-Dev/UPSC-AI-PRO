// Resolve the backend API base URL. Priority:
//   1. A saved override (set via the ⚙️ settings button) — handy for testing, but
//      honoured only when it points at this machine or at the configured API
//      origin, so a stale or foreign URL can never receive auth tokens. An
//      ignored override is left in storage untouched.
//   2. VITE_API_BASE from the build/env (required for production builds)
//   3. Development builds only: the local backend
// There is no remote fallback: a production build without VITE_API_BASE fails
// with a clear configuration error instead of calling an old deployment.
const DEV_DEFAULT_API = "http://127.0.0.1:8000/api/v1"
const LS_API = "upscai-api"
const LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

export class ApiConfigError extends Error {
  constructor() {
    super(
      "Backend API URL is not configured. Set VITE_API_BASE when building the frontend.",
    )
    this.name = "ApiConfigError"
  }
}

function configuredBase(): string {
  const env = (import.meta.env.VITE_API_BASE || "").trim().replace(/\/$/, "")
  if (env) return env
  return import.meta.env.DEV ? DEV_DEFAULT_API : ""
}

function isAllowedOverride(value: string, configured: string): boolean {
  if (value.startsWith("/") && !value.startsWith("//")) return true // same origin
  let u: URL
  try {
    u = new URL(value)
  } catch {
    return false
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false
  if (LOOPBACK_HOSTS.includes(u.hostname)) return true
  try {
    return configured !== "" && u.origin === new URL(configured).origin
  } catch {
    return false
  }
}

let warnedOverride = false

export function getApiBase(): string {
  const configured = configuredBase()
  let override: string | null = null
  try {
    override = localStorage.getItem(LS_API)
  } catch {
    override = null
  }
  if (override) {
    const v = override.replace(/\/$/, "")
    if (isAllowedOverride(v, configured)) return v
    if (!warnedOverride) {
      warnedOverride = true
      console.warn(
        "Ignoring saved backend URL override: it is neither this machine nor the configured API origin.",
      )
    }
  }
  if (!configured) throw new ApiConfigError()
  return configured
}

/** Saves an override if it is allowed; returns false (and saves nothing) otherwise. */
export function setApiBase(url: string): boolean {
  const v = url.trim().replace(/\/$/, "")
  if (!isAllowedOverride(v, configuredBase())) return false
  localStorage.setItem(LS_API, v)
  return true
}

/** For display only: the active base URL, or "" when it is not configured. */
export function describeApiBase(): string {
  try {
    return getApiBase()
  } catch {
    return ""
  }
}

export const OVERRIDE_RULE =
  "Only this machine (localhost / 127.0.0.1) or the configured API origin can be used."

export function healthUrl(): string {
  return getApiBase().replace(/\/api\/v1\/?$/, "") + "/health"
}
