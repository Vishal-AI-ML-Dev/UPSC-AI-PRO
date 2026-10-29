import { useMemo, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { streamAgent } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card } from "../../components/ui"
import { Feedback } from "../../components/Feedback"
import { parsePlan, structureSection, planKey } from "../../lib/planMarkdown"

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

  async function generate() {
    if (streaming || !goal.trim()) return
    setStreaming(true)
    setPlan("")
    setLastGoal(goal.trim())
    await streamAgent(
      "/planner/generate",
      {
        goal: goal.trim(),
        hours: hours.trim() || "6",
        optional: optional.trim() || "Not decided",
        weak: weak.trim() || "Not specified",
        attempt_number: attempt,
      },
      (piece) => setPlan((p) => p + piece),
    )
    setStreaming(false)
  }

  function toggleTask(key: string) {
    setProgress((p) => ({ ...p, [key]: !p[key] }))
  }

  function resetProgress() {
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
          Generate a personalised UPSC preparation timeline. Leave weak areas blank and
          they are auto-filled from your recent evaluations.
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
              <option value="">Select optional\u2026</option>
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
            placeholder="e.g. Economy, Environment \u2014 or leave blank to auto-fill"
          />
        </div>
        <Button onClick={() => void generate()} disabled={streaming || !goal.trim()}>
          {streaming ? "Generating\u2026" : "Generate plan"}
        </Button>
      </Card>

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
                  <Button variant="ghost" onClick={resetProgress} disabled={streaming}>
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
                onToggle={(e) =>
                  setCollapsed((c) => ({
                    ...c,
                    [s.id]: !(e.currentTarget as HTMLDetailsElement).open,
                  }))
                }
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
