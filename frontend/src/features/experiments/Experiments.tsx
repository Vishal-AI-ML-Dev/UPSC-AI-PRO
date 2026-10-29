import { useQuery } from "@tanstack/react-query"
import { api, type ExperimentsOverview } from "../../lib/api"
import { Card, Spinner, ErrorState } from "../../components/ui"

// --- tiny formatters ------------------------------------------------------- //
const pct = (x: number | null | undefined) =>
  x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`

const signedPct = (x: number | null | undefined) => {
  if (x === null || x === undefined) return "-"
  const s = (x * 100).toFixed(1)
  return x > 0 ? `+${s}%` : `${s}%`
}

// --- styles (kept as consts: JSX inline object literals are avoided) ------- //
const wrapStyle: React.CSSProperties = { display: "grid", gap: 16 }
const rowBetween: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 12,
}
const statGrid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
  gap: 12,
}
const statValue: React.CSSProperties = { fontSize: 24, fontWeight: 700 }
const statLabel: React.CSSProperties = { fontSize: 12, opacity: 0.65 }
const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 14,
}
const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 10px",
  borderBottom: "1px solid rgba(128,128,128,0.25)",
  opacity: 0.7,
  fontWeight: 600,
}
const tdStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: "1px solid rgba(128,128,128,0.12)",
}
const barTrack: React.CSSProperties = {
  position: "relative",
  height: 8,
  width: 120,
  borderRadius: 999,
  background: "rgba(128,128,128,0.2)",
  overflow: "hidden",
}
const leaderTag: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  padding: "2px 8px",
  borderRadius: 999,
  background: "rgba(34,197,94,0.15)",
  color: "rgb(21,128,61)",
}
const mutedTag: React.CSSProperties = {
  fontSize: 11,
  padding: "2px 8px",
  borderRadius: 999,
  background: "rgba(128,128,128,0.15)",
  opacity: 0.8,
}
const noteStyle: React.CSSProperties = { fontSize: 12, opacity: 0.6, marginTop: 4 }
const descStyle: React.CSSProperties = { fontSize: 13, opacity: 0.75, marginTop: 2 }
const headingStyle: React.CSSProperties = { margin: 0 }

function barFill(fraction: number): React.CSSProperties {
  const w = Math.max(0, Math.min(1, fraction)) * 100
  return {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: `${w}%`,
    background: "rgb(59,130,246)",
  }
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div style={statValue}>{value}</div>
      <div style={statLabel}>{label}</div>
    </div>
  )
}

export default function Experiments() {
  const { data, isLoading, error, refetch } = useQuery<ExperimentsOverview>({
    queryKey: ["experiments"],
    queryFn: () => api.experiments(),
    retry: false,
  })

  if (isLoading) return <Spinner />
  if (error || !data)
    return (
      <ErrorState
        message="Could not load experiments. This view is admin-only."
        onRetry={() => void refetch()}
      />
    )

  const overall = data.feedback.overall

  return (
    <div style={wrapStyle}>
      <Card>
        <div style={rowBetween}>
          <h2 style={headingStyle}>Prompt experiments</h2>
          <span style={mutedTag}>{data.feedback.sample_size} ratings</span>
        </div>
        <p style={noteStyle}>
          Human thumbs up/down feed a labelled dataset and per-variant win-rates.
          Winners are picked by a conservative lower bound, so a variant needs both a
          good rate and enough data before it is crowned.
        </p>
        <div style={statGrid}>
          <Stat label="Overall up-rate" value={pct(overall.up_rate)} />
          <Stat label="Thumbs up" value={overall.up} />
          <Stat label="Thumbs down" value={overall.down} />
          <Stat label="Total ratings" value={overall.total} />
        </div>
      </Card>

      {data.experiments.map((exp) => {
        const res = exp.results
        return (
          <Card key={exp.key}>
            <div style={rowBetween}>
              <div>
                <h3 style={headingStyle}>{exp.key}</h3>
                <div style={descStyle}>{exp.description}</div>
              </div>
              <span style={exp.enabled ? leaderTag : mutedTag}>
                {exp.enabled ? "active" : "disabled"}
              </span>
            </div>

            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Variant</th>
                  <th style={thStyle}>Up-rate</th>
                  <th style={thStyle}>Win-rate</th>
                  <th style={thStyle}>Lift</th>
                  <th style={thStyle}>N</th>
                  <th style={thStyle}>Confidence floor</th>
                </tr>
              </thead>
              <tbody>
                {res.variants.map((v) => {
                  const isLeader = res.leader === v.variant
                  return (
                    <tr key={v.variant}>
                      <td style={tdStyle}>
                        {v.variant}{" "}
                        {isLeader ? (
                          <span style={leaderTag}>
                            {res.confident ? "leader" : "leading"}
                          </span>
                        ) : null}
                      </td>
                      <td style={tdStyle}>{pct(v.up_rate)}</td>
                      <td style={tdStyle}>
                        <div style={barTrack}>
                          <div style={barFill(v.up_rate)} />
                        </div>
                      </td>
                      <td style={tdStyle}>
                        {v.variant === res.baseline
                          ? "baseline"
                          : signedPct(v.lift_vs_baseline)}
                      </td>
                      <td style={tdStyle}>{v.total}</td>
                      <td style={tdStyle}>
                        {pct(v.wilson_lower)}
                        {v.enough_data ? "" : " (low N)"}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p style={noteStyle}>
              {res.confident
                ? `"${res.leader}" is ahead with enough evidence to act on.`
                : `No confident winner yet - collect at least ${res.min_sample} ratings per arm.`}
            </p>
          </Card>
        )
      })}
    </div>
  )
}
