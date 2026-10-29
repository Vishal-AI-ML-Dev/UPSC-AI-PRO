import { useState } from "react"
import { api, type FeedbackInput } from "../lib/api"

interface FeedbackProps {
  agent?: string
  question?: string
  answer?: string
}

type Rating = "up" | "down"

/**
 * Lightweight thumbs up/down control shown under a completed answer.
 * A downvote reveals an optional comment box. Submission is best-effort and
 * silent - the student sees a small acknowledgement, nothing more.
 */
export function Feedback({
  agent = "mentor",
  question = "",
  answer = "",
}: FeedbackProps) {
  const [rating, setRating] = useState<Rating | null>(null)
  const [comment, setComment] = useState("")
  const [showComment, setShowComment] = useState(false)
  const [done, setDone] = useState(false)

  function send(next: Rating, withComment: string) {
    const payload: FeedbackInput = {
      rating: next,
      agent,
      question,
      answer,
      comment: withComment,
    }
    void api.submitFeedback(payload).catch(() => {
      // best-effort: never interrupt the study flow on a feedback error
    })
  }

  function choose(next: Rating) {
    setRating(next)
    if (next === "up") {
      setShowComment(false)
      setDone(true)
      send("up", "")
    } else {
      setShowComment(true)
    }
  }

  function submitComment() {
    setDone(true)
    setShowComment(false)
    send("down", comment.trim())
  }

  if (done) {
    return (
      <p className="mt-2 text-xs text-muted">
        Thanks for the feedback — it helps improve future answers.
      </p>
    )
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2 text-muted">
        <span className="text-xs">Was this helpful?</span>
        <button
          className={`rounded-md px-2 py-1 text-sm hover:bg-surface2 ${
            rating === "up" ? "text-emerald-400" : ""
          }`}
          title="Helpful"
          onClick={() => choose("up")}
        >
          👍
        </button>
        <button
          className={`rounded-md px-2 py-1 text-sm hover:bg-surface2 ${
            rating === "down" ? "text-red-400" : ""
          }`}
          title="Not helpful"
          onClick={() => choose("down")}
        >
          👎
        </button>
      </div>
      {showComment && (
        <div className="mt-2 flex items-start gap-2">
          <textarea
            className="input min-h-[38px] flex-1 resize-y text-sm"
            placeholder="Optional: what was missing or wrong? (e.g. needed more depth, wrong facts)"
            value={comment}
            rows={2}
            onChange={(e) => setComment(e.target.value)}
          />
          <button className="btn btn-ghost text-sm" onClick={submitComment}>
            Submit
          </button>
        </div>
      )}
    </div>
  )
}
