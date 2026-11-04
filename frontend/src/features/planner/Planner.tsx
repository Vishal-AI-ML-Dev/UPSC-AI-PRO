import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import {
  ApiError,
  STREAM_NOTICE_PREFIX,
  api,
  streamAgent,
  type StudyPlanInputs,
} from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card } from "../../components/ui"
import { Feedback } from "../../components/Feedback"
import { parsePlan, structureSection, planKey } from "../../lib/planMarkdown"
import { createLatestGuard, decideReconcile, type PlanDraft } from "./reconcilePlan"

const ATTEMPTS = ["1", "2", "3", "4+"]
const HOURS = ["2", "3", "4", "5", "6", "7", "8", "10", "12"]
const OPTIONALS = [
  "Not decided",
  "Sociology",
  "PSIR (Political Science & IR)",
  "Geography",
  "History",
  "Public Administration",
  "Anthropology",
  "Philosophy",
  "Economics",
  "Mathematics",
  "Hindi Literature",
  "Commerce & Accountancy",
]

// Server sync state of the plan shown (spec R-B6). The server copy is
// authoritative once a plan is saved; localStorage is a cache / offline copy.
type SyncState =
  | "loading" // initial GET /planner/plan in flight (read-only)
  | "offline" // GET failed: cached copy, read-only until a successful GET
  | "synced" // the shown plan is the saved plan (planId set)
  | "local" // a cached plan that was never saved (explicit Save only)
  | "draftConflict" // an unsaved generated plan + a different saved plan: user chooses
  | "saving"
  | "saveFailed"
  | "idle" // nothing to sync (empty, streaming, or a failed generation)

const TICKS_NOT_SAVED = "Your last ticks were not saved."
const PROGRESS_DEBOUNCE_MS = 800

// The ticked checklist entries of one plan (keys are `${planKey}:${section}:${i}`).
function ticksFor(pKey: string, progress: Record<string, boolean>) {
  const out: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(progress)) {
    if (v === true && k.startsWith(`${pKey}:`)) out[k] = true
  }
  return out
}

// A real plan, not empty output or a failure notice (stream errors, timeouts,
// specialist failure fallbacks all carry STREAM_NOTICE_PREFIX).
function isSavable(text: string) {
  return !!text.trim() && !text.includes(STREAM_NOTICE_PREFIX)
}

