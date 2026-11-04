// Saves one Mentor exchange to Session History (best-effort). Kept free of React
// and imports so the logic can be checked on its own.

export interface SaveMessageInput {
  role: "user" | "assistant"
  content: string
  agent: string
  conversation_id: string | null
  title?: string
}

export interface PersistExchangeDeps {
  save: (input: SaveMessageInput) => Promise<{ conversation_id: string }>
  // STREAM_NOTICE_PREFIX from lib/api.ts (passed in to keep this module import-free).
  noticePrefix: string
  // The conversation the next exchange should join (null starts a new one).
  conversationId: string | null
  // Bumped by "New chat". A save that started under an older generation must not
  // restore its conversation id after the reset.
  generation: { current: number }
  setConversationId: (id: string) => void
}

export async function persistExchange(
  question: string,
  answer: string,
  deps: PersistExchangeDeps,
): Promise<void> {
  // An empty answer or a stream notice (error / stop / session message) is not an
  // answer, so neither the question nor the notice is saved.
  if (!answer.trim() || answer.includes(deps.noticePrefix)) return
  const started = deps.generation.current
  try {
    const title = question.length > 60 ? question.slice(0, 57) + "…" : question
    const saved = await deps.save({
      role: "user",
      content: question,
      agent: "mentor",
      conversation_id: deps.conversationId,
      title,
    })
    if (deps.generation.current === started)
      deps.setConversationId(saved.conversation_id)
    // The reply always joins the conversation its question was saved to.
    await deps.save({
      role: "assistant",
      content: answer,
      agent: "mentor",
      conversation_id: saved.conversation_id,
    })
  } catch {
    // history persistence is non-critical; ignore failures
  }
}
