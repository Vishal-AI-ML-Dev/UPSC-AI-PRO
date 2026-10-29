import { useQuery } from "@tanstack/react-query"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { api, type MonitoringOverview } from "../../lib/api"
import { Card, Spinner, ErrorState } from "../../components/ui"

const BRAND_COLOR = "#7c3aed"
const DANGER_COLOR = "#f87171"

const axisTick = { fontSize: 11, fill: "#9aa4bd" }
const areaMargin = { top: 8, right: 8, left: -16, bottom: 0 }
const tooltipStyle = {
  background: "#141a2b",
  border: "1px solid #232a3d",
  borderRadius: 10,
  color: "#e6e9f0",
  fontSize: 12,
}
const tooltipLabelStyle = { color: "#9aa4bd" }

function pct(n: number): string {
  return (n * 100).toFixed(2) + "%"
}

function ms(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(2) + " s"
  return Math.round(n) + " ms"
}

function uptime(sec: number): string {
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return d + "d " + h + "h"
  if (h > 0) return h + "h " + m + "m"
  if (m > 0) return m + "m"
  return sec + "s"
}

function hourLabel(iso: string): string {
  const t = iso.split("T")[1] || iso
  return t.slice(0, 5)
}

function StatCard({
  label,
  value,
  hint,
  danger,
}: {
  label: string
  value: string
  hint?: string
  danger?: boolean
}) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-sm text-muted">{label}</span>
      <span
        className={"text-3xl font-extrabold " + (danger ? "text-danger" : "text-fg")}
      >
        {value}
      </span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </Card>
  )
}

function RequestsChart({ data }: { data: MonitoringOverview["hourly"] }) {
  const points = data.map((p) => ({
    label: hourLabel(p.hour),
    Requests: p.count,
    Errors: p.errors,
  }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={points} margin={areaMargin}>
        <defs>
          <linearGradient id="reqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={BRAND_COLOR} stopOpacity={0.5} />
            <stop offset="100%" stopColor={BRAND_COLOR} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#232a3d" vertical={false} />
        <XAxis
          dataKey="label"
          tick={axisTick}
          interval="preserveStartEnd"
          minTickGap={20}
        />
        <YAxis tick={axisTick} allowDecimals={false} width={30} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} />
        <Area
          type="monotone"
          dataKey="Requests"
          stroke={BRAND_COLOR}
          strokeWidth={2}
          fill="url(#reqFill)"
        />
        <Area
          type="monotone"
          dataKey="Errors"
          stroke={DANGER_COLOR}
          strokeWidth={2}
          fillOpacity={0}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function Monitoring() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["monitoring-overview"],
    queryFn: () => api.monitoring(),
    retry: false,
    refetchInterval: 15000,
  })

  if (isLoading) {
    return (
      <div className="grid min-h-[40vh] place-items-center">
        <Spinner label={"Loading monitoring data\u2026"} />
      </div>
    )
  }

  if (isError) {
    const forbidden = (error as { status?: number })?.status === 403
    const msg = forbidden
      ? "You do not have access to the monitoring dashboard."
      : "Could not load monitoring data. Please try again."
    return (
      <ErrorState
        message={msg}
        onRetry={forbidden ? undefined : () => void refetch()}
      />
    )
  }

  const d = data as MonitoringOverview
  const hasTraffic = d.total_requests > 0
  const lat = d.latency_ms
  const sc = d.status_classes

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-extrabold text-fg">Monitoring</h1>
        <span className="rounded-full bg-surface2 px-3 py-1 text-xs text-muted">
          Live {"\u00b7"} since last restart
        </span>
      </div>

      {!hasTraffic && (
        <Card className="text-center text-muted">
          No requests recorded yet. Use the app for a bit, then refresh to see latency,
          throughput, and error rate.
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        <StatCard
          label="Uptime"
          value={uptime(d.uptime_seconds)}
          hint="since restart"
        />
        <StatCard
          label="Requests"
          value={d.total_requests.toLocaleString("en-IN")}
          hint={d.rps + " req/s avg"}
        />
        <StatCard label="p95 latency" value={ms(lat.p95)} hint={"p50 " + ms(lat.p50)} />
        <StatCard label="p99 latency" value={ms(lat.p99)} hint={"max " + ms(lat.max)} />
        <StatCard
          label="Error rate"
          value={pct(d.error_rate)}
          hint="5xx responses"
          danger={d.error_rate > 0}
        />
      </div>

      <Card className="flex flex-col gap-3">
        <h2 className="font-bold text-fg">Requests (last 24h)</h2>
        <RequestsChart data={d.hourly} />
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <h2 className="font-bold text-fg">Latency percentiles</h2>
          <ul className="flex flex-col gap-1 text-sm text-fg">
            <li className="flex justify-between">
              <span className="text-muted">p50 (median)</span>
              <span>{ms(lat.p50)}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">p95</span>
              <span>{ms(lat.p95)}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">p99</span>
              <span>{ms(lat.p99)}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">Average</span>
              <span>{ms(lat.avg)}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">Max</span>
              <span>{ms(lat.max)}</span>
            </li>
          </ul>
        </Card>

        <Card className="flex flex-col gap-3">
          <h2 className="font-bold text-fg">Status codes</h2>
          <ul className="flex flex-col gap-1 text-sm text-fg">
            <li className="flex justify-between">
              <span className="text-muted">2xx success</span>
              <span>{sc["2xx"] || 0}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">3xx redirect</span>
              <span>{sc["3xx"] || 0}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">4xx client</span>
              <span>{sc["4xx"] || 0}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-muted">5xx server</span>
              <span className={sc["5xx"] ? "text-danger" : ""}>{sc["5xx"] || 0}</span>
            </li>
          </ul>
        </Card>
      </div>

      <Card className="flex flex-col gap-3">
        <h2 className="font-bold text-fg">Top endpoints</h2>
        {d.endpoints.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">
            No endpoint activity yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted">
                  <th className="py-2 pr-4 font-semibold">Endpoint</th>
                  <th className="py-2 pr-4 font-semibold">Requests</th>
                  <th className="py-2 pr-4 font-semibold">Errors</th>
                  <th className="py-2 pr-4 font-semibold">Avg</th>
                  <th className="py-2 font-semibold">Max</th>
                </tr>
              </thead>
              <tbody>
                {d.endpoints.map((e) => (
                  <tr key={e.method + e.path} className="border-t border-border">
                    <td className="py-2 pr-4">
                      <span className="mr-2 rounded bg-surface2 px-1.5 py-0.5 text-[10px] font-bold text-muted">
                        {e.method}
                      </span>
                      <span className="text-fg">{e.path}</span>
                    </td>
                    <td className="py-2 pr-4">{e.count}</td>
                    <td className={"py-2 pr-4 " + (e.errors > 0 ? "text-danger" : "")}>
                      {e.errors}
                    </td>
                    <td className="py-2 pr-4">{ms(e.avg_ms)}</td>
                    <td className="py-2">{ms(e.max_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-center text-xs text-muted">
        In-process operational metrics. Counters reset when the backend restarts;
        health-check pings are excluded.
      </p>
    </div>
  )
}
