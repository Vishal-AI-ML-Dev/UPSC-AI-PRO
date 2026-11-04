import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  api,
  type Conversation,
  type CurrentUser,
  type ProgressOverview,
  type SubjectMastery,
} from "../../lib/api"
import { Card, Spinner } from "../../components/ui"
import { useAuth } from "../../lib/auth"

const WEAK_COLOR = "#f87171"
const STRONG_COLOR = "#34d399"
const BRAND_COLOR = "#7c3aed"

const axisTick = { fontSize: 11, fill: "#9aa4bd" }
const areaMargin = { top: 8, right: 8, left: -16, bottom: 0 }
const barMargin = { top: 8, right: 8, left: -8, bottom: 0 }
const tooltipStyle = {
  background: "#141a2b",
  border: "1px solid #232a3d",
  borderRadius: 10,
  color: "#e6e9f0",
  fontSize: 12,
}
const tooltipLabelStyle = { color: "#9aa4bd" }

const AGENT_META: Record<string, { icon: string; label: string }> = {
  mentor: { icon: "\u{1F9E0}", label: "Mentor" },
  planner: { icon: "\u{1F5D3}\u{FE0F}", label: "Planner" },
  pyq: { icon: "\u2753", label: "PYQ Practice" },
  ncert: { icon: "\u{1F4DA}", label: "NCERT" },
  evaluator: { icon: "\u{1F4DD}", label: "Evaluator" },
  upload: { icon: "\u{1F4CE}", label: "Upload" },
  lecture: { icon: "\u{1F3A7}", label: "Lecture" },
  current_affairs: { icon: "\u{1F4F0}", label: "Current Affairs" },
}

function firstName(u: CurrentUser | null): string {
  if (!u) return "there"
  if (u.name) return u.name.split(/\s+/)[0]
  if (u.email) return u.email.split("@")[0]
  return "there"
}

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
}

function timeAgo(iso: string | null): string {
  if (!iso) return ""
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return "just now"
  const m = Math.floor(s / 60)
  if (m < 60) return m + "m ago"
  const h = Math.floor(m / 60)
  if (h < 24) return h + "h ago"
  return Math.floor(h / 24) + "d ago"
}

function ActivityChart({ data }: { data: ProgressOverview["streak"]["activity"] }) {
  const points = data.map((p) => ({ label: shortDate(p.date), count: p.count }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={points} margin={areaMargin}>
        <defs>
          <linearGradient id="activityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={BRAND_COLOR} stopOpacity={0.5} />
            <stop offset="100%" stopColor={BRAND_COLOR} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#232a3d" vertical={false} />
        <XAxis
          dataKey="label"
          tick={axisTick}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis tick={axisTick} allowDecimals={false} width={28} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} />
        <Area
          type="monotone"
          dataKey="count"
          name="Questions"
          stroke={BRAND_COLOR}
          strokeWidth={2}
          fill="url(#activityFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function pct(fraction: number): string {
  return Math.round(fraction * 100) + "%"
}

function plural(n: number, word: string): string {
  return n + " " + word + (n === 1 ? "" : "s")
}

function MasteryChart({
  subjects,
  weak,
}: {
  subjects: SubjectMastery[]
  weak: SubjectMastery[]
}) {
  const weakSet = new Set(weak.map((w) => w.subject))
  const data = subjects.slice(0, 12).map((s) => ({
    topic: s.subject,
    pct: Math.round(s.mastery * 100),
    weak: weakSet.has(s.subject),
  }))
  return (
    <ResponsiveContainer width="100%" height={Math.max(220, data.length * 34)}>
      <BarChart data={data} layout="vertical" margin={barMargin}>
        <CartesianGrid stroke="#232a3d" horizontal={false} />
        <XAxis type="number" domain={[0, 100]} tick={axisTick} unit="%" />
        <YAxis type="category" dataKey="topic" tick={axisTick} width={150} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} />
        <Bar dataKey="pct" name="Mastery" radius={[0, 6, 6, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.weak ? WEAK_COLOR : STRONG_COLOR} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: string
  label: string
  value: string
  hint?: string
}) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-xl">{icon}</span>
      <span className="text-2xl font-extrabold text-fg">{value}</span>
      <span className="text-sm text-muted">{label}</span>
      {hint && <span className="text-xs text-muted/70">{hint}</span>}
    </Card>
  )
}

