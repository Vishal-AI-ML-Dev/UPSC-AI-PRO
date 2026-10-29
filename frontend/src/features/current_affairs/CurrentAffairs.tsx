import { useEffect, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { api, streamAgent } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card } from "../../components/ui"
import { Feedback } from "../../components/Feedback"

type Tab = "daily" | "editorial" | "monthly"

const TABS: { id: Tab; label: string }[] = [
  { id: "daily", label: "Daily" },
  { id: "editorial", label: "Editorial" },
  { id: "monthly", label: "Monthly" },
]

export function CurrentAffairs() {
  const [tab, setTab] = usePersistentState<Tab>("ca:tab", "daily")
  const [dates, setDates] = useState<string[]>([])
  const [topics, setTopics] = useState<string[]>([])
  const [months, setMonths] = useState<Array<[string, string]>>([])

  const [date, setDate] = usePersistentState("ca:date", "")
  const [topic, setTopic] = usePersistentState("ca:topic", "")
  const [monthIdx, setMonthIdx] = usePersistentState("ca:monthIdx", 0)

  const [streaming, setStreaming] = useState(false)
  const [output, setOutput] = usePersistentState("ca:output", "")
  const [label, setLabel] = usePersistentState("ca:label", "")

  useEffect(() => {
    api
      .caDates()
      .then((r) => {
        setDates(r.dates)
        setDate((d) => d || r.dates[0] || "")
      })
      .catch(() => {})
    api
      .caTopics()
      .then((r) => {
        setTopics(r.topics)
        setTopic((t) => t || r.topics[0] || "")
      })
      .catch(() => {})
    api
      .caMonths()
      .then((r) => setMonths(r.months))
      .catch(() => {})
  }, [])

  async function run() {
    if (streaming) return
    let path = ""
    let body: Record<string, string> = {}
    let lbl = ""
    if (tab === "daily") {
      if (!date) return
      path = "/current-affairs/daily"
      body = { date }
      lbl = `Daily current affairs — ${date}`
    } else if (tab === "editorial") {
      if (!topic) return
      path = "/current-affairs/editorial"
      body = { topic }
      lbl = `Editorial — ${topic}`
    } else {
      const m = months[monthIdx]
      if (!m) return
      path = "/current-affairs/monthly"
      body = { month: m[0], year: m[1] }
      lbl = `Monthly digest — ${m[0]} ${m[1]}`
    }
    setStreaming(true)
    setOutput("")
    setLabel(lbl)
    await streamAgent(path, body, (piece) => setOutput((p) => p + piece))
    setStreaming(false)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Current Affairs</h1>
        <p className="text-sm text-muted">
          Exam-focused daily current affairs, editorial analysis, and monthly digests.
        </p>
      </div>

      <Card className="space-y-4">
        <div className="flex gap-1 rounded-lg border border-border p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm ${
                tab === t.id ? "bg-surface2 text-fg" : "text-muted hover:text-fg"
              }`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "daily" && (
          <div>
            <label className="label">Date</label>
            <select
              className="input"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            >
              {dates.length === 0 && <option value="">No dates available yet</option>}
              {dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        )}

        {tab === "editorial" && (
          <div>
            <label className="label">Topic</label>
            <select
              className="input"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            >
              {topics.length === 0 && <option value="">No topics available yet</option>}
              {topics.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        )}

        {tab === "monthly" && (
          <div>
            <label className="label">Month</label>
            <select
              className="input"
              value={monthIdx}
              onChange={(e) => setMonthIdx(Number(e.target.value))}
            >
              {months.length === 0 && (
                <option value={0}>No months available yet</option>
              )}
              {months.map((m, i) => (
                <option key={`${m[0]}-${m[1]}`} value={i}>
                  {m[0]} {m[1]}
                </option>
              ))}
            </select>
          </div>
        )}

        <Button onClick={() => void run()} disabled={streaming}>
          {streaming ? "Generating…" : "Generate"}
        </Button>
      </Card>

      {output && (
        <Card className="space-y-3">
          <Markdown>{output}</Markdown>
          {!streaming && (
            <Feedback agent="current_affairs" question={label} answer={output} />
          )}
        </Card>
      )}
    </div>
  )
}
