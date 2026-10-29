"""
Evaluator routes - Answer evaluation
"""

from fastapi import APIRouter

from src.agents.evaluator.graph import evaluate_answer, evaluate_mains, get_model_answer
from src.core.eval_parse import parse_answer_evaluation, parse_mains_evaluation
from src.schemas import EvaluateRequest, MainsEvalRequest, ModelAnswerRequest

router = APIRouter(prefix="/evaluator", tags=["Evaluator"])


@router.post("/evaluate/sync")
async def evaluate_sync(request: EvaluateRequest):
    """Basic answer evaluation (non-streaming)."""
    response = ""
    for chunk in evaluate_answer(request.question, request.answer):
        response += chunk
    return {
        "response": response,
        "structured": parse_answer_evaluation(response).model_dump(),
    }


@router.post("/mains/sync")
async def mains_eval_sync(request: MainsEvalRequest):
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
    return {
        "response": response,
        "structured": parse_mains_evaluation(response, max_marks=request.marks).model_dump(),
    }


@router.post("/model-answer/sync")
async def model_answer_sync(request: ModelAnswerRequest):
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