export function Planner() {
  const [goal, setGoal] = usePersistentState("planner:goal", "UPSC 2026")
  const [hours, setHours] = usePersistentState("planner:hours", "6")
  const [optional, setOptional] = usePersistentState("planner:optional", "")
  const [weak, setWeak] = usePersistentState("planner:weak", "")
  const [attempt, setAttempt] = usePersistentState("planner:attempt", "1")

  const [streaming, setStreaming] = useState(false)
  const [plan, setPlan] = usePersistentState("planner:plan", "")
  const [lastGoal, setLastGoal] = usePersistentState("planner:lastGoal", "")

  // Per-plan checklist progress + collapsed state, namespaced by a plan hash so
  // a freshly generated plan starts with a clean, unchecked list.
  const [progress, setProgress] = usePersistentState<Record<string, boolean>>(
    "planner:progress",
    {},
  )
  const [collapsed, setCollapsed] = usePersistentState<Record<string, boolean>>(
    "planner:collapsed",
    {},
  )
  const [printing, setPrinting] = useState(false)

  // Server persistence (cache keys per spec R-B6).
  const [planId, setPlanId] = usePersistentState<string | null>("planner:planId", null)
  const [dirty, setDirty] = usePersistentState("planner:dirty", false)
  const [sync, setSync] = useState<SyncState>("loading")
  const [syncError, setSyncError] = useState("")
  const [ticksNotice, setTicksNotice] = useState("")
  // An unsaved generated plan and the inputs that produced it (Amendment B-1).
  // Set when a generation succeeds, cleared only by a successful save or by
  // "Load saved plan" / a new generation (explicit actions).
  const [draft, setDraft] = usePersistentState<PlanDraft | null>("planner:draft", null)
  // Whether the account has a saved plan (from the last GET or save).
  const [savedExists, setSavedExists] = useState(false)
  // Only the latest generation / save / load may apply a save result, so a late
  // save response never attaches an old id to a different plan.
  const saveGuard = useRef(createLatestGuard())
  // Bumped on every tick, so a PATCH that resolves after a newer tick does not
  // clear the dirty flag of the newer ticks.
  const tickVersion = useRef(0)
  const alive = useRef(true)
  const readOnly = sync === "loading" || sync === "offline"

  const pKey = useMemo(() => (plan ? planKey(plan) : ""), [plan])
  const parsed = useMemo(() => (plan ? parsePlan(plan) : null), [plan])
  const structured = useMemo(() => {
    if (!parsed) return []
    return parsed.sections.map((s) => ({
      id: s.id,
      title: s.title,
      ...structureSection(s.body),
    }))
  }, [parsed])

  const { totalTasks, doneTasks } = useMemo(() => {
    let total = 0
    let done = 0
    for (const s of structured) {
      s.items.forEach((_, i) => {
        total += 1
        if (progress[`${pKey}:${s.id}:${i}`]) done += 1
      })
    }
    return { totalTasks: total, doneTasks: done }
  }, [structured, progress, pKey])

  const percent = totalTasks ? Math.round((doneTasks / totalTasks) * 100) : 0

  // Reconcile the cache with the server copy (spec R-B6). `cache` always holds
  // the latest cached values, so "Retry sync" reconciles what is cached now.
  const cache = useRef({ plan, planId, dirty, progress, draft })
  cache.current = { plan, planId, dirty, progress, draft }

  // `discardDraft`: the user chose "Load saved plan", so a pending draft is
  // dropped and the saved plan replaces it.
  const reconcile = useCallback(
    async (discardDraft = false) => {
      setSync("loading")
      setSyncError("")
      let server
      try {
        server = (await api.getPlan()).plan
      } catch (e) {
        if (!alive.current) return
        setSyncError(e instanceof Error ? e.message : "")
        setSync("offline")
        return
      }
      if (!alive.current) return
      const cached = { ...cache.current }
      // Only a saved plan can replace the draft; with none, the draft is kept.
      if (discardDraft && server) {
        cached.draft = null
        setDraft(null)
      }
      setSavedExists(!!server)
      const action = decideReconcile(server, cached, isSavable)
      if (action === "keepLocal") {
        setSync("local") // never saved: kept, uploaded only by an explicit Save
        return
      }
      if (action === "clear") {
        // The saved plan was deleted on the server, which is authoritative.
        setPlan("")
        setProgress({})
        setPlanId(null)
        setDirty(false)
        setDraft(null)
      }
      if (!server || action === "clear" || action === "empty") {
        setSync("idle")
        return
      }
      if (action === "draftConflict") {
        // Never discard an unsaved generated plan silently: the user chooses.
        setSync("draftConflict")
        return
      }
      if (action === "adoptSaved") {
        // The draft's save reached the server before the response was lost.
        setPlanId(server.id)
        setDraft(null)
        setDirty(
          Object.keys(ticksFor(planKey(cached.plan), cached.progress)).length > 0,
        )
        setLastGoal(server.goal)
        setSync("synced")
        return
      }
      if (action === "resendTicks") {
        // Unsent ticks for the same plan: re-send them once.
        try {
          const res = await api.patchPlanProgress(
            server.id,
            ticksFor(planKey(server.plan_md), cached.progress),
          )
          if (!alive.current) return
          setPlan(server.plan_md)
          setProgress(res.progress)
          setDirty(false)
          setTicksNotice("")
          setSync("synced")
          return
        } catch {
          if (!alive.current) return
          setTicksNotice(TICKS_NOT_SAVED)
        }
      }
      // "useServer", or a failed re-send: the server copy replaces the cache.
      setPlan(server.plan_md)
      setProgress(server.progress)
      setPlanId(server.id)
      setDirty(false)
      setDraft(null)
      setLastGoal(server.goal)
      setSync("synced")
    },
    [setDirty, setDraft, setLastGoal, setPlan, setPlanId, setProgress],
  )

  useEffect(() => {
    alive.current = true
    void reconcile()
    return () => {
      alive.current = false
    }
  }, [reconcile])

  async function savePlan(text: string, toSave: PlanDraft) {
    const token = saveGuard.current.begin()
    setSync("saving")
    setSyncError("")
    try {
      const res = await api.savePlan({
        goal: toSave.goal.slice(0, 200),
        inputs: toSave.inputs,
        plan_md: text,
      })
      // A newer generation / save / load started meanwhile: this result is stale.
      if (!alive.current || !saveGuard.current.isCurrent(token)) return
      setPlanId(res.id)
      setDraft(null)
      setSavedExists(true)
      // The server starts the plan with no progress; ticks made before the save
      // are sent by the debounced PATCH below.
      setDirty(Object.keys(ticksFor(planKey(text), cache.current.progress)).length > 0)
      setTicksNotice("")
      setSync("synced")
    } catch (e) {
      if (!alive.current || !saveGuard.current.isCurrent(token)) return
      // The draft stays cached (incl. its ticks and inputs) for Retry or a later visit.
      setSyncError(e instanceof Error ? e.message : "")
      setSync("saveFailed")
    }
  }

  function currentInputs(): StudyPlanInputs {
    return {
      hours: hours.trim() || "6",
      optional: optional.trim() || "Not decided",
      weak: weak.trim() || "Not specified",
      attempt_number: attempt,
    }
  }

  function saveCurrent() {
    if (!isSavable(plan) || streaming || sync === "saving") return
    // A plan cached before Amendment B-1 has no draft: use the current form inputs.
    void savePlan(
      plan,
      draft ?? { goal: lastGoal || goal.trim(), inputs: currentInputs() },
    )
  }

  function loadSavedPlan() {
    if (streaming || sync === "saving") return
    saveGuard.current.begin()
    void reconcile(true)
  }

  const generateBlocked = streaming || readOnly || sync === "saving"

  async function generate() {
    if (generateBlocked || !goal.trim()) return
    // Any save still in flight is now stale and can no longer apply its id.
    saveGuard.current.begin()
    setStreaming(true)
    setPlan("")
    setLastGoal(goal.trim())
    // The saved plan is replaced only once the new one is saved.
    setPlanId(null)
    setDirty(false)
    setDraft(null)
    setTicksNotice("")
    setSyncError("")
    setSync("idle")
    const inputs = currentInputs()
    let text = ""
    await streamAgent(
      "/planner/generate",
      {
        goal: goal.trim(),
        hours: inputs.hours,
        optional: inputs.optional,
        weak: inputs.weak,
        attempt_number: inputs.attempt_number,
      },
      (piece) => {
        text += piece
        setPlan((p) => p + piece)
      },
    )
    setStreaming(false)
    if (!alive.current) return
    // Failure notices (connection errors, timeouts, specialist failure
    // fallbacks) carry STREAM_NOTICE_PREFIX and are never saved as a plan.
    if (!isSavable(text)) return
    // Persist the draft before saving, so a failed save or a reload during the
    // save never loses the generated plan or its inputs.
    const newDraft: PlanDraft = { goal: goal.trim(), inputs }
    setDraft(newDraft)
    await savePlan(text, newDraft)
  }

  // Debounced progress sync of the saved plan.
  useEffect(() => {
    if (!dirty || !planId || sync !== "synced" || !pKey) return
    const version = tickVersion.current
    const handle = window.setTimeout(() => {
      api
        .patchPlanProgress(planId, ticksFor(pKey, progress))
        .then(() => {
          if (!alive.current) return
          setTicksNotice("")
          if (tickVersion.current === version) setDirty(false)
        })
        .catch((e: unknown) => {
          if (!alive.current) return
          setTicksNotice(
            e instanceof ApiError && e.status === 409
              ? TICKS_NOT_SAVED +
                  " This plan was replaced by a newer one; reload to see it."
              : TICKS_NOT_SAVED,
          )
        })
    }, PROGRESS_DEBOUNCE_MS)
    return () => window.clearTimeout(handle)
  }, [dirty, planId, sync, pKey, progress, setDirty])

  function markTicked() {
    tickVersion.current += 1
    if (planId && sync === "synced") setDirty(true)
  }

  function toggleTask(key: string) {
    if (readOnly) return
    setProgress((p) => ({ ...p, [key]: !p[key] }))
    markTicked()
  }

  function resetProgress() {
    if (readOnly) return
    markTicked()
    setProgress((prev) => {
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (k.startsWith(`${pKey}:`)) delete next[k]
      }
      return next
    })
  }

  function printPlan() {
    // Force every section open so nothing is clipped, then invoke the browser's
    // native print / "Save as PDF" dialog.
    setPrinting(true)
    window.setTimeout(() => {
      window.print()
      setPrinting(false)
    }, 80)
  }

  return (
    <div className="space-y-6">
      {/* Print styles: only the plan area is printed, controls are hidden. */}
      <style>{`
        @media print {
          body * { visibility: hidden !important; }
          #plan-printable, #plan-printable * { visibility: visible !important; }
          #plan-printable { position: absolute; left: 0; top: 0; width: 100%; padding: 0 8px; }
          #plan-printable details { break-inside: avoid; }
          .no-print { display: none !important; }
        }
      `}</style>

      <div className="no-print">
        <h1 className="text-2xl font-extrabold text-fg">Study Planner</h1>
        <p className="text-sm text-muted">
          Generate a personalised UPSC preparation timeline. Leave blank to use weak
          areas you&apos;ve mentioned to the Mentor.
        </p>
      </div>

      <Card className="space-y-4 no-print">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label">Goal</label>
            <input
              className="input"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. UPSC 2026"
            />
          </div>
          <div>
            <label className="label">Daily study hours</label>
            <select
              className="input"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>
                  {h} hours / day
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Optional subject</label>
            <select
              className="input"
              value={optional}
              onChange={(e) => setOptional(e.target.value)}
            >
              <option value="">Select optional…</option>
              {OPTIONALS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Attempt number</label>
            <select
              className="input"
              value={attempt}
              onChange={(e) => setAttempt(e.target.value)}
            >
              {ATTEMPTS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label className="label">Weak areas (optional)</label>
          <input
            className="input"
            value={weak}
            onChange={(e) => setWeak(e.target.value)}
            placeholder="e.g. Economy, Environment — or leave blank to auto-fill"
          />
        </div>
        <Button
          onClick={() => void generate()}
          disabled={generateBlocked || !goal.trim()}
        >
          {streaming ? "Generating\u2026" : "Generate plan"}
        </Button>
      </Card>

      {(plan || sync === "offline") && !streaming && (
        <SyncBanner
          sync={sync}
          error={syncError}
          ticksNotice={ticksNotice}
          onSave={saveCurrent}
          onRetrySync={() => void reconcile()}
          savedExists={savedExists}
          onLoadSaved={loadSavedPlan}
        />
      )}

      {plan && (
        <div id="plan-printable" className="space-y-4">
          {/* Progress + actions toolbar */}
          {totalTasks > 0 && (
            <Card className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-fg">
                  Progress: {doneTasks}/{totalTasks} tasks ({percent}%)
                </div>
                <div className="no-print flex gap-2">
                  <Button
                    variant="ghost"
                    onClick={resetProgress}
                    disabled={streaming || readOnly}
                  >
                    Reset
                  </Button>
                  <Button variant="ghost" onClick={printPlan} disabled={streaming}>
                    Print / Save PDF
                  </Button>
                </div>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-border">
                <div
                  className="h-2 rounded-full bg-brand-400 transition-all"
                  style={{ width: `${percent}%` }}
                />
              </div>
            </Card>
          )}

          {parsed?.intro && (
            <Card>
              <Markdown>{parsed.intro}</Markdown>
            </Card>
          )}

          {structured.map((s) => {
            const doneInSection = s.items.filter(
              (_, i) => progress[`${pKey}:${s.id}:${i}`],
            ).length
            return (
              <details
                key={s.id}
                open={printing || !collapsed[s.id]}
                onToggle={(e) => {
                  // Read the event synchronously: React may run the updater later,
                  // when e.currentTarget has already been reset to null.
                  const open = e.currentTarget.open
                  setCollapsed((c) => ({ ...c, [s.id]: !open }))
                }}
                className="overflow-hidden rounded-xl border border-border"
              >
                <summary className="flex cursor-pointer select-none items-center justify-between gap-2 px-4 py-3 font-semibold text-fg">
                  <span>{s.title}</span>
                  {s.items.length > 0 && (
                    <span className="text-xs font-normal text-muted">
                      {doneInSection}/{s.items.length}
                    </span>
                  )}
                </summary>
                <div className="space-y-2 px-4 pb-4">
                  {s.lead && <Markdown>{s.lead}</Markdown>}
                  {s.items.map((item, i) => {
                    const key = `${pKey}:${s.id}:${i}`
                    const checked = !!progress[key]
                    return (
                      <div key={i} className="flex gap-2">
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 shrink-0"
                          checked={checked}
                          disabled={readOnly}
                          onChange={() => toggleTask(key)}
                        />
                        <div className={checked ? "opacity-60 line-through" : ""}>
                          <Markdown>{item.text}</Markdown>
                          {item.detail && <Markdown>{item.detail}</Markdown>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </details>
            )
          })}

          {!streaming && (
            <div className="no-print">
              <Feedback agent="planner" question={lastGoal} answer={plan} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function SyncBanner({
  sync,
  error,
  ticksNotice,
  savedExists,
  onSave,
  onRetrySync,
  onLoadSaved,
}: {
  sync: SyncState
  error: string
  ticksNotice: string
  savedExists: boolean
  onSave: () => void
  onRetrySync: () => void
  onLoadSaved: () => void
}) {
  let message = ""
  let actions: { label: string; onClick: () => void }[] = []
  if (sync === "offline") {
    message = "Offline copy — not synced. Changes are disabled until it syncs."
    actions = [{ label: "Retry sync", onClick: onRetrySync }]
  } else if (sync === "local") {
    message = "Not saved to your account."
    actions = [{ label: "Save", onClick: onSave }]
  } else if (sync === "draftConflict") {
    message =
      "This generated plan is not saved. A different plan is saved to your account."
    actions = [
      { label: "Save to replace it", onClick: onSave },
      { label: "Load saved plan", onClick: onLoadSaved },
    ]
  } else if (sync === "saveFailed") {
    message = "Not saved — kept on this device until you save it."
    actions = [{ label: "Retry", onClick: onSave }]
    if (savedExists) actions.push({ label: "Load saved plan", onClick: onLoadSaved })
  } else if (sync === "saving") {
    message = "Saving to your account…"
  } else if (sync === "loading") {
    message = "Syncing with your account…"
  }
  if (!message && !ticksNotice) return null
  return (
    <Card className="no-print space-y-1 text-sm">
      {message && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-fg">{message}</span>
          {actions.length > 0 && (
            <div className="flex gap-2">
              {actions.map((a) => (
                <Button key={a.label} variant="ghost" onClick={a.onClick}>
                  {a.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}
      {error && (sync === "offline" || sync === "saveFailed") && (
        <div className="text-muted">{error}</div>
      )}
      {ticksNotice && <div className="text-fg">{ticksNotice}</div>}
    </Card>
  )
}