export function Dashboard() {
  const { user } = useAuth()
  const progressQ = useQuery({
    queryKey: ["progress"],
    queryFn: () => api.progress(),
    retry: false,
  })
  const convQ = useQuery({
    queryKey: ["conversations-recent"],
    queryFn: () => api.conversations(),
    retry: false,
  })

  const data = progressQ.data
  const accuracyPct = data ? Math.round(data.questions.accuracy * 100) : 0
  const evals = data?.evaluations
  const conversations: Conversation[] = (convQ.data?.conversations ?? []).slice(0, 6)

  const stats = data
    ? [
        {
          icon: "\u2753",
          label: "Mentor questions",
          value: String(data.totals.questions_asked),
          hint: "Mentor chat only",
        },
        {
          icon: "\u{1F3AF}",
          label: "Helpful-answer rate",
          value: data.questions.total ? accuracyPct + "%" : "\u2014",
          hint: data.questions.total
            ? data.questions.correct +
              " of " +
              data.questions.total +
              " answers rated \u{1F44D}"
            : "no ratings yet",
        },
        {
          icon: "\u{1F525}",
          label: "Mentor streak",
          value: data.streak.current + (data.streak.current === 1 ? " day" : " days"),
          hint: "days in a row with Mentor chat",
        },
        {
          icon: "\u{1F4C5}",
          label: "Mentor active days",
          value: String(data.totals.active_days),
          hint: "days with Mentor chat",
        },
        {
          icon: "\u{1F4DD}",
          label: "Average Evaluation Score",
          value: evals && evals.scored ? pct(evals.average_score_pct) : "—",
          hint:
            evals && evals.scored
              ? "evaluator score across " + plural(evals.scored, "scored answer")
              : "no scored evaluations yet",
        },
        {
          icon: "\u{1F501}",
          label: "Due for revision",
          value: plural(data.revision.due, "subject"),
          hint: data.revision.total
            ? "of " + plural(data.revision.total, "evaluated subject")
            : "tag a subject when you evaluate an answer",
        },
      ]
    : []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-fg">Hi {firstName(user)}</h1>
        <p className="text-sm text-muted">Here is your UPSC prep at a glance.</p>
      </div>

      {progressQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : !data ? (
        <Card>
          <p className="text-sm text-muted">Progress data is unavailable right now.</p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {stats.map((s) => (
              <StatCard
                key={s.label}
                icon={s.icon}
                label={s.label}
                value={s.value}
                hint={s.hint}
              />
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="mb-3 text-sm font-semibold text-fg">Recent activity</h2>
              {conversations.length === 0 ? (
                <p className="text-sm text-muted">No conversations yet.</p>
              ) : (
                <ul className="space-y-1">
                  {conversations.map((c) => {
                    const meta = AGENT_META[c.agent] ?? {
                      icon: "\u{1F4AC}",
                      label: c.agent,
                    }
                    return (
                      <li key={c.id}>
                        <Link
                          to="/app/history"
                          className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface2"
                        >
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-surface2 text-base">
                            {meta.icon}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-fg">
                              {c.title}
                            </span>
                            <span className="block text-xs text-muted">
                              {meta.label} {"\u00b7"}{" "}
                              {timeAgo(c.updated_at || c.created_at)}
                            </span>
                          </span>
                        </Link>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Card>

            <Card>
              <h2 className="mb-3 text-sm font-semibold text-fg">Subject mastery</h2>
              {data.subjects.length === 0 ? (
                <p className="text-sm text-muted">
                  Tag a subject when you evaluate an answer to see subject mastery.
                </p>
              ) : (
                <MasteryChart subjects={data.subjects} weak={data.weak_subjects} />
              )}
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="mb-3 text-sm font-semibold text-fg">Weak subjects</h2>
              {data.weak_subjects.length === 0 ? (
                <p className="text-sm text-muted">
                  No weak subjects. A subject is weak after 3 or more scored answers
                  averaging below 60%.
                </p>
              ) : (
                <ul className="space-y-1">
                  {data.weak_subjects.map((w) => (
                    <li
                      key={w.subject}
                      className="flex items-center justify-between rounded-lg px-2 py-2 text-sm"
                    >
                      <span className="text-fg">{w.subject}</span>
                      <span className="text-muted">
                        {pct(w.mastery)} {"·"} {plural(w.attempts, "answer")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <h2 className="mb-3 text-sm font-semibold text-fg">
                Due for revision: {plural(data.revision.due, "subject")}
              </h2>
              {data.revision.subjects.length === 0 ? (
                <p className="text-sm text-muted">
                  Tag a subject when you evaluate an answer to get a revision schedule.
                </p>
              ) : (
                <ul className="space-y-1">
                  {data.revision.subjects.map((r) => (
                    <li
                      key={r.subject}
                      className="flex items-center justify-between rounded-lg px-2 py-2 text-sm"
                    >
                      <span className="text-fg">{r.subject}</span>
                      <span className={r.due ? "font-semibold text-fg" : "text-muted"}>
                        {r.due ? "Due now" : "Next: " + r.next_due}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card>
            <h2 className="mb-3 text-sm font-semibold text-fg">
              Mentor activity (last 30 days)
            </h2>
            {data.streak.activity.length === 0 ? (
              <p className="text-sm text-muted">No activity recorded yet.</p>
            ) : (
              <ActivityChart data={data.streak.activity} />
            )}
          </Card>
        </>
      )}
    </div>
  )
}
