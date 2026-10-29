import { Suspense, useEffect, useState } from "react"
import { lazyWithRetry } from "../lib/lazyWithRetry"
import { Navigate, Route, Routes } from "react-router-dom"
import { Sidebar } from "../components/Sidebar"
import { Topbar } from "../components/Topbar"
import { Spinner } from "../components/ui"
import { ErrorBoundary } from "../components/ErrorBoundary"
import { NotFound } from "./NotFound"
import { usePersistentState } from "../lib/usePersistentState"

// Route-level code splitting: each feature ships as its own lazy chunk, so the
// initial app bundle stays small and a feature's JS is only fetched the first
// time it is visited. Layout chrome (Sidebar/Topbar) stays eager.
const MentorChat = lazyWithRetry(() =>
  import("../features/mentor/MentorChat").then((m) => ({ default: m.MentorChat })),
)
const Dashboard = lazyWithRetry(() =>
  import("../features/dashboard/Dashboard").then((m) => ({ default: m.Dashboard })),
)
const History = lazyWithRetry(() =>
  import("../features/history/History").then((m) => ({ default: m.History })),
)
const Evaluator = lazyWithRetry(() =>
  import("../features/evaluator/Evaluator").then((m) => ({ default: m.Evaluator })),
)
const Cost = lazyWithRetry(() =>
  import("../features/cost/Cost").then((m) => ({ default: m.Cost })),
)
const Experiments = lazyWithRetry(() => import("../features/experiments/Experiments"))
const Monitoring = lazyWithRetry(() =>
  import("../features/monitoring/Monitoring").then((m) => ({ default: m.Monitoring })),
)
const Planner = lazyWithRetry(() =>
  import("../features/planner/Planner").then((m) => ({ default: m.Planner })),
)
const Pyq = lazyWithRetry(() =>
  import("../features/pyq/Pyq").then((m) => ({ default: m.Pyq })),
)
const Ncert = lazyWithRetry(() =>
  import("../features/ncert/Ncert").then((m) => ({ default: m.Ncert })),
)
const CurrentAffairs = lazyWithRetry(() =>
  import("../features/current_affairs/CurrentAffairs").then((m) => ({
    default: m.CurrentAffairs,
  })),
)
const Lecture = lazyWithRetry(() =>
  import("../features/lecture/Lecture").then((m) => ({ default: m.Lecture })),
)
const Upload = lazyWithRetry(() =>
  import("../features/upload/Upload").then((m) => ({ default: m.Upload })),
)

const COLLAPSED_W = 76
const MIN_W = 208
const MAX_W = 420

export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  // Persisted, user-resizable sidebar width + collapsed state (desktop only).
  const [width, setWidth] = usePersistentState<number>("sidebar:width", 256)
  const [collapsed, setCollapsed] = usePersistentState<boolean>(
    "sidebar:collapsed",
    false,
  )
  const [isDesktop, setIsDesktop] = useState<boolean>(() =>
    typeof window !== "undefined"
      ? window.matchMedia("(min-width:1024px)").matches
      : true,
  )

  useEffect(() => {
    const mq = window.matchMedia("(min-width:1024px)")
    const handler = () => setIsDesktop(mq.matches)
    handler()
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])

  // Rail width on desktop reflects collapse; mobile always uses a comfy width.
  const railWidth = collapsed ? COLLAPSED_W : width
  const asideWidth = isDesktop ? railWidth : 272

  return (
    <div className="min-h-screen">
      {/* Shift the main content by the current rail width on desktop only. */}
      <style>{`@media (min-width:1024px){.main-shift{padding-left:${railWidth}px}}`}</style>
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={isDesktop && collapsed}
        width={asideWidth}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        onResize={(w) => setWidth(Math.min(MAX_W, Math.max(MIN_W, Math.round(w))))}
      />
      <div className="main-shift flex min-h-screen flex-col transition-[padding] duration-200">
        <Topbar onMenuClick={() => setSidebarOpen(true)} />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
          <ErrorBoundary>
            <Suspense
              fallback={
                <div className="grid place-items-center py-20">
                  <Spinner label="Loading\u2026" />
                </div>
              }
            >
              <Routes>
                <Route index element={<Navigate to="dashboard" replace />} />
                <Route path="mentor" element={<MentorChat />} />
                <Route path="evaluator" element={<Evaluator />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="history" element={<History />} />
                <Route path="cost" element={<Cost />} />
                <Route path="experiments" element={<Experiments />} />
                <Route path="monitoring" element={<Monitoring />} />
                <Route path="planner" element={<Planner />} />
                <Route path="pyq" element={<Pyq />} />
                <Route path="ncert" element={<Ncert />} />
                <Route path="current-affairs" element={<CurrentAffairs />} />
                <Route path="lecture" element={<Lecture />} />
                <Route path="upload" element={<Upload />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
