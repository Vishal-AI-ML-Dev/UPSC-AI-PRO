import { useRef, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { api, streamAgent, type UploadResult } from "../../lib/api"
import { Markdown } from "../../components/Markdown"
import { Button, Card, Spinner } from "../../components/ui"
import { Feedback } from "../../components/Feedback"

interface Msg {
  role: "user" | "assistant"
  content: string
}

export function Upload() {
  const [processing, setProcessing] = useState(false)
  const [stage, setStage] = useState("")
  const [error, setError] = useState("")
  const [doc, setDoc] = usePersistentState<UploadResult | null>("upload:doc", null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const [q, setQ] = useState("")
  const [messages, setMessages] = usePersistentState<Msg[]>("upload:messages", [])
  const [streaming, setStreaming] = useState(false)

  const MAX_UPLOAD_MB = 50

  async function upload(file: File) {
    if (file.type && file.type !== "application/pdf") {
      setError("Please choose a PDF file.")
      return
    }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(
        `This PDF is ${(file.size / (1024 * 1024)).toFixed(1)} MB. Maximum allowed size is ${MAX_UPLOAD_MB} MB \u2014 try compressing it or uploading a single chapter.`,
      )
      return
    }
    setProcessing(true)
    setError("")
    setDoc(null)
    setMessages([])
    try {
      const r = await api.uploadProcess(file, setStage)
      setDoc(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not process the PDF.")
    } finally {
      setProcessing(false)
      setStage("")
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void upload(f)
  }

  async function ask() {
    if (streaming || !q.trim() || !doc) return
    const question = q.trim()
    const history = messages
    setQ("")
    setMessages([
      ...history,
      { role: "user", content: question },
      { role: "assistant", content: "" },
    ])
    setStreaming(true)
    await streamAgent(
      "/upload/chat?pdf_hash=" + encodeURIComponent(doc.hash),
      { request: { question, chat_history: history } },
      (piece) =>
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + piece }
          }
          return next
        }),
    )
    setStreaming(false)
  }

  const bookTitle =
    doc && typeof doc.book_info?.title === "string"
      ? (doc.book_info.title as string)
      : doc?.filename

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Document Upload</h1>
        <p className="text-sm text-muted">
          Upload a PDF (book chapter, notes, or report) to get clean study notes, then
          chat with the document to clear your doubts.
        </p>
      </div>

      <Card className="space-y-3">
        {/* Drag-and-drop dropzone */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => !processing && inputRef.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !processing)
              inputRef.current?.click()
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={
            "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-12 text-center transition " +
            (dragging
              ? "border-brand bg-brand/5"
              : "border-border hover:border-brand hover:bg-surface2")
          }
        >
          <span className="grid h-14 w-14 place-items-center rounded-2xl bg-brandgrad text-2xl text-white">
            {"\u{1F4C4}"}
          </span>
          {processing ? (
            <Spinner label="Reading and summarising your PDF\u2026" />
          ) : (
            <>
              <div className="text-base font-semibold text-fg">
                Drag &amp; drop your PDF here
              </div>
              <div className="text-sm text-muted">
                or <span className="font-medium text-brand-400">click to browse</span>{" "}
                \u2014 book chapters, notes or reports
              </div>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            disabled={processing}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void upload(f)
              e.target.value = ""
            }}
          />
        </div>

        {doc && !processing && (
          <div className="flex items-center gap-2 rounded-xl border border-border bg-surface2 px-3 py-2 text-sm">
            <span>{"\u2705"}</span>
            <span className="text-muted">Loaded:</span>
            <span className="font-medium text-fg">{bookTitle}</span>
          </div>
        )}
        {(stage === "queued" || stage === "running") && !error && (
          <p className="text-sm text-muted">
            {stage === "queued"
              ? "Queued... waiting for a background worker."
              : "Processing in the background... large files can take a minute."}
          </p>
        )}
        {error && <p className="text-sm text-danger">{error}</p>}
      </Card>

      {doc && (
        <Card className="space-y-3">
          <h2 className="text-lg font-semibold text-fg">Study notes</h2>
          <Markdown>{doc.notes}</Markdown>
        </Card>
      )}

      {doc && (
        <Card className="space-y-3">
          <h2 className="text-lg font-semibold text-fg">Chat with this document</h2>
          <div className="space-y-3">
            {messages.map((m, i) => (
              <div
                key={i}
                className={
                  m.role === "user" ? "flex justify-end" : "flex justify-start"
                }
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                    m.role === "user"
                      ? "bg-brand text-white"
                      : "border border-border bg-surface"
                  }`}
                >
                  {m.role === "assistant" ? (
                    m.content ? (
                      <Markdown>{m.content}</Markdown>
                    ) : (
                      <span className="text-muted">Thinking\u2026</span>
                    )
                  ) : (
                    <span className="whitespace-pre-wrap">{m.content}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <textarea
              className="input max-h-40 min-h-[48px] flex-1 resize-y"
              placeholder="Ask something about this document\u2026"
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
              {streaming ? "\u2026" : "Ask"}
            </Button>
          </div>
          {messages.length > 0 && !streaming && (
            <Feedback
              agent="upload"
              question={messages[messages.length - 2]?.content || ""}
              answer={messages[messages.length - 1]?.content || ""}
            />
          )}
        </Card>
      )}
    </div>
  )
}
