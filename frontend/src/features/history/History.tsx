import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type Conversation } from "../../lib/api"
import { Card, Spinner } from "../../components/ui"
import { Markdown } from "../../components/Markdown"

function formatWhen(iso: string | null): string {
  if (!iso) return ""
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function ConversationView({ conversation }: { conversation: Conversation }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["messages", conversation.id],
    queryFn: () => api.conversationMessages(conversation.id),
  })

  if (isLoading) {
    return (
      <div className="grid place-items-center py-16">
        <Spinner />
      </div>
    )
  }
  if (isError || !data) {
    return (
      <p className="py-16 text-center text-muted">Could not load this conversation.</p>
    )
  }
  if (!data.messages.length) {
    return (
      <p className="py-16 text-center text-muted">This conversation has no messages.</p>
    )
  }

  return (
    <div className="space-y-4">
      {data.messages.map((m) => (
        <div
          key={m.id}
          className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
        >
          <div
            className={`max-w-[85%] rounded-2xl px-4 py-3 ${
              m.role === "user"
                ? "bg-brand text-white"
                : "border border-border bg-surface"
            }`}
          >
            {m.role === "assistant" ? (
              <Markdown>{m.content}</Markdown>
            ) : (
              <span className="whitespace-pre-wrap">{m.content}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export function History() {
  const [selected, setSelected] = useState<Conversation | null>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.conversations(),
  })

  if (isLoading) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <Card className="mx-auto max-w-md text-center">
        <p className="mb-3 text-muted">We couldn&apos;t load your history right now.</p>
        <button className="btn btn-brand" onClick={() => void refetch()}>
          Try again
        </button>
      </Card>
    )
  }

  const conversations = data.conversations

  if (selected) {
    return (
      <div className="space-y-4">
        <button className="btn btn-ghost text-sm" onClick={() => setSelected(null)}>
          ← Back to all sessions
        </button>
        <div>
          <h1 className="text-xl font-extrabold text-fg">
            {selected.title || "Untitled session"}
          </h1>
          <p className="text-xs text-muted">
            {selected.agent} · {formatWhen(selected.updated_at || selected.created_at)}
          </p>
        </div>
        <ConversationView conversation={selected} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Session history</h1>
        <p className="text-sm text-muted">
          Revisit your past conversations and answers.
        </p>
      </div>

      {conversations.length ? (
        <ul className="space-y-2">
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                className="card flex w-full items-center justify-between text-left transition hover:border-brand"
                onClick={() => setSelected(c)}
              >
                <div className="min-w-0">
                  <p className="truncate font-semibold text-fg">
                    {c.title || "Untitled session"}
                  </p>
                  <p className="text-xs capitalize text-muted">{c.agent}</p>
                </div>
                <span className="ml-3 shrink-0 text-xs text-muted">
                  {formatWhen(c.updated_at || c.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="py-16 text-center text-muted">
          <div className="mb-2 text-4xl">📝</div>
          <p className="text-lg font-semibold text-fg">No sessions yet</p>
          <p className="text-sm">
            Your Mentor conversations will appear here so you can pick up where you left
            off.
          </p>
        </div>
      )}
    </div>
  )
}
