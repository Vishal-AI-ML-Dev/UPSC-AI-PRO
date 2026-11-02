"""
Pydantic schemas for request/response models
"""

from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, Field

# ─────────────────────────────────────────
# INPUT BOUNDS (Phase 02, R6)
# ─────────────────────────────────────────

CHAT_QUESTION_MAX = 4_000
TOPIC_MAX = 200
ID_MAX = 100
TRANSCRIPT_MAX = 200_000
PARSE_TEXT_MAX = 50_000
HISTORY_MAX_MESSAGES = 20
HISTORY_CONTENT_MAX = 4_000

# MD5 hex digest identifying an uploaded PDF (addresses upload_<user id>_<hash>).
PDF_HASH_PATTERN = r"^[0-9a-f]{32}$"

# YouTube video id (11 chars) or a pasted-transcript id ("paste_" + 11 hex).
VIDEO_ID_PATTERN = r"^(paste_[0-9a-f]{11}|[A-Za-z0-9_-]{11})$"


def _truncate_content(value: Any) -> Any:
    """Cut long history text BEFORE length validation (never a 422)."""
    if isinstance(value, str) and len(value) > HISTORY_CONTENT_MAX:
        return value[:HISTORY_CONTENT_MAX]
    return value


def _keep_last_messages(value: Any) -> Any:
    """Keep only the most recent history turns BEFORE length validation."""
    if isinstance(value, list) and len(value) > HISTORY_MAX_MESSAGES:
        return value[-HISTORY_MAX_MESSAGES:]
    return value


# ─────────────────────────────────────────
# COMMON SCHEMAS
# ─────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., max_length=20, description="'user' or 'assistant'")
    # BeforeValidator runs first, so max_length is an invariant that never fires
    # for strings: long messages are truncated, not rejected.
    content: Annotated[
        str, BeforeValidator(_truncate_content), Field(max_length=HISTORY_CONTENT_MAX)
    ]


# Client-sent history: trimmed to the last HISTORY_MAX_MESSAGES before validation.
ChatHistory = Annotated[
    list[ChatMessage],
    BeforeValidator(_keep_last_messages),
    Field(max_length=HISTORY_MAX_MESSAGES),
]


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=CHAT_QUESTION_MAX)
    chat_history: Optional[ChatHistory] = None


class ChatResponse(BaseModel):
    response: str


# ─────────────────────────────────────────
# MENTOR SCHEMAS
# ─────────────────────────────────────────


class StudentContext(BaseModel):
    name: Optional[str] = None
    optional: Optional[str] = None
    stage: Optional[str] = None
    weak_areas: Optional[str] = None
    strong_areas: Optional[str] = None
    target_year: Optional[int] = None
    study_hours: Optional[int] = None
    attempts: Optional[int] = None


class MentorRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=CHAT_QUESTION_MAX)
    student_context: Optional[StudentContext] = None
    chat_history: Optional[ChatHistory] = None


class MentorResponse(BaseModel):
    response: str
    intent: Optional[str] = None


# ─────────────────────────────────────────
# PLANNER SCHEMAS
# ─────────────────────────────────────────


# Size bounds for free-text planner/evaluator inputs (each is sent to the model).
SHORT_TEXT_MAX = 200


class PlannerRequest(BaseModel):
    goal: str = Field(..., max_length=SHORT_TEXT_MAX, description="e.g., 'UPSC 2026'")
    hours: str = Field(default="6", max_length=SHORT_TEXT_MAX, description="Daily study hours")
    optional: Optional[str] = Field(default="Not decided", max_length=SHORT_TEXT_MAX)
    weak: Optional[str] = Field(default="Not specified", max_length=SHORT_TEXT_MAX)
    attempt_number: Optional[str] = Field(default="1", max_length=20)


class PlannerResponse(BaseModel):
    plan: str
    meta: Optional[dict] = None


# ─────────────────────────────────────────
# NCERT SCHEMAS
# ─────────────────────────────────────────


class NCERTSessionRequest(BaseModel):
    class_name: str
    subject: str
    chapter: str


class NCERTSessionResponse(BaseModel):
    notes: str
    mindmap_html: Optional[str] = ""
    questions_html: Optional[str] = ""
    chapter_path: str


class NCERTChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=CHAT_QUESTION_MAX)
    class_name: str = Field(..., max_length=TOPIC_MAX)
    subject: str = Field(..., max_length=TOPIC_MAX)
    chapter: str = Field(..., max_length=TOPIC_MAX)
    chat_history: Optional[ChatHistory] = None


class NCERTListResponse(BaseModel):
    items: list[str]
    # False when no NCERT PDFs are installed; ``message`` then explains it.
    available: bool = True
    message: str | None = None


# ─────────────────────────────────────────
# LECTURE SCHEMAS
# ─────────────────────────────────────────


class LectureRequest(BaseModel):
    youtube_url: str = Field(..., description="YouTube video URL")
    medium: str = Field("English", description="Notes output language: English / Hindi / Hinglish")


