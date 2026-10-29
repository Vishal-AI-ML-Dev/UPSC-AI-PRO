import {
  useCallback,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react"
import { Link, NavLink } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import { useAuth } from "../lib/auth"
import { describeApiBase, OVERRIDE_RULE, setApiBase } from "../lib/config"
import { Modal, Button, Input } from "./ui"

type Item = { to: string; icon: string; label: string }

const TOOLS: Item[] = [
  { to: "/app/mentor", icon: "\u{1F9E0}", label: "Mentor" },
  { to: "/app/planner", icon: "\u{1F5D3}\u{FE0F}", label: "Planner" },
  { to: "/app/pyq", icon: "\u2753", label: "PYQ Practice" },
  { to: "/app/ncert", icon: "\u{1F4DA}", label: "NCERT Library" },
  { to: "/app/upload", icon: "\u{1F4CE}", label: "Notes & Uploads" },
  { to: "/app/lecture", icon: "\u{1F3A7}", label: "Lecture Notes" },
  { to: "/app/current-affairs", icon: "\u{1F4F0}", label: "Current Affairs" },
  { to: "/app/evaluator", icon: "\u{1F4DD}", label: "Evaluator" },
]

const SPACE: Item[] = [
  { to: "/app/dashboard", icon: "\u{1F4CA}", label: "Dashboard" },
  { to: "/app/history", icon: "\u{1F550}", label: "History" },
]

const itemBase =
  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"

function linkClass(isActive: boolean, collapsed: boolean): string {
  return (
    itemBase +
    (collapsed ? " justify-center px-2" : "") +
    (isActive
      ? " bg-brand text-white shadow-card"
      : " text-muted hover:bg-surface2 hover:text-fg")
  )
}

function initials(u: { name?: string; email?: string } | null): string {
  const src = (u?.name || u?.email || "U").trim()
  const parts = src.split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return src.slice(0, 2).toUpperCase()
}

function SectionLabel({ children }: { children: string }) {
  return (
    <p className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-muted/70">
      {children}
    </p>
  )
}

export function Sidebar({
  open,
  onClose,
  collapsed = false,
  width = 256,
  onToggleCollapse,
  onResize,
}: {
  open: boolean
  onClose: () => void
  collapsed?: boolean
  width?: number
  onToggleCollapse?: () => void
  onResize?: (w: number) => void
}) {
  const { logout, user } = useAuth()
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
  const { data: monAccess } = useQuery({
    queryKey: ["monitoring-access"],
    queryFn: () => api.monitoringAccess(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const space: Item[] = [
    ...SPACE,
    ...(access?.admin ? [{ to: "/app/cost", icon: "\u{1F4B0}", label: "Cost" }] : []),
    ...(expAccess?.admin
      ? [{ to: "/app/experiments", icon: "\u{1F9EA}", label: "Experiments" }]
      : []),
    ...(monAccess?.admin
      ? [{ to: "/app/monitoring", icon: "\u{1F4E1}", label: "Monitoring" }]
      : []),
  ]

  const [backendOpen, setBackendOpen] = useState(false)
  const [backendUrl, setBackendUrl] = useState("")

  function openBackend() {
    setBackendUrl(describeApiBase())
    setBackendOpen(true)
  }
  function saveBackend() {
    const v = backendUrl.trim()
    if (v) {
      if (setApiBase(v)) window.location.reload()
      else window.alert("This backend URL is not allowed. " + OVERRIDE_RULE)
    }
  }

  // Drag-to-resize: while the mouse is down we track its X position and report
  // it back to the parent, which clamps + persists the width.
  const dragging = useRef(false)
  const startResize = useCallback(
    (e: ReactMouseEvent) => {
      if (!onResize) return
      e.preventDefault()
      dragging.current = true
      const move = (ev: globalThis.MouseEvent) => {
        if (dragging.current) onResize(ev.clientX)
      }
      const up = () => {
        dragging.current = false
        document.removeEventListener("mousemove", move)
        document.removeEventListener("mouseup", up)
        document.body.style.userSelect = ""
      }
      document.body.style.userSelect = "none"
      document.addEventListener("mousemove", move)
      document.addEventListener("mouseup", up)
    },
    [onResize],
  )

  const renderItem = (t: Item) => (
    <NavLink
      key={t.to}
      to={t.to}
      onClick={onClose}
      title={collapsed ? t.label : undefined}
      className={({ isActive }) => linkClass(isActive, collapsed)}
    >
      <span className="w-5 shrink-0 text-center">{t.icon}</span>
      {!collapsed && <span className="truncate">{t.label}</span>}
    </NavLink>
  )

  const btnClass =
    itemBase +
    (collapsed ? " justify-center px-2" : "") +
    " w-full text-muted hover:bg-surface2 hover:text-fg"

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        style={{ width }}
        className={
          "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-surface transition-transform duration-200 lg:translate-x-0 " +
          (open ? "translate-x-0" : "-translate-x-full")
        }
      >
        {/* Brand */}
        <Link
          to="/"
          title="Go to UPSC AI home"
          className={
            "flex items-center gap-2 border-b border-border px-4 py-4 transition hover:bg-surface2/60 " +
            (collapsed ? "justify-center px-2" : "")
          }
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brandgrad text-lg font-extrabold text-white">
            U
          </span>
          {!collapsed && (
            <div className="leading-tight">
              <div className="font-extrabold">
                UPSC<span className="text-brand-400">AI</span>
              </div>
              <div className="text-[11px] text-muted">Your Personal Mentor</div>
            </div>
          )}
        </Link>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-2 pb-4 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {collapsed ? (
            <div className="pt-3" />
          ) : (
            <SectionLabel>Study Tools</SectionLabel>
          )}
          {TOOLS.map(renderItem)}

          {collapsed ? (
            <div className="my-3 border-t border-border" />
          ) : (
            <SectionLabel>My Space</SectionLabel>
          )}
          {space.map(renderItem)}

          {collapsed ? (
            <div className="my-3 border-t border-border" />
          ) : (
            <SectionLabel>Account</SectionLabel>
          )}
          {/* Signed-in user profile */}
          <div
            className={
              "flex items-center gap-3 rounded-lg px-3 py-2 " +
              (collapsed ? "justify-center px-2" : "")
            }
            title={collapsed ? user?.name || user?.email || "Account" : undefined}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brandgrad text-xs font-bold text-white">
              {initials(user)}
            </span>
            {!collapsed && (
              <div className="min-w-0 leading-tight">
                <div className="truncate text-sm font-semibold text-fg">
                  {user?.name || "UPSC Aspirant"}
                </div>
                {user?.email && (
                  <div className="truncate text-xs text-muted">{user.email}</div>
                )}
              </div>
            )}
          </div>
          {access?.admin && (
            <button
              onClick={openBackend}
              title={collapsed ? "Backend URL" : undefined}
              className={btnClass}
            >
              <span className="w-5 shrink-0 text-center">{"\u2699\u{FE0F}"}</span>
              {!collapsed && <span>Backend URL</span>}
            </button>
          )}
          <button
            onClick={() => void logout()}
            title={collapsed ? "Logout" : undefined}
            className={btnClass}
          >
            <span className="w-5 shrink-0 text-center">{"\u{1F6AA}"}</span>
            {!collapsed && <span>Logout</span>}
          </button>
        </nav>

        {/* Collapse / expand toggle (desktop only) */}
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={
              "hidden items-center gap-2 border-t border-border px-3 py-3 text-sm font-medium text-muted hover:bg-surface2 hover:text-fg lg:flex " +
              (collapsed ? "justify-center" : "")
            }
          >
            <span>{collapsed ? "\u00BB" : "\u00AB"}</span>
            {!collapsed && <span>Collapse</span>}
          </button>
        )}

        {/* Drag handle to resize (desktop only, hidden when collapsed) */}
        {!collapsed && onResize && (
          <div
            onMouseDown={startResize}
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize"
            className="absolute inset-y-0 right-0 hidden w-1.5 cursor-col-resize bg-transparent transition-colors hover:bg-brand/40 lg:block"
          />
        )}
      </aside>

      <Modal
        open={backendOpen}
        title="Backend API URL"
        onClose={() => setBackendOpen(false)}
      >
        <label className="mb-1 block text-sm text-muted" htmlFor="backend-url">
          Base URL
        </label>
        <Input
          id="backend-url"
          value={backendUrl}
          onChange={(e) => setBackendUrl(e.target.value)}
          placeholder="https://api.example.com/api/v1"
          autoFocus
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setBackendOpen(false)}>
            Cancel
          </Button>
          <Button onClick={saveBackend}>Save and reload</Button>
        </div>
      </Modal>
    </>
  )
}
