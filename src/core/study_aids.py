"""
Study Aids - shared mind map + practice questions generator.
Used by the NCERT and Lecture agents to fill mindmap_html / questions_html.
All HTML is self-contained (inline CSS, no external CDN/JS) so it renders
reliably inside Streamlit components.html().
"""

import html
import json
import logging
import re

from src.core.llm import describe_exception, get_fast_llm, message_text
from src.core.observability import trace_config
from src.core.prompt_safety import fence_label, harden_untrusted

logger = logging.getLogger(__name__)


# Plain-string prompt (markers replaced via str.replace, no f-string / template
# braces) to stay robust against brace-escaping issues.
_AIDS_PROMPT = """You are a UPSC study-aid generator. Using ONLY the study material below, build a concept mind map and exam practice questions.

TOPIC: <<TOPIC>>
PAPER: <<PAPER>>

STUDY MATERIAL:
<<TEXT>>

Return ONLY valid JSON (no markdown, no code fences) in EXACTLY this shape:
{
  "central": "short central theme, 3-6 words",
  "branches": [
    {"title": "branch name", "points": ["short point", "short point"]}
  ],
  "prelims": [
    {"q": "question text", "options": ["opt one", "opt two", "opt three", "opt four"], "answer": "A", "explanation": "one line why"}
  ],
  "mains": [
    {"q": "mains question text", "approach": "2-3 line approach hint"}
  ]
}

Rules:
- Use ONLY facts, dates, numbers and names explicitly present in the material. NEVER invent or guess.
- IMPORTANT: If the material is only an announcement / news / update / notification, or has no real teachable subject content, return EMPTY lists for "branches", "prelims" and "mains" (a short "central" label is fine). Do NOT manufacture questions from thin content.
- When there IS enough teaching content: up to 6 branches (2-4 crisp points each), up to 5 prelims MCQs, and up to 3 mains questions. Include only as many as the material genuinely supports - fewer is better than fabricated.
- Each prelims MCQ has exactly 4 plain options (do NOT prefix them with A/B/C/D). "answer" must be one of A, B, C, D.
- Keep language simple and exam-focused.
"""


_PLACEHOLDER_RE = re.compile(r"<<(?:TOPIC|PAPER|TEXT)>>")


def _safe_json(raw: str):
    """Best-effort extraction of a JSON object from an LLM response."""
    if not isinstance(raw, str) or not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        if s.count("```") >= 2:
            s = s.split("```", 2)[1]
        else:
            s = s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception as e:
        logger.warning("study_aids JSON parse failed: %s", type(e).__name__)
        return None


_MINDMAP_CSS = """
<style>
.sa-mm { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; padding: 8px; }
.sa-central { background: #4f46e5; color: #ffffff; padding: 12px 18px; border-radius: 10px; font-weight: 700; font-size: 16px; text-align: center; margin: 0 auto 16px; max-width: 70%; }
.sa-branches { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }
.sa-branch { background: #f8fafc; border: 1px solid #e2e8f0; border-left: 5px solid #6366f1; border-radius: 8px; padding: 10px 14px; min-width: 200px; max-width: 280px; flex: 1; }
.sa-branch h4 { margin: 0 0 6px; color: #3730a3; font-size: 14px; }
.sa-branch ul { margin: 0; padding-left: 18px; }
.sa-branch li { font-size: 13px; color: #334155; margin: 3px 0; }
</style>
"""

_QUESTIONS_CSS = """
<style>
.sa-q { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; padding: 8px; }
.sa-q .sa-h { color: #3730a3; border-bottom: 2px solid #e2e8f0; padding-bottom: 4px; margin: 14px 0 10px; }
.sa-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; margin: 10px 0; }
.sa-qtext { font-weight: 600; color: #1e293b; margin-bottom: 8px; }
.sa-opts { margin: 4px 0 8px; list-style: none; padding-left: 0; }
.sa-opts li { margin: 3px 0; color: #334155; }
.sa-ans { margin-top: 6px; }
.sa-ans summary { cursor: pointer; color: #4f46e5; font-weight: 600; }
.sa-ans p { margin: 6px 0; color: #166534; }
</style>
"""

_BRANCH_COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]
_OPT_LABELS = ["A", "B", "C", "D", "E", "F"]


def _esc(value) -> str:
    return html.escape(str(value).strip())


