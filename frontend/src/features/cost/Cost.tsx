import { useQuery } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { api, type CostOverview } from "../../lib/api"
import { Card, Spinner, ErrorState } from "../../components/ui"

const BRAND_COLOR = "#7c3aed"
const LITE_COLOR = "#22d3ee"
const STRONG_COLOR = "#f87171"

const axisTick = { fontSize: 11, fill: "#9aa4bd" }
const barMargin = { top: 8, right: 8, left: -8, bottom: 0 }
const tooltipStyle = {
  background: "#141a2b",
  border: "1px solid #232a3d",
  borderRadius: 10,
  color: "#e6e9f0",
  fontSize: 12,
}
const tooltipLabelStyle = { color: "#9aa4bd" }
const barCursor = { fill: "#232a3d55" }

function rupees(n: number): string {
  return "\u20b9" + n.toLocaleString("en-IN", { maximumFractionDigits: 2 })
}

function compact(n: number): string {
  return n.toLocaleString("en-IN", { notation: "compact", maximumFractionDigits: 1 })
}

function pct(n: number): string {
  return (n * 100).toFixed(0) + "%"
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-3xl font-extrabold text-fg">{value}</span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </Card>
  )
}

function AgentSpendChart({ data }: { data: CostOverview["agents"] }) {
  const points = data.map((a) => ({ label: a.agent, cost: a.cost_inr }))
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={points} margin={barMargin}>
        <CartesianGrid stroke="#232a3d" vertical={false} />
        <XAxis
          dataKey="label"
          tick={axisTick}
          interval={0}
          angle={-15}
          height={48}
          textAnchor="end"
        />
        <YAxis tick={axisTick} />
        <Tooltip
          contentStyle={tooltipStyle}
          labelStyle={tooltipLabelStyle}
          cursor={barCursor}
          formatter={(v: number) => [rupees(v), "Est. cost"]}
        />
        <Bar dataKey="cost" radius={[6, 6, 0, 0]}>
          {points.map((_, i) => (
            <Cell key={i} fill={BRAND_COLOR} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function TierMixBar({ mix }: { mix: CostOverview["tier_mix"] }) {
  const total = mix.lite + mix.strong
  const liteW = total ? (mix.lite / total) * 100 : 0
  const liteStyle = { width: liteW + "%", background: LITE_COLOR }
  const strongStyle = { width: 100 - liteW + "%", background: STRONG_COLOR }
  const liteDot = { color: LITE_COLOR }
  const strongDot = { color: STRONG_COLOR }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-4 w-full overflow-hidden rounded-full bg-surface2">
        <div style={liteStyle} />
        <div style={strongStyle} />
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>
          <span style={liteDot}>●</span> LITE {mix.lite} ({pct(mix.lite_share)})
        </span>
        <span>
          <span style={strongDot}>●</span> STRONG {mix.strong}
        </span>
      </div>
    </div>
  )
}

export function Cost() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["cost-overview"],
    queryFn: () => api.cost(),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="grid min-h-[40vh] place-items-center">
        <Spinner label="Loading cost data…" />
      </div>
    )
  }

  if (isError) {
    const forbidden = (error as { status?: number })?.status === 403
    const msg = forbidden
      ? "You do not have access to the cost dashboard."
      : "Could not load cost data. Please try again."
    return (
      <ErrorState
        message={msg}
        onRetry={forbidden ? undefined : () => void refetch()}
      />
    )
  }

  const d = data as CostOverview
  const hasTraffic = d.totals.calls > 0

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-extrabold text-fg">Cost</h1>
        <span className="rounded-full bg-surface2 px-3 py-1 text-xs text-muted">
          Estimated · since last restart
        </span>
      </div>

      {!hasTraffic && (
        <Card className="text-center text-muted">
          No traffic recorded yet. Ask the mentor a few questions, then refresh to see
          estimated spend by model.
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="Estimated spend"
          value={rupees(d.totals.cost_inr)}
          hint={compact(d.totals.tokens) + " tokens · " + d.totals.calls + " calls"}
        />
        <StatCard
          label="Cache hit rate"
          value={pct(d.cache.hit_rate)}
          hint={
            d.cache.hit_exact +
            d.cache.hit_semantic +
            " hits · " +
            d.cache.miss +
            " misses"
          }
        />
        <StatCard
          label="LITE share"
          value={pct(d.tier_mix.lite_share)}
          hint="cheap-tier traffic"
        />
        <StatCard
          label="Saved by cache"
          value={rupees(d.cache.estimated_savings_inr)}
          hint="est. avoided spend"
        />
      </div>

      <Card className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-fg">Estimated spend by model</h2>
          <span className="text-xs text-muted">
            avg {rupees(d.totals.avg_cost_per_call_inr)}/call
          </span>
        </div>
        {d.agents.length ? (
          <AgentSpendChart data={d.agents} />
        ) : (
          <p className="py-8 text-center text-sm text-muted">No model activity yet.</p>
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <h2 className="font-bold text-fg">Model tier mix</h2>
          <p className="text-xs text-muted">
            Cheaper LITE-tier traffic lowers the blended rate. Routing (item #8) biases
            here.
          </p>
          <TierMixBar mix={d.tier_mix} />
        </Card>

        <Card className="flex flex-col gap-3">
          <h2 className="font-bold text-fg">Cache effectiveness</h2>
          <ul className="flex flex-col gap-1 text-sm text-fg">
            <li className="flex justify-between">
              <span className="text-muted">Exact hits</span>
              <span>{d.cache.hit_exact}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">Semantic hits</span>
              <span>{d.cache.hit_semantic}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">Misses</span>
              <span>{d.cache.miss}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">Skipped (volatile)</span>
              <span>{d.cache.skip}</span>
            </li>
          </ul>
        </Card>
      </div>

      <p className="text-center text-xs text-muted">
        Figures are estimates (heuristic token counts × list-price rates) for a spend
        gauge, not billing. Counters reset when the backend restarts.
      </p>
    </div>
  )
}
