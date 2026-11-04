"""
Evaluator routes - Answer evaluation
"""

import logging

from fastapi import APIRouter, Depends

from src.agents.evaluator.graph import evaluate_answer, evaluate_mains, get_model_answer
from src.api.deps import get_current_user
from src.core.eval_parse import parse_answer_evaluation, parse_mains_evaluation
from src.core.evaluation_history import record_attempt
from src.schemas import EvaluateRequest, MainsEvalRequest, ModelAnswerRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluator", tags=["Evaluator"])


def _record_best_effort(
    user: dict, mode: str, subject: str | None, score: object, max_score: object
) -> None:
    """Record one attempt (Phase 08 Slice C1); a failure never affects the response.

    The warning carries only the event, mode and exception type - never the
    question, answer, score text, email or the exception message.
    """
    try:
        record_attempt(
            user_id=user["id"],
            mode=mode,
            subject=subject,
            score=score,
            max_score=max_score,
        )
    except Exception as exc:  # best-effort by design (ADR-017)
        logger.warning(
            "evaluation_attempt_persist_failed",
            extra={
                "event": "evaluation_attempt_persist_failed",
                "mode": mode,
                "error_type": type(exc).__name__,
            },
        )


@router.post("/evaluate/sync")
def evaluate_sync(request: EvaluateRequest, user: dict = Depends(get_current_user)):
    """Basic answer evaluation (non-streaming)."""
    response = ""
    for chunk in evaluate_answer(request.question, request.answer):
        response += chunk
    structured = parse_answer_evaluation(response)
    body = {"response": response, "structured": structured.model_dump()}
    _record_best_effort(user, "basic", request.subject, structured.score, structured.max_score)
    return body


@router.post("/mains/sync")
def mains_eval_sync(request: MainsEvalRequest, user: dict = Depends(get_current_user)):
    """Mains answer evaluation (non-streaming)."""
    response = ""
    for chunk in evaluate_mains(
        question=request.question,
        answer=request.answer,
        marks=request.marks,
        keywords=request.keywords,
        word_limit=request.word_limit,
    ):
        response += chunk
    structured = parse_mains_evaluation(response, max_marks=request.marks)
    body = {"response": response, "structured": structured.model_dump()}
    _record_best_effort(user, "mains", request.subject, structured.score, request.marks)
    return body


@router.post("/model-answer/sync")
def model_answer_sync(request: ModelAnswerRequest):
    """Generate model answer (non-streaming)."""
    response = ""
    for chunk in get_model_answer(
        question=request.question,
        marks=request.marks,
        keywords=request.keywords,
        word_limit=request.word_limit,
    ):
        response += chunk
    return {"response": response}
