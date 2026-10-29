import { lazy, type ComponentType } from "react"

// A drop-in replacement for React.lazy that survives "stale chunk" failures.
//
// After a new frontend deploy, Vite emits freshly-hashed chunk filenames. A tab
// (or CDN edge) still holding the OLD index.html will request chunk names that
// no longer exist -> the dynamic import() rejects -> React surfaces it to the
// nearest ErrorBoundary ("Something went wrong"). A single hard reload fetches
// the new index.html + correct chunk manifest and everything works.
//
// This wrapper automates that: on the first import failure it reloads exactly
// once (guarded by sessionStorage so we can never loop). A genuine, repeatable
// import error still bubbles to the ErrorBoundary on the second attempt.
export function lazyWithRetry<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>,
) {
  const RELOAD_KEY = "upscai:chunk-reloaded"
  return lazy(async () => {
    try {
      const mod = await factory()
      window.sessionStorage.removeItem(RELOAD_KEY)
      return mod
    } catch (err) {
      if (!window.sessionStorage.getItem(RELOAD_KEY)) {
        window.sessionStorage.setItem(RELOAD_KEY, "1")
        window.location.reload()
        // Keep Suspense pending while the page reloads.
        return new Promise<{ default: T }>(() => {})
      }
      throw err
    }
  })
}
