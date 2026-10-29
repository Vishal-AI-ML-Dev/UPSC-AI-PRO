import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  ApiError,
  AUTH_NOTICE_KEY,
  api,
  clearTokens,
  getToken,
  onSessionInvalid,
  sameSession,
  tokenClaims,
  TOKEN_STORAGE_KEY,
  type CurrentUser,
  type RegisterResult,
  type SessionEndReason,
} from "./api"
import { beginSession, endSession, sessionOwner, sessionSid } from "./session"

/** Stable account key used to namespace local state for this user. */
export function accountKey(user: CurrentUser): string {
  return String(user.id ?? user.sub ?? user.email ?? "")
}

type SignOutReason = SessionEndReason | "other_tab"

const NOTICES: Record<SignOutReason, string> = {
  session_superseded:
    "You were signed out because your account signed in on another device or browser.",
  session_ended: "Your session has ended. Please sign in again.",
  session_outdated: "Please sign in again to continue.",
  session_expired: "Your session expired. Please sign in again.",
  other_tab: "You signed out in another tab.",
}

// Non-secret hint so other tabs can show the precise sign-out reason. It is
// written by api.ts before the tokens are removed (see sessionInvalidated).
const NOTICE_KEY = AUTH_NOTICE_KEY

interface AuthState {
  user: CurrentUser | null
  loading: boolean
  /** Why the last session ended (shown on the sign-in page), if any. */
  notice: string | null
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, name: string) => Promise<RegisterResult>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // Every transition into an account session goes through here: switch the
  // local-state namespace / session (aborting the previous session's work)
  // before rendering it. The sid comes from the server's /me response.
  function adopt(me: CurrentUser) {
    const key = accountKey(me)
    if (!key) throw new Error("Signed-in user has no id")
    beginSession(key, String(me.sid ?? tokenClaims().sid ?? ""))
    queryClient.clear()
    setUser(me)
  }

  function signOutLocally(reason: SignOutReason | null) {
    endSession()
    queryClient.clear()
    setUser(null)
    setNotice(reason ? NOTICES[reason] : null)
  }

  // /me for the session `token` belongs to; null if a different session replaced
  // it meanwhile (its result must not overwrite the newer login).
  async function meFor(token: string): Promise<CurrentUser | null> {
    const me = await api.me()
    return sameSession(token, getToken()) ? me : null
  }

  useEffect(() => {
    let active = true
    async function boot() {
      const token = getToken()
      if (!token) {
        endSession() // purge any account data left by an expired session
        setLoading(false)
        return
      }
      try {
        const me = await meFor(token)
        if (active && me) adopt(me)
      } catch (e) {
        // A definitive 401 already cleared the session centrally. Keep tokens on
        // transient failures (503 / network) so a temporary outage signs no one out.
        if (e instanceof ApiError && e.status === 401 && sameSession(token, getToken()))
          clearTokens()
        endSession()
      } finally {
        if (active) setLoading(false)
      }
    }
    void boot()
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Server rejected the current session (superseded / ended / outdated / expired).
  useEffect(
    () => onSessionInvalid((reason) => signOutLocally(reason)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  // Same-origin tabs share one session through localStorage. React to changes made
  // by OTHER tabs (storage events never fire in the tab that made the change).
  // Handlers only read storage and update this tab's UI: they never call the
  // server's logout or write tokens, so no tab can trigger a sync loop.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.storageArea !== window.localStorage) return
      if (e.key !== null && e.key !== TOKEN_STORAGE_KEY) return
      const token = getToken()
      if (!token) {
        if (sessionOwner() === null) return
        let reason: SignOutReason = "other_tab"
        try {
          const r = localStorage.getItem(NOTICE_KEY) as SignOutReason | null
          if (r && r in NOTICES) reason = r
        } catch {
          /* ignore */
        }
        signOutLocally(reason)
        return
      }
      const c = tokenClaims(token)
      if (c.sub === sessionOwner() && c.sid === sessionSid()) return // same session (refresh)
      // A login in another tab (same or different account): adopt it, guarded.
      void meFor(token)
        .then((me) => {
          if (me) {
            setNotice(null)
            adopt(me)
          }
        })
        .catch(() => {
          /* session errors are handled centrally; transient errors: next event */
        })
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function clearNoticeHint() {
    try {
      localStorage.removeItem(NOTICE_KEY)
    } catch {
      /* ignore */
    }
  }

  async function login(email: string, password: string) {
    await api.login(email, password)
    const token = getToken()
    const me = await meFor(token)
    if (me) {
      clearNoticeHint()
      setNotice(null)
      adopt(me)
    }
  }

  async function register(email: string, password: string, name: string) {
    const res = await api.register(email, password, name)
    if (res.access_token) {
      const me = await meFor(getToken())
      if (me) {
        clearNoticeHint()
        setNotice(null)
        adopt(me)
      }
    }
    return res
  }

  async function logout() {
    // Revoke the refresh token first (needs it), then end the session locally:
    // abort in-flight work, drop cached queries and purge this account's data.
    // If another tab replaced the session meanwhile, its newer login is kept.
    if (!(await api.logout())) return
    clearNoticeHint()
    signOutLocally(null)
  }

  async function refreshUser() {
    const token = getToken()
    if (!token) return
    const me = await meFor(token)
    if (me) {
      clearNoticeHint()
      setNotice(null)
      adopt(me)
    }
  }

  const value: AuthState = {
    user,
    loading,
    notice,
    login,
    register,
    logout,
    refreshUser,
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
