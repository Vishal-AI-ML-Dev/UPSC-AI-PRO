"""
Pydantic schemas for request/response models
"""

from typing import Annotated, Optional

from pydantic import BaseModel, Field

# ─────────────────────────────────────────
# COMMON SCHEMAS
# ─────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    chat_history: Optional[list[ChatMessage]] = None


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
    question: str = Field(..., min_length=1)
    student_context: Optional[StudentContext] = None
    chat_history: Optional[list[ChatMessage]] = None


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
    question: str = Field(..., min_length=1)
    class_name: str
    subject: str
    chapter: str
    chat_history: Optional[list[ChatMessage]] = None


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
    transcript: str = Field(..., min_length=1, description="Pasted lecture transcript text")
    medium: str = Field("English", description="Notes output language: English / Hindi / Hinglish")


class LectureChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    video_id: str
    topic_info: Optional[dict] = None
    chat_history: Optional[list[ChatMessage]] = None


# ─────────────────────────────────────────
# CURRENT AFFAIRS SCHEMAS
# ─────────────────────────────────────────


class DailyCARequest(BaseModel):
    date: str = Field(..., description="Date in 'DD Month YYYY' format")


class EditorialRequest(BaseModel):
    topic: str


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
    question: str = Field(..., min_length=1)
    pdf_hash: str
    book_info: Optional[dict] = None
    chat_history: Optional[list[ChatMessage]] = None


# ─────────────────────────────────────────
# PYQ SCHEMAS
# ─────────────────────────────────────────


class QuestionGenRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    question_type: str = Field(default="mcq", description="'mcq' or 'mains'")
    difficulty: str = Field(default="medium", description="'easy', 'medium', 'hard'")
    num_questions: int = Field(default=5, ge=1, le=20)
    marks: int = Field(default=10, description="For mains questions")


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=10)


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
    topic: str = Field(default="", description="Topic to focus on; empty = mixed from the bank")
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