def _build_mindmap_html(data: dict) -> str:
    central = _esc(data.get("central", "")) or "Overview"
    branches = data.get("branches") or []
    parts = [_MINDMAP_CSS, '<div class="sa-mm">']
    parts.append('<div class="sa-central">' + central + "</div>")
    parts.append('<div class="sa-branches">')
    for i, b in enumerate(branches):
        if not isinstance(b, dict):
            continue
        title = _esc(b.get("title", ""))
        if not title:
            continue
        color = _BRANCH_COLORS[i % len(_BRANCH_COLORS)]
        parts.append('<div class="sa-branch" style="border-left-color:' + color + '">')
        parts.append("<h4>" + title + "</h4><ul>")
        for p in b.get("points") or []:
            txt = _esc(p)
            if txt:
                parts.append("<li>" + txt + "</li>")
        parts.append("</ul></div>")
    parts.append("</div></div>")
    return "".join(parts)


def _build_questions_html(data: dict) -> str:
    prelims = data.get("prelims") or []
    mains = data.get("mains") or []
    parts = [_QUESTIONS_CSS, '<div class="sa-q">']

    if prelims:
        parts.append('<h3 class="sa-h">Prelims Practice (MCQs)</h3>')
        for i, q in enumerate(prelims, 1):
            if not isinstance(q, dict):
                continue
            parts.append('<div class="sa-card">')
            parts.append(
                '<div class="sa-qtext">Q' + str(i) + ". " + _esc(q.get("q", "")) + "</div>"
            )
            parts.append('<ul class="sa-opts">')
            for j, opt in enumerate(q.get("options") or []):
                lab = _OPT_LABELS[j] if j < len(_OPT_LABELS) else str(j + 1)
                parts.append("<li><b>" + lab + ".</b> " + _esc(opt) + "</li>")
            parts.append("</ul>")
            ans = _esc(q.get("answer", ""))
            exp = _esc(q.get("explanation", ""))
            parts.append('<details class="sa-ans"><summary>Show answer</summary>')
            parts.append("<p><b>Answer: " + ans + "</b></p>")
            if exp:
                parts.append("<p>" + exp + "</p>")
            parts.append("</details></div>")

    if mains:
        parts.append('<h3 class="sa-h">Mains Practice</h3>')
        for i, q in enumerate(mains, 1):
            if not isinstance(q, dict):
                continue
            parts.append('<div class="sa-card">')
            parts.append(
                '<div class="sa-qtext">Q' + str(i) + ". " + _esc(q.get("q", "")) + "</div>"
            )
            approach = _esc(q.get("approach", ""))
            if approach:
                parts.append(
                    '<details class="sa-ans"><summary>Approach hint</summary><p>'
                    + approach
                    + "</p></details>"
                )
            parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)


def generate_study_aids(text: str, topic: str = "", paper: str = ""):
    """Return (mindmap_html, questions_html). On any failure returns ('', '')."""
    if not text or not text.strip():
        return "", ""

    # The material (raw transcript / chapter text) and the model-derived topic /
    # paper are untrusted: fence them (Phase 07 F7). One-pass substitution, so a
    # placeholder inside a value is never expanded.
    values = {
        "<<TOPIC>>": fence_label(topic, "General", label="study topic"),
        "<<PAPER>>": fence_label(paper, "General Studies", label="UPSC paper"),
        "<<TEXT>>": harden_untrusted(text[:12000], label="study material"),
    }
    prompt = _PLACEHOLDER_RE.sub(lambda m: values[m.group(0)], _AIDS_PROMPT)

    try:
        # Study aids are structured JSON generation, not deep reasoning, so use
        # the fast/cheap chain (flash-lite first) to avoid the ~15x cost of the
        # full reasoning model. trace_config makes the call legible in Langfuse.
        res = get_fast_llm().invoke(prompt, config=trace_config("study-aids"))
        # String or list-of-blocks content -> plain text ("" if no text blocks).
        raw = message_text(getattr(res, "content", res))
    except Exception as e:
        # Class/status/location only - never the provider message or the text.
        logger.error(
            "study_aids LLM call failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
        return "", ""

    data = _safe_json(raw)
    if not isinstance(data, dict):
        return "", ""

    try:
        mindmap = _build_mindmap_html(data) if data.get("branches") else ""
    except Exception as e:
        logger.warning(f"mindmap build failed: {e}")
        mindmap = ""
    try:
        questions = (
            _build_questions_html(data) if (data.get("prelims") or data.get("mains")) else ""
        )
    except Exception as e:
        logger.warning(f"questions build failed: {e}")
        questions = ""

    return mindmap, questions