class LectureResponse(BaseModel):
    notes: str
    topic_info: dict
    video_id: str
    mindmap_html: Optional[str] = ""
    questions_html: Optional[str] = ""


class LectureTextRequest(BaseModel):
    transcript: str = Field(
        ..., min_length=1, max_length=TRANSCRIPT_MAX, description="Pasted lecture transcript text"
    )
    medium: str = Field("English", description="Notes output language: English / Hindi / Hinglish")


class LectureChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=CHAT_QUESTION_MAX)
    video_id: str = Field(..., pattern=VIDEO_ID_PATTERN)
    topic_info: Optional[dict] = None
    chat_history: Optional[ChatHistory] = None


# ─────────────────────────────────────────
# CURRENT AFFAIRS SCHEMAS
# ─────────────────────────────────────────


class DailyCARequest(BaseModel):
    date: str = Field(..., description="Date in 'DD Month YYYY' format")


class EditorialRequest(BaseModel):
    topic: str = Field(..., max_length=TOPIC_MAX)


class MonthlyRequest(BaseModel):
    month: str
    year: str


class EditorialTopicsResponse(BaseModel):
    topics: list[str]


class AvailableDatesResponse(BaseModel):
    dates: list[str]


class AvailableMonthsResponse(BaseModel):
    months: list[tuple[str, str]]


# ─────────────────────────────────────────
# UPLOAD SCHEMAS
# ─────────────────────────────────────────


class UploadResponse(BaseModel):
    success: bool
    filename: str
    hash: str
    book_info: dict
    notes: str


class UploadChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=CHAT_QUESTION_MAX)
    pdf_hash: str = Field(..., pattern=PDF_HASH_PATTERN)
    book_info: Optional[dict] = None
    chat_history: Optional[ChatHistory] = None


# ─────────────────────────────────────────
# PYQ SCHEMAS
# ─────────────────────────────────────────


class QuestionGenRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=TOPIC_MAX)
    question_type: str = Field(default="mcq", description="'mcq' or 'mains'")
    difficulty: str = Field(default="medium", description="'easy', 'medium', 'hard'")
    num_questions: int = Field(default=5, ge=1, le=20)
    marks: int = Field(default=10, description="For mains questions")


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=10, max_length=PARSE_TEXT_MAX)


class ParseResponse(BaseModel):
    questions: list[dict]


class TopicSuggestionsResponse(BaseModel):
    topics: list[str]


# ─────────────────────────────────────────
# EVALUATOR SCHEMAS
# ─────────────────────────────────────────


QUESTION_MAX = 2_000
ANSWER_MAX = 20_000
Keyword = Annotated[str, Field(max_length=100)]
Keywords = Optional[list[Keyword]]


class EvaluateRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=QUESTION_MAX)
    answer: str = Field(..., min_length=50, max_length=ANSWER_MAX)


class MainsEvalRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=QUESTION_MAX)
    answer: str = Field(..., min_length=30, max_length=ANSWER_MAX)
    marks: int = Field(default=10, ge=5, le=15)
    keywords: Keywords = Field(default=None, max_length=20)
    word_limit: int = Field(default=150, ge=1, le=1000)


class ModelAnswerRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=QUESTION_MAX)
    marks: int = Field(default=10, ge=5, le=15)
    keywords: Keywords = Field(default=None, max_length=20)
    word_limit: int = Field(default=150, ge=1, le=1000)


# ─────────────────────────────────────────
# PYQ BANK SCHEMAS (personal, grounded on user-uploaded papers)
# ─────────────────────────────────────────


class BankGenRequest(BaseModel):
    topic: str = Field(
        default="",
        max_length=TOPIC_MAX,
        description="Topic to focus on; empty = mixed from the bank",
    )
    question_type: str = Field(default="mcq", description="'mcq' or 'mains'")
    num_questions: int = Field(default=5, ge=1, le=20)
    marks: int = Field(default=10)
    difficulty: str = Field(default="medium")


class BankUploadResponse(BaseModel):
    success: bool
    filename: str
    hash: str
    chunks: int
    approx_questions: int


class BankStatusResponse(BaseModel):
    exists: bool


# ─────────────────────────────────────────
# STRUCTURED EVALUATION / PLAN SCHEMAS (#5)
# ─────────────────────────────────────────


class AnswerEvaluation(BaseModel):
    """Structured view of a basic answer evaluation (parsed from markdown)."""

    score: Optional[float] = None
    max_score: int = 10
    did_well: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


class MainsEvaluation(BaseModel):
    """Structured view of a mains answer evaluation (parsed from markdown)."""

    score: Optional[float] = None
    max_marks: int = 10
    verdict: Optional[str] = None
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


class StudyPlanMeta(BaseModel):
    """Deterministic, code-derived study-plan metadata (no LLM parsing)."""

    attempt_year: int
    months_left: int
    timeline_msg: str
    prelims_date: Optional[str] = None
