import { useEffect, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { api, streamAgent } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card, Spinner } from "../../components/ui"
import { Feedback } from "../../components/Feedback"

type Mode = "generate" | "bank"

const TYPES = [
  { value: "mcq", label: "MCQ (Prelims)" },
  { value: "mains", label: "Mains (descriptive)" },
]
const DIFFICULTY = ["easy", "medium", "hard"]

export function Pyq() {
  const [mode, setMode] = usePersistentState<Mode>("pyq:mode", "generate")

  const [topic, setTopic] = usePersistentState("pyq:topic", "")
  const [questionType, setQuestionType] = usePersistentState("pyq:questionType", "mcq")
  const [difficulty, setDifficulty] = usePersistentState("pyq:difficulty", "medium")
  const [num, setNum] = usePersistentState("pyq:num", 5)
  const [marks, setMarks] = usePersistentState("pyq:marks", 10)

  const [suggestions, setSuggestions] = useState<string[]>([])
  const [streaming, setStreaming] = useState(false)
  const [output, setOutput] = usePersistentState("pyq:output", "")

  const [bankExists, setBankExists] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [bankStage, setBankStage] = useState("")
  const [bankMsg, setBankMsg] = useState("")

  useEffect(() => {
    api
      .pyqTopics(questionType)
      .then((r) => setSuggestions(r.topics))
      .catch(() => setSuggestions([]))
  }, [questionType])

  useEffect(() => {
    api
      .pyqBankStatus()
      .then((r) => setBankExists(r.exists))
      .catch(() => {})
  }, [])

  async function generate(path: string, requireTopic: boolean) {
    if (streaming) return
    if (requireTopic && !topic.trim()) return
    setStreaming(true)
    setOutput("")
    await streamAgent(
      path,
      {
        topic: topic.trim(),
        question_type: questionType,
        difficulty,
        num_questions: num,
        marks,
      },
      (piece) => setOutput((p) => p + piece),
    )
    setStreaming(false)
  }

  async function uploadBank(file: File) {
    setUploading(true)
    setBankMsg("")
    try {
      const r = await api.pyqBankUpload(file, setBankStage)
      setBankExists(true)
      setBankMsg(`Added ${r.filename} — ~${r.approx_questions} questions indexed.`)
    } catch (e) {
      setBankMsg(e instanceof Error ? e.message : "Upload failed.")
    } finally {
      setUploading(false)
      setBankStage("")
    }
  }

  async function clearBank() {
    try {
      await api.pyqBankClear()
      setBankExists(false)
      setBankMsg("Question bank cleared.")
    } catch {
      // best-effort
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Practice Questions (PYQ)</h1>
        <p className="text-sm text-muted">
          Generate exam-style MCQs and Mains questions on any topic, or ground them on
          your own uploaded question papers.
        </p>
      </div>

      <Card className="space-y-4">
        <div className="flex gap-1 rounded-lg border border-border p-1">
          <button
            className={`flex-1 rounded-md px-3 py-1.5 text-sm ${
              mode === "generate" ? "bg-surface2 text-fg" : "text-muted hover:text-fg"
            }`}
            onClick={() => setMode("generate")}
          >
            Generate
          </button>
          <button
            className={`flex-1 rounded-md px-3 py-1.5 text-sm ${
              mode === "bank" ? "bg-surface2 text-fg" : "text-muted hover:text-fg"
            }`}
            onClick={() => setMode("bank")}
          >
            My question bank
          </button>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label">
              Topic {mode === "bank" && <span className="text-muted">(optional)</span>}
            </label>
            <input
              className="input"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Fundamental Rights"
            />
          </div>
          <div>
            <label className="label">Question type</label>
            <select
              className="input"
              value={questionType}
              onChange={(e) => setQuestionType(e.target.value)}
            >
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Difficulty</label>
            <select
              className="input"
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value)}
            >
              {DIFFICULTY.map((d) => (
                <option key={d} value={d}>
                  {d[0].toUpperCase() + d.slice(1)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Number of questions</label>
            <input
              type="number"
              className="input"
              min={1}
              max={20}
              value={num}
              onChange={(e) => setNum(Number(e.target.value))}
            />
          </div>
          {questionType === "mains" && (
            <div>
              <label className="label">Marks per question</label>
              <select
                className="input"
                value={marks}
                onChange={(e) => setMarks(Number(e.target.value))}
              >
                <option value={10}>10 marks</option>
                <option value={15}>15 marks</option>
              </select>
            </div>
          )}
        </div>

        {suggestions.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {suggestions.slice(0, 8).map((s) => (
              <button
                key={s}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted transition hover:border-brand hover:text-fg"
                onClick={() => setTopic(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {mode === "generate" && (
          <Button
            onClick={() => void generate("/pyq/generate", true)}
            disabled={streaming || !topic.trim()}
          >
            {streaming ? "Generating…" : "Generate questions"}
          </Button>
        )}

        {mode === "bank" && (
          <div className="space-y-3 rounded-lg border border-border p-3">
            <p className="text-sm text-muted">
              {bankExists
                ? "Your personal question bank is ready. Questions are grounded on your uploaded papers."
                : "Upload a past-paper PDF to build your personal, grounded question bank."}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <label className="btn btn-ghost cursor-pointer text-sm">
                {uploading ? "Uploading…" : "Upload PDF"}
                <input
                  type="file"
                  accept="application/pdf"
                  className="hidden"
                  disabled={uploading}
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) void uploadBank(f)
                    e.target.value = ""
                  }}
                />
              </label>
              {bankExists && (
                <button
                  className="text-sm text-muted hover:text-danger"
                  onClick={() => void clearBank()}
                >
                  Clear bank
                </button>
              )}
              {uploading && <Spinner label="Indexing paper…" />}
            </div>
            {(bankStage === "queued" || bankStage === "running") && (
              <p className="text-xs text-muted">
                {bankStage === "queued"
                  ? "Queued... waiting for a background worker."
                  : "Indexing in the background..."}
              </p>
            )}
            {bankMsg && <p className="text-xs text-muted">{bankMsg}</p>}
            <Button
              onClick={() => void generate("/pyq/bank/generate", false)}
              disabled={streaming || !bankExists}
            >
              {streaming ? "Generating…" : "Generate from my bank"}
            </Button>
          </div>
        )}
      </Card>

      {output && (
        <Card className="space-y-3">
          <Markdown>{output}</Markdown>
          {!streaming && (
            <Feedback agent="pyq" question={topic || "question bank"} answer={output} />
          )}
        </Card>
      )}
    </div>
  )
}
