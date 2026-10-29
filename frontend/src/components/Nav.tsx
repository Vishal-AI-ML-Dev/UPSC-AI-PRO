import { useQuery } from "@tanstack/react-query"
import { Link, NavLink } from "react-router-dom"
import { api } from "../lib/api"
import { useAuth } from "../lib/auth"
import { describeApiBase, OVERRIDE_RULE, setApiBase } from "../lib/config"

const TABS = [
  { to: "/app/mentor", label: "🧠 Mentor" },
  { to: "/app/planner", label: "🗓️ Planner" },
  { to: "/app/pyq", label: "❓ PYQ" },
  { to: "/app/ncert", label: "📚 NCERT" },
  { to: "/app/current-affairs", label: "📰 Current Affairs" },
  { to: "/app/lecture", label: "🎧 Lecture" },
  { to: "/app/upload", label: "📎 Upload" },
  { to: "/app/evaluator", label: "📝 Evaluator" },
  { to: "/app/dashboard", label: "📊 Dashboard" },
  { to: "/app/history", label: "🕘 History" },
]

export function Nav() {
  const { user, logout } = useAuth()
  const { data: access } = useQuery({
    queryKey: ["cost-access"],
    queryFn: () => api.costAccess(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const { data: expAccess } = useQuery({
    queryKey: ["experiments-access"],
    queryFn: () => api.experimentsAccess(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const tabs = [
    ...TABS,
    ...(access?.admin ? [{ to: "/app/cost", label: "💰 Cost" }] : []),
    ...(expAccess?.admin ? [{ to: "/app/experiments", label: "🧪 Experiments" }] : []),
  ]
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-bg/80 backdrop-blur">
      <div className="mx-auto flex max-w-screen-2xl items-center gap-3 px-4 py-3">
        <Link
          to="/"
          title="Go to UPSC AI home"
          className="flex shrink-0 items-center gap-2 font-extrabold transition hover:opacity-80"
        >
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white">
            U
          </span>
          <span>
            UPSC<span className="text-brand-400">AI</span>
          </span>
        </Link>
        <nav className="flex flex-1 items-center gap-1 overflow-x-auto whitespace-nowrap [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {tabs.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) =>
                `shrink-0 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-surface2 text-fg"
                    : "text-muted hover:bg-surface2/60 hover:text-fg"
                }`
              }
            >
              {t.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex shrink-0 items-center gap-3">
          <button
            className="text-sm text-muted hover:text-fg"
            title="Backend URL settings"
            onClick={() => {
              const v = window.prompt("Backend API base URL:", describeApiBase())
              if (v) {
                if (setApiBase(v)) window.location.reload()
                else window.alert("This backend URL is not allowed. " + OVERRIDE_RULE)
              }
            }}
          >
            ⚙️
          </button>
          <span className="hidden text-sm text-muted sm:inline">
            {user?.name || user?.email}
          </span>
          <button
            className="text-sm text-muted hover:text-fg"
            onClick={() => void logout()}
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  )
}
