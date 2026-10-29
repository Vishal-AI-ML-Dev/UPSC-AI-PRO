import { useEffect, useRef, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { api, streamAgent, type NcertSession } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card, Spinner } from "../../components/ui"
import { Feedback } from "../../components/Feedback"

// Server-generated (trusted) HTML for the mind map / question blocks. Kept in a
// small helper so the dangerouslySetInnerHTML payload is a named object.
function RawHtml({ html }: { html: string }) {
  const props = { __html: html }
  return <div className="mt-2" dangerouslySetInnerHTML={props} />
}

export function Ncert() {
  const [classes, setClasses] = useState<string[]>([])
  const [subjects, setSubjects] = useState<string[]>([])
  const [chapters, setChapters] = useState<string[]>([])
  const [cls, setCls] = usePersistentState("ncert:cls", "")
  const [subject, setSubject] = usePersistentState("ncert:subject", "")
  const [chapter, setChapter] = usePersistentState("ncert:chapter", "")

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [session, setSession] = usePersistentState<NcertSession | null>(
    "ncert:session",
    null,
  )

  const [q, setQ] = useState("")
  const [answer, setAnswer] = usePersistentState("ncert:answer", "")
  const [streaming, setStreaming] = useState(false)

  // Track previous selections so we only reset child dropdowns when the user
  // actually changes a parent selection — not on the initial mount, which
  // would otherwise wipe restored (persisted) selections.
  const prevCls = useRef<string | null>(null)
  const prevChapKey = useRef<string | null>(null)

  useEffect(() => {
    api
      .ncertClasses()
      .then((r) => setClasses(r.items))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const changed = prevCls.current !== null && prevCls.current !== cls
    prevCls.current = cls
    if (changed) {
      setSubjects([])
      setSubject("")
      setChapters([])
      setChapter("")
    }
    if (!cls) return
    api
      .ncertSubjects(cls)
      .then((r) => setSubjects(r.items))
      .catch(() => {})
  }, [cls])

  useEffect(() => {
    const key = cls + "|" + subject
    const changed = prevChapKey.current !== null && prevChapKey.current !== key
    prevChapKey.current = key
    if (changed) {
      setChapters([])
      setChapter("")
    }
    if (!cls || !subject) return
    api
      .ncertChapters(cls, subject)
      .then((r) => setChapters(r.items))
      .catch(() => {})
  }, [cls, subject])

  async function study() {
    if (loading || !cls || !subject || !chapter) return
    setLoading(true)
    setError("")
    setSession(null)
    setAnswer("")
    try {
      const r = await api.ncertStudy({ class_name: cls, subject, chapter })
      setSession(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate the study session.")
    } finally {
      setLoading(false)
    }
  }

  async function ask() {
    if (streaming || !q.trim() || !cls || !subject || !chapter) return
    const question = q.trim()
    setQ("")
    setAnswer("")
    setStreaming(true)
    await streamAgent(
      "/ncert/chat",
      { question, class_name: cls, subject, chapter },
      (piece) => setAnswer((p) => p + piece),
    )
    setStreaming(false)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">NCERT Study</h1>
        <p className="text-sm text-muted">
          Pick a class, subject, and chapter to get concise NCERT notes, then ask
          follow-up questions about the chapter.
        </p>
      </div>

      <Card className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <label className="label">Class</label>
            <select
              className="input"
              value={cls}
              onChange={(e) => setCls(e.target.value)}
            >
              <option value="">Select class</option>
              {classes.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Subject</label>
            <select
              className="input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={!cls}
            >
              <option value="">Select subject</option>
              {subjects.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Chapter</label>
            <select
              className="input"
              value={chapter}
              onChange={(e) => setChapter(e.target.value)}
              disabled={!subject}
            >
              <option value="">Select chapter</option>
              {chapters.map((ch) => (
                <option key={ch} value={ch}>
                  {ch}
                </option>
              ))}
            </select>
          </div>
        </div>
        <Button
          onClick={() => void study()}
          disabled={loading || !cls || !subject || !chapter}
        >
          {loading ? "Preparing notes…" : "Generate notes"}
        </Button>
        {loading && <Spinner label="Building your study session…" />}
        {error && <p className="text-sm text-danger">{error}</p>}
      </Card>

      {session && (
        <Card className="space-y-3">
          <Markdown>{session.notes}</Markdown>
          {session.mindmap_html && (
            <details className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-fg">
                Mind map
              </summary>
              <RawHtml html={session.mindmap_html} />
            </details>
          )}
          {session.questions_html && (
            <details className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-fg">
                Practice questions
              </summary>
              <RawHtml html={session.questions_html} />
            </details>
          )}
        </Card>
      )}

      {session && (
        <Card className="space-y-3">
          <h2 className="text-lg font-semibold text-fg">Ask about this chapter</h2>
          <div className="flex items-end gap-2">
            <textarea
              className="input max-h-40 min-h-[48px] flex-1 resize-y"
              placeholder="e.g. Explain the main causes discussed in this chapter"
              value={q}
              rows={1}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  void ask()
                }
              }}
            />
            <Button onClick={() => void ask()} disabled={streaming || !q.trim()}>
              {streaming ? "…" : "Ask"}
            </Button>
          </div>
          {answer && (
            <div className="rounded-2xl border border-border bg-surface px-4 py-3">
              <Markdown>{answer}</Markdown>
              {!streaming && (
                <Feedback
                  agent="ncert"
                  question={`${subject} — ${chapter}`}
                  answer={answer}
                />
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
