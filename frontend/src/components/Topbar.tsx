import { useEffect, useRef, useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api, type CurrentUser } from "../lib/api"
import { useAuth } from "../lib/auth"
import { ThemeToggle } from "./ThemeToggle"

function initials(u: CurrentUser | null): string {
  const src = (u?.name || u?.email || "U").trim()
  const parts = src.split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return src.slice(0, 2).toUpperCase()
}

export function Topbar({ onMenuClick }: { onMenuClick: () => void }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [q, setQ] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: progress } = useQuery({
    queryKey: ["progress"],
    queryFn: () => api.progress(),
    staleTime: 60 * 1000,
    retry: false,
  })

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  function submit(e: FormEvent) {
    e.preventDefault()
    const t = q.trim()
    if (!t) return
    setQ("")
    navigate("/app/mentor?q=" + encodeURIComponent(t))
  }

  const streak = progress?.streak.current ?? 0

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/80 backdrop-blur">
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          onClick={onMenuClick}
          className="grid h-9 w-9 place-items-center rounded-lg text-muted hover:bg-surface2 hover:text-fg lg:hidden"
          aria-label="Open menu"
        >
          {"\u2630"}
        </button>

        <form onSubmit={submit} className="relative flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted">
            {"\u{1F50D}"}
          </span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask the Mentor anything\u2026 (press Enter)"
            title="Type a question and press Enter \u2014 the AI Mentor will answer it"
            className="input w-full pl-9 pr-14"
          />
          <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border border-border bg-surface2 px-1.5 py-0.5 text-[10px] text-muted sm:block">
            Ctrl K
          </kbd>
        </form>

        <div className="flex shrink-0 items-center gap-2">
          <span
            className="hidden items-center gap-1 rounded-full border border-border bg-surface2 px-3 py-1.5 text-sm font-semibold sm:inline-flex"
            title="Current streak"
          >
            <span>{"\u{1F525}"}</span>
            <span>{streak}</span>
            <span className="text-xs font-normal text-muted">
              {streak === 1 ? "day" : "days"}
            </span>
          </span>
          <ThemeToggle />
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-full bg-brand text-sm font-bold text-white">
              {initials(user)}
            </span>
            <span className="hidden text-sm font-medium sm:inline">
              {user?.name || user?.email}
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
