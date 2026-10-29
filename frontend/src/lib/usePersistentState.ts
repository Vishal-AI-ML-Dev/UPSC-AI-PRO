import { useEffect, useState } from "react"
import { scopedKey, sessionOwner } from "./session"

// A drop-in replacement for useState that transparently persists the value to
// localStorage. This keeps feature content (chat messages, generated notes,
// form inputs, etc.) alive across tab switches and page refreshes, since React
// Router unmounts route components when you navigate away.
//
// Only persist meaningful content/inputs with this hook. Do NOT persist
// transient flags like `loading` or `streaming` — otherwise a refresh mid-run
// could leave the UI stuck in a loading state.
//
// Values live in the signed-in user's namespace (see session.ts). The namespace
// is captured on mount; once that session ends the component never writes
// again, so a late update cannot leak into another account.

function read<T>(storageKey: string | null, initial: T): T {
  if (storageKey === null) return initial
  try {
    const raw = window.localStorage.getItem(storageKey)
    return raw === null ? initial : (JSON.parse(raw) as T)
  } catch {
    // Corrupt JSON or storage unavailable — fall back to the initial value.
    return initial
  }
}

export function usePersistentState<T>(key: string, initial: T) {
  const [ns] = useState(sessionOwner)
  const [state, setState] = useState<T>(() => read(scopedKey(key, ns), initial))

  useEffect(() => {
    const storageKey = scopedKey(key, ns)
    if (storageKey === null || sessionOwner() !== ns) return
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(state))
    } catch {
      // Storage full or unavailable — persistence is best-effort, so ignore.
    }
  }, [key, ns, state])

  return [state, setState] as const
}
