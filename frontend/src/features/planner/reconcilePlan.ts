// Planner server/cache reconciliation decisions (spec R-B6 + Amendment B-1) and a
// latest-only guard for plan saves. Kept free of React and imports so the logic
// can be checked on its own with Node.

export interface PlanDraftInputs {
  hours: string
  optional?: string | null
  weak?: string | null
  attempt_number?: string | null
}

// A generated plan that is not saved yet: the inputs that produced it, kept with
// the cached plan text until a save succeeds or the user loads the saved plan.
export interface PlanDraft {
  goal: string
  inputs: PlanDraftInputs
}

export interface ServerPlanRef {
  id: string
  plan_md: string
}

export interface CachedPlan {
  plan: string
  planId: string | null
  dirty: boolean
  draft: PlanDraft | null
}

export type ReconcileAction =
  | "useServer" // replace the cache with the server plan
  | "resendTicks" // same plan with unsent ticks: re-send once, then use the server plan
  | "adoptSaved" // the pending draft is the saved plan (its save landed): adopt its id
  | "draftConflict" // a pending unsaved draft differs from the saved plan: ask the user
  | "keepLocal" // no saved plan; keep the unsaved cached plan (explicit Save only)
  | "clear" // the saved plan was deleted on the server
  | "empty"

// `savable(text)` is false for empty text and failure notices.
export function decideReconcile(
  server: ServerPlanRef | null,
  cache: CachedPlan,
  savable: (text: string) => boolean,
): ReconcileAction {
  const unsaved = !cache.planId && savable(cache.plan)
  if (!server) {
    if (unsaved) return "keepLocal"
    return cache.planId ? "clear" : "empty"
  }
  if (unsaved && cache.draft) {
    return cache.plan === server.plan_md ? "adoptSaved" : "draftConflict"
  }
  if (cache.planId === server.id && cache.dirty) return "resendTicks"
  return "useServer"
}

// Only the most recently started operation may apply its result. Starting a new
// generation, save or "Load saved plan" makes every older save stale.
export interface LatestGuard {
  begin(): number
  isCurrent(token: number): boolean
}

export function createLatestGuard(): LatestGuard {
  let current = 0
  return {
    begin() {
      current += 1
      return current
    },
    isCurrent(token: number) {
      return token === current
    },
  }
}
