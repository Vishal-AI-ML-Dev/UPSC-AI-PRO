import { useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { api, type AnswerEvaluation, type MainsEvaluation } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Feedback } from "../../components/Feedback"
import { Button, Card, Spinner } from "../../components/ui"

type Mode = "answer" | "mains"

interface Result {
  response: string
  answer?: AnswerEvaluation
  mains?: MainsEvaluation
}

const MIN_ANSWER = 50
const MIN_MAINS = 30

const PLACEHOLDER_Q =
  "Paste the exam question here. For example: Examine the role of the Governor in the Indian federal structure and whether the office has lived up to its constitutional intent."
const PLACEHOLDER_A =
  "Paste your full written answer here. Write it the way you would in the exam — introduction, body, and conclusion — so the evaluation reflects your real performance."

function toneClass(score: number | null, max: number): string {
  if (score === null || !max) return "text-muted"
  const pct = score / max
  if (pct >= 0.75) return "text-emerald-400"
  if (pct >= 0.5) return "text-amber-400"
  return "text-red-400"
}

function ListSection({
  title,
  items,
  marker,
}: {
  title: string
  items: string[]
  marker: string
}) {
  if (!items || items.length === 0) return null
  return (
    <div>
      <h4 className="mb-1 text-sm font-semibold text-fg">{title}</h4>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="flex gap-2 text-sm text-muted">
            <span aria-hidden>{marker}</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ScoreBadge({
  score,
  max,
  unit,
}: {
  score: number | null
  max: number
  unit: string
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className={`text-4xl font-extrabold ${toneClass(score, max)}`}>
        {score === null ? "—" : score}
      </span>
      <span className="text-lg text-muted">
        / {max} {unit}
      </span>
    </div>
  )
}

export function Evaluator() {
  const [mode, setMode] = usePersistentState<Mode>("eval:mode", "answer")
  const [question, setQuestion] = usePersistentState("eval:question", "")
  const [answer, setAnswer] = usePersistentState("eval:answer", "")
  const [marks, setMarks] = usePersistentState("eval:marks", 10)
  const [wordLimit, setWordLimit] = usePersistentState("eval:wordLimit", 150)
  const [keywords, setKeywords] = usePersistentState("eval:keywords", "")

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [result, setResult] = usePersistentState<Result | null>("eval:result", null)

  const [modelLoading, setModelLoading] = useState(false)
  const [modelAnswer, setModelAnswer] = usePersistentState("eval:modelAnswer", "")

  const minAnswer = mode === "answer" ? MIN_ANSWER : MIN_MAINS
  const answerShort = answer.trim().length < minAnswer
  const questionShort = question.trim().length < 5

  function keywordList(): string[] | undefined {
    const list = keywords
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean)
    return list.length ? list : undefined
  }

  async function evaluate() {
    if (loading || questionShort || answerShort) return
    setLoading(true)
    setError("")
    setResult(null)
    setModelAnswer("")
    try {
      if (mode === "answer") {
        const r = await api.evaluateAnswer({
          question: question.trim(),
          answer: answer.trim(),
        })
        setResult({ response: r.response, answer: r.structured })
      } else {
        const r = await api.evaluateMains({
          question: question.trim(),
          answer: answer.trim(),
          marks,
          word_limit: wordLimit,
          keywords: keywordList(),
        })
        setResult({ response: r.response, mains: r.structured })
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Evaluation failed. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  async function generateModel() {
    if (modelLoading || questionShort) return
    setModelLoading(true)
    setError("")
    try {
      const r = await api.modelAnswer({
        question: question.trim(),
        marks: mode === "mains" ? marks : 10,
        word_limit: wordLimit,
        keywords: keywordList(),
      })
      setModelAnswer(r.response)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate a model answer.")
    } finally {
      setModelLoading(false)
    }
  }

  const scoreMax = result?.answer
    ? result.answer.max_score
    : (result?.mains?.max_marks ?? marks)
  const scoreVal = result?.answer ? result.answer.score : (result?.mains?.score ?? null)
  const hasStructured = Boolean(result?.answer || result?.mains)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Answer Evaluator</h1>
        <p className="text-sm text-muted">
          Get an examiner-style score with specific strengths and fixes on your answer,
          plus an optional model answer to compare against.
        </p>
      </div>

      <Card className="space-y-4">
        <div className="flex gap-1 rounded-lg border border-border p-1">
          <button
            className={`flex-1 rounded-md px-3 py-1.5 text-sm ${
              mode === "answer" ? "bg-surface2 text-fg" : "text-muted hover:text-fg"
            }`}
            onClick={() => setMode("answer")}
          >
            Quick evaluation
          </button>
          <button
            className={`flex-1 rounded-md px-3 py-1.5 text-sm ${
              mode === "mains" ? "bg-surface2 text-fg" : "text-muted hover:text-fg"
            }`}
            onClick={() => setMode("mains")}
          >
            Mains (marks-based)
          </button>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-fg">Question</label>
          <textarea
            className="input min-h-[70px] w-full resize-y"
            placeholder={PLACEHOLDER_Q}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-fg">Your answer</label>
          <textarea
            className="input min-h-[160px] w-full resize-y"
            placeholder={PLACEHOLDER_A}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <p className="mt-1 text-xs text-muted">
            {answer.trim().length} characters — at least {minAnswer} are needed for a
            reliable evaluation.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {mode === "mains" && (
            <div>
              <label className="mb-1 block text-sm font-medium text-fg">Marks</label>
              <select
                className="input w-full"
                value={marks}
                onChange={(e) => setMarks(Number(e.target.value))}
              >
                <option value={10}>10 marks</option>
                <option value={15}>15 marks</option>
              </select>
            </div>
          )}
        </div>

        {mode === "mains" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-fg">
                Word limit
              </label>
              <input
                type="number"
                className="input w-full"
                value={wordLimit}
                min={50}
                max={400}
                onChange={(e) => setWordLimit(Number(e.target.value))}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-fg">
                Expected keywords <span className="text-muted">(optional)</span>
              </label>
              <input
                className="input w-full"
                placeholder="comma, separated, terms"
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => void evaluate()}
            disabled={loading || questionShort || answerShort}
          >
            {loading ? "Evaluating…" : "Evaluate answer"}
          </Button>
          <Button
            variant="ghost"
            onClick={() => void generateModel()}
            disabled={modelLoading || questionShort}
          >
            {modelLoading ? "Generating…" : "Show model answer"}
          </Button>
          {loading && <Spinner label="Reading your answer like an examiner…" />}
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}
      </Card>

      {hasStructured && result && (
        <Card className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="text-sm text-muted">Score</span>
              <ScoreBadge
                score={scoreVal}
                max={scoreMax}
                unit={mode === "mains" ? "marks" : "points"}
              />
            </div>
            {result.mains?.verdict && (
              <p className="max-w-md text-sm text-muted">
                <span className="font-semibold text-fg">Verdict: </span>
                {result.mains.verdict}
              </p>
            )}
          </div>

          {scoreVal === null && (
            <p className="text-xs text-amber-400">
              A numeric score was not detected — see the full feedback below.
            </p>
          )}

          <div className="grid gap-5 sm:grid-cols-3">
            {result.answer && (
              <>
                <ListSection
                  title="What you did well"
                  items={result.answer.did_well}
                  marker="✅"
                />
                <ListSection
                  title="What's missing"
                  items={result.answer.missing}
                  marker="⚠️"
                />
                <ListSection
                  title="Priority improvements"
                  items={result.answer.improvements}
                  marker="→"
                />
              </>
            )}
            {result.mains && (
              <>
                <ListSection
                  title="Strengths"
                  items={result.mains.strengths}
                  marker="✅"
                />
                <ListSection title="Gaps" items={result.mains.gaps} marker="⚠️" />
                <ListSection
                  title="Top improvements"
                  items={result.mains.improvements}
                  marker="→"
                />
              </>
            )}
          </div>

          <details className="rounded-lg border border-border bg-surface2 p-3">
            <summary className="cursor-pointer text-sm font-medium text-fg">
              Full examiner feedback
            </summary>
            <div className="mt-3">
              <Markdown>{result.response}</Markdown>
            </div>
          </details>

          <Feedback agent="evaluator" question={question} answer={result.response} />
        </Card>
      )}

      {modelAnswer && (
        <Card className="space-y-2">
          <h3 className="text-lg font-semibold text-fg">Model answer</h3>
          <Markdown>{modelAnswer}</Markdown>
        </Card>
      )}
    </div>
  )
}
