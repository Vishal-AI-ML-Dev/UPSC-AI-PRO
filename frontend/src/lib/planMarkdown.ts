// Utilities to clean and structure a generated study plan so it can be shown as
// collapsible sections with an interactive, persisted checklist.
//
// The planner LLM is told to emit "## " Markdown headings, but models sometimes
// deviate and wrap section titles in **bold** (which then renders as literal
// asterisks). These helpers deterministically repair that, then split the plan
// into sections and tasks the UI can render.

const SECTION_EMOJIS = [
  "\u{1F50D}",
  "\u{23F0}",
  "\u{1F4C5}",
  "\u{1F4DA}",
  "\u{1F3AF}",
  "\u{1F527}",
  "\u{1F4CA}",
  "\u{1F4F0}",
  "\u2705",
  "\u{1F393}",
  "\u{1F4D6}",
  "\u{1F9ED}",
  "\u{1F4DD}",
  "\u{1F525}",
  "\u{1F4A1}",
]

function startsWithSectionEmoji(text: string): boolean {
  const t = text.trimStart()
  return SECTION_EMOJIS.some((e) => t.startsWith(e))
}

/**
 * Repair malformed plan markdown so headings always render cleanly, regardless
 * of how well the model followed the format instructions.
 */
export function normalizePlan(md: string): string {
  if (!md) return ""
  const lines = md.replace(/\r\n/g, "\n").split("\n")
  const out: string[] = []
  for (const raw of lines) {
    const trimmed = raw.trim()

    // Skip lines that are only stray asterisks.
    if (/^\*{1,3}$/.test(trimmed)) continue

    // Whole-line bold -> heading (e.g. "**Reality Check**").
    const boldOnly = trimmed.match(/^\*\*(.+?)\*\*$/)
    if (boldOnly) {
      const inner = boldOnly[1].trim()
      if (startsWithSectionEmoji(inner) || inner.length <= 60) {
        out.push(`## ${inner}`)
        continue
      }
    }

    // A section-emoji line that isn't already a heading -> heading, stripping
    // stray asterisks/hashes that leaked in.
    if (startsWithSectionEmoji(trimmed) && !trimmed.startsWith("#")) {
      const clean = trimmed
        .replace(/\*\*/g, "")
        .replace(/^#+\s*/, "")
        .replace(/\s*#+\s*$/, "")
        .trim()
      out.push(`## ${clean}`)
      continue
    }

    // An existing heading that still carries trailing asterisks.
    if (trimmed.startsWith("#")) {
      out.push(trimmed.replace(/\*\*/g, "").replace(/\s+$/, ""))
      continue
    }

    out.push(raw)
  }
  return out.join("\n")
}

export interface PlanSection {
  id: string
  title: string
  body: string
}

export interface ParsedPlan {
  intro: string
  sections: PlanSection[]
}

function slugify(text: string, index: number): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return `${index}-${base || "section"}`
}

/** Split a (normalized) plan into an intro blob + "## " sections. */
export function parsePlan(md: string): ParsedPlan {
  const normalized = normalizePlan(md)
  const lines = normalized.split("\n")
  const introLines: string[] = []
  const sections: PlanSection[] = []
  let current: PlanSection | null = null
  let idx = 0

  for (const line of lines) {
    const heading = line.match(/^##\s+(.*)$/)
    if (heading) {
      if (current) sections.push(current)
      const title = heading[1].trim()
      current = { id: slugify(title, idx), title, body: "" }
      idx += 1
    } else if (current) {
      current.body += (current.body ? "\n" : "") + line
    } else {
      introLines.push(line)
    }
  }
  if (current) sections.push(current)

  return { intro: introLines.join("\n").trim(), sections }
}

export interface PlanItem {
  text: string
  detail: string
}

export interface StructuredSection {
  lead: string
  items: PlanItem[]
}

const TASK_RE = /^(?:[-*\u2022]|\d+[.)]|Day\s*\d+|Hour\s*\d+)/i

/**
 * A checkable task line = a top-level bullet, numbered item, or Day/Hour row.
 * Deeply-nested detail lines are treated as context, not tasks.
 */
export function isTaskLine(line: string): boolean {
  const t = line.trim()
  if (!t) return false
  if (t.startsWith("\u25E6")) return false
  if (/^\s{4,}/.test(line)) return false
  return TASK_RE.test(t)
}

function stripMarker(line: string): string {
  return line.trim().replace(/^(?:[-*\u2022]|\d+[.)])\s*/, "")
}

/** Break a section body into an optional lead paragraph + checkable items. */
export function structureSection(body: string): StructuredSection {
  const lines = body.split("\n")
  const leadLines: string[] = []
  const items: PlanItem[] = []
  let current: PlanItem | null = null

  for (const line of lines) {
    if (isTaskLine(line)) {
      if (current) items.push(current)
      current = { text: stripMarker(line), detail: "" }
    } else if (current) {
      current.detail += (current.detail ? "\n" : "") + line
    } else {
      leadLines.push(line)
    }
  }
  if (current) items.push(current)

  return { lead: leadLines.join("\n").trim(), items }
}

/**
 * A stable short id for a plan, used to namespace persisted checklist progress
 * so that generating a new plan starts with a fresh, unchecked list.
 */
export function planKey(md: string): string {
  let h = 0
  for (let i = 0; i < md.length; i++) {
    h = (h * 31 + md.charCodeAt(i)) | 0
  }
  return `p${(h >>> 0).toString(36)}`
}
