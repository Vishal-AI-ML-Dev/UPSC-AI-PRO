import { useEffect, useRef, useState } from "react"
import { usePersistentState } from "../../lib/usePersistentState"
import { useSearchParams } from "react-router-dom"
import {
  api,
  STREAM_NOTICE_PREFIX,
  streamAgent,
  type MentorMessage,
} from "../../lib/api"
import { randomId } from "../../lib/randomId"
import { readScoped, sessionEpoch, writeScoped } from "../../lib/session"
import { persistExchange } from "./persistExchange"
import { Markdown } from "../../components/Markdown"
import { Button } from "../../components/ui"
import { Feedback } from "../../components/Feedback"

const SUGGESTIONS = [
  "Explain the doctrine of basic structure with key cases.",
  "Summarise the causes of the 1857 revolt.",
  "How should I structure my Mains answer writing practice?",
]

export function MentorChat() {
  const [messages, setMessages] = usePersistentState<MentorMessage[]>(
    "mentor:messages",
    [],
  )
  const [input, setInput] = usePersistentState("mentor:input", "")
  const [streaming, setStreaming] = useState(false)
  // Persisted so the next exchange after a reload joins the same saved
  // conversation in Session History; "New chat" resets it.
  const [conversationId, setConversationId] = usePersistentState<string | null>(
    "mentor:conversationId",
    null,
  )
  // Bumped by "New chat" so an in-flight save cannot restore the old conversation.
  const conversationGen = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [params, setParams] = useSearchParams()
  const bootRef = useRef(false)
  // Stable per-user thread id (stored in the signed-in user's namespace) so the
  // agent's server-side checkpointer keeps conversation memory across reloads.
  const threadId = useRef<string>(
    (() => {
      const KEY = "mentor:threadId"
      let id = readScoped(KEY)
      if (!id) {
        id = randomId()
        writeScoped(KEY, id)
      }
      return id
    })(),
  )

  // Heal an interrupted stream: a reload or post-verify redirect mid-answer
  // can persist a trailing assistant bubble with empty content, which would
  // otherwise render "Thinking…" forever. Drop that orphan on mount.
  useEffect(() => {
    setMessages((prev) =>
      prev.length > 0 &&
      prev[prev.length - 1].role === "assistant" &&
      !prev[prev.length - 1].content.trim()
        ? prev.slice(0, -1)
        : prev,
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (bootRef.current) return
    const q = params.get("q")
    const subject = params.get("subject")
    if (q) {
      bootRef.current = true
      setParams({}, { replace: true })
      void send(q)
    } else if (subject) {
      bootRef.current = true
      setParams({}, { replace: true })
      setInput("Help me with " + subject + " for UPSC - where should I start?")
    }
  }, [params, setParams])

  function scrollToBottom() {
    requestAnimationFrame(() => {
      const el = scrollRef.current
      if (el) el.scrollTo({ top: el.scrollHeight })
    })
  }

  // Persist the exchange so it appears in Session History. Best-effort only.
  function persist(question: string, answer: string) {
    return persistExchange(question, answer, {
      save: (input) => api.saveMessage(input),
      noticePrefix: STREAM_NOTICE_PREFIX,
      conversationId,
      generation: conversationGen,
      setConversationId,
    })
  }

  // Start a fresh conversation: rotate the server-side thread so the new chat
  // does NOT inherit the previous conversation's memory, then clear the view.
  function newChat() {
    if (streaming) return
    const id = randomId()
    writeScoped("mentor:threadId", id)
    threadId.current = id
    conversationGen.current += 1
    setConversationId(null)
    setMessages([])
    setInput("")
  }

  async function send(text?: string) {
    const q = (text ?? input).trim()
    if (!q || streaming) return
    const epoch = sessionEpoch()
    const history = messages
    setInput("")
    setMessages([
      ...history,
      { role: "user", content: q },
      { role: "assistant", content: "" },
    ])
    setStreaming(true)
    scrollToBottom()
    let answer = ""
    await streamAgent(
      "/agent/chat/stream",
      { question: q, thread_id: threadId.current },
      (piece) => {
        answer += piece
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + piece }
          }
          return next
        })
        scrollToBottom()
      },
    )
    // The session ended mid-stream: never save this exchange under another account.
    if (epoch !== sessionEpoch()) return
    setStreaming(false)
    void persist(q, answer)
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      {messages.length > 0 && (
        <div className="mb-2 flex justify-end">
          <button
            onClick={newChat}
            disabled={streaming}
            className="rounded-md border border-border px-3 py-1 text-xs text-muted transition hover:border-brand hover:text-fg disabled:opacity-50"
          >
            + New chat
          </button>
        </div>
      )}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto pb-4">
        {messages.length === 0 && (
          <div className="mt-16 text-center">
            <div className="mb-2 text-4xl">🧠</div>
            <p className="text-lg font-semibold text-fg">
              Ask your UPSC mentor anything
            </p>
            <p className="text-sm text-muted">
              Polity, history, current affairs, or strategy — you&apos;ll get clear,
              grounded answers.
            </p>
            <div className="mx-auto mt-6 flex max-w-xl flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="rounded-lg border border-border bg-surface px-4 py-2 text-left text-sm text-muted transition hover:border-brand hover:text-fg"
                  onClick={() => void send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          const isAssistant = m.role === "assistant"
          const isLast = i === messages.length - 1
          const question = i > 0 ? messages[i - 1].content : ""
          const showFeedback =
            isAssistant && m.content.length > 0 && !(isLast && streaming)
          return (
            <div
              key={i}
              className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                  m.role === "user"
                    ? "bg-brand text-white"
                    : "border border-border bg-surface"
                }`}
              >
                {isAssistant ? (
                  m.content ? (
                    <>
                      <Markdown>{m.content}</Markdown>
                      {showFeedback && (
                        <Feedback
                          agent="mentor"
                          question={question}
                          answer={m.content}
                        />
                      )}
                    </>
                  ) : isLast && streaming ? (
                    <span className="text-muted">Thinking…</span>
                  ) : (
                    <span className="text-muted">No response — please ask again.</span>
                  )
                ) : (
                  <span className="whitespace-pre-wrap">{m.content}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
      <div className="border-t border-border pt-3">
        <div className="flex items-end gap-2">
          <textarea
            className="input max-h-40 min-h-[48px] flex-1 resize-y"
            placeholder="Ask a question — press Enter to send, Shift+Enter for a new line"
            value={input}
            rows={1}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <Button onClick={() => void send()} disabled={streaming || !input.trim()}>
            {streaming ? "…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  )
}
