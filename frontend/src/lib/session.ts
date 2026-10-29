// Account-session boundary for browser state.
//
// Everything a signed-in user produces locally (chats, notes, form inputs, the
// mentor thread id) is stored under a per-user localStorage namespace. Ending or
// switching a session aborts that session's in-flight requests/streams, bumps an
// epoch so late callbacks can detect they are stale, and purges account data that
// does not belong to the new user. Preferences such as the theme and the backend
// URL override live outside these namespaces and are never touched here.

const STATE_PREFIX = "upscai:u:"
// Pre-namespacing keys: their owner is unknown, so they are always purged.
const LEGACY_PREFIX = "upscai:state:"
const LEGACY_KEYS = ["mentor:threadId"]

let owner: string | null = null
// Server session id (JWT `sid`) of the current account session. A new login for
// the SAME user gets a new sid, which must also invalidate the old session's work.
let ownerSid: string | null = null
let epoch = 0
let controller = new AbortController()

function ownerPrefix(id: string): string {
  return STATE_PREFIX + encodeURIComponent(id) + ":"
}

function purge(keep: string | null): void {
  try {
    const keepPrefix = keep === null ? null : ownerPrefix(keep)
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k === null) continue
      const foreign =
        k.startsWith(STATE_PREFIX) && (keepPrefix === null || !k.startsWith(keepPrefix))
      if (foreign || k.startsWith(LEGACY_PREFIX) || LEGACY_KEYS.includes(k))
        doomed.push(k)
    }
    doomed.forEach((k) => localStorage.removeItem(k))
  } catch {
    // storage unavailable: nothing persisted to purge
  }
}

function rotate(): void {
  controller.abort()
  controller = new AbortController()
  epoch += 1
}

/** The signed-in user's id, or null when no account session is active. */
export function sessionOwner(): string | null {
  return owner
}

/** Changes whenever a session ends or switches; compare to detect stale work. */
export function sessionEpoch(): number {
  return epoch
}

/** Aborted when the current session ends or switches. */
export function sessionSignal(): AbortSignal {
  return controller.signal
}

/** Server session id of the current account session (null when signed out). */
export function sessionSid(): string | null {
  return ownerSid
}

/**
 * Start (or switch to) the session `sid` for `userId`. A different user purges the
 * previous account's local data; a new session for the same user keeps it but
 * still aborts and invalidates the old session's pending work.
 */
export function beginSession(userId: string, sid: string | null = null): void {
  if (owner === userId && ownerSid === sid) return
  const sameUser = owner === userId
  rotate()
  owner = userId
  ownerSid = sid
  if (!sameUser) purge(userId)
}

/** End the current session: abort its work and remove its local account data. */
export function endSession(): void {
  rotate()
  owner = null
  ownerSid = null
  purge(null)
}

/** localStorage key for `key` in `forOwner`'s namespace (null when signed out). */
export function scopedKey(key: string, forOwner: string | null = owner): string | null {
  return forOwner === null ? null : ownerPrefix(forOwner) + key
}

export function readScoped(key: string): string | null {
  const k = scopedKey(key)
  if (k === null) return null
  try {
    return localStorage.getItem(k)
  } catch {
    return null
  }
}

export function writeScoped(key: string, value: string): void {
  const k = scopedKey(key)
  if (k === null) return
  try {
    localStorage.setItem(k, value)
  } catch {
    // best-effort
  }
}
