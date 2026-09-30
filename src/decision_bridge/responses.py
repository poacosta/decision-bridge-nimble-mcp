"""Validate an upstream decision response against the questions that were submitted.

Nothing is normalized, rounded, or invented: a response either matches the contract or is rejected
as INVALID_UPSTREAM_RESPONSE. Optional additive upstream fields are ignored.
"""

from __future__ import annotations

import math
from typing import Any

from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import ChoiceQuestion, DecisionInput, NoulQuestion, ScoreQuestion

# Tolerance for "probabilities sum to one": upstream floats are not guaranteed to be exact.
PROB_SUM_TOLERANCE = 1e-3


def _bad(where: str, why: str) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_UPSTREAM_RESPONSE,
        f"Ollama's response violated the decision contract ({where}: {why}).",
    )


def _number(value: Any, where: str, lo: float, hi: float) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _bad(where, "expected a finite number")
    if not lo <= value <= hi:
        raise _bad(where, f"expected a value between {lo:g} and {hi:g}")
    return value


def _prob_map(value: Any, keys: list[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys) or len(value) != len(keys):
        raise _bad(where, "probability labels do not match the submitted alternatives")
    total = 0.0
    for key in keys:
        total += _number(value[key], f"{where}.{key}", 0.0, 1.0)
    if abs(total - 1.0) > PROB_SUM_TOLERANCE:
        raise _bad(where, "probabilities do not sum to one")
    return {key: value[key] for key in value}


def _optional_confidence(answer: dict[str, Any], out: dict[str, Any], where: str) -> None:
    if "confidence" in answer:
        out["confidence"] = _number(answer["confidence"], f"{where}.confidence", 0.0, 1.0)


def _choice(question: ChoiceQuestion, answer: dict[str, Any], where: str) -> dict[str, Any]:
    labels = list(question.criteria)
    picked = answer.get("choice")
    if not isinstance(picked, str) or picked not in question.criteria:
        raise _bad(where, "selected choice is not one of the submitted labels")
    out: dict[str, Any] = {
        "type": "choice",
        "choice": picked,
        "probabilities": _prob_map(answer.get("probabilities"), labels, f"{where}.probabilities"),
    }
    _optional_confidence(answer, out, where)
    return out


def _noul(_: NoulQuestion, answer: dict[str, Any], where: str) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "noul", "noul": _number(answer.get("noul"), where, 0.0, 1.0)}
    _optional_confidence(answer, out, where)
    return out


def _score(question: ScoreQuestion, answer: dict[str, Any], where: str) -> dict[str, Any]:
    keys = [str(i) for i in range(len(question.criteria))]
    legend = answer.get("legend")
    if (
        not isinstance(legend, dict)
        or set(legend) != set(keys)
        or not all(isinstance(v, str) for v in legend.values())
    ):
        raise _bad(f"{where}.legend", "level keys do not match the submitted rubric")
    out: dict[str, Any] = {
        "type": "score",
        "score": _number(answer.get("score"), where, 0.0, float(len(keys) - 1)),
        "legend": dict(legend),
        "probabilities": _prob_map(answer.get("probabilities"), keys, f"{where}.probabilities"),
    }
    _optional_confidence(answer, out, where)
    return out


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _bad("usage", "expected an object")
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise _bad(f"usage.{key}", "expected a non-negative integer")
        out[key] = item
    return out


def validate_response(
    inp: DecisionInput, raw: dict[str, Any], *, model_fallback: str
) -> dict[str, Any]:
    """Return the envelope parts `model`, `answers` (in question order) and optional `usage`."""
    answers = raw.get("answers")
    if not isinstance(answers, dict):
        raise _bad("answers", "expected an object")
    if set(answers) != set(inp.questions):
        raise _bad("answers", "answers do not match the submitted questions")

    checked: dict[str, Any] = {}
    for qid, question in inp.questions.items():
        answer = answers[qid]
        where = f"answers.{qid}"
        if not isinstance(answer, dict):
            raise _bad(where, "expected an object")
        if answer.get("type") != question.type:
            raise _bad(where, "answer type does not match the question type")
        if isinstance(question, ChoiceQuestion):
            checked[qid] = _choice(question, answer, where)
        elif isinstance(question, NoulQuestion):
            checked[qid] = _noul(question, answer, where)
        else:
            checked[qid] = _score(question, answer, where)

    model = raw.get("model")
    result: dict[str, Any] = {
        "model": model if isinstance(model, str) and model else model_fallback,
        "answers": checked,
    }
    if "usage" in raw:
        result["usage"] = _usage(raw["usage"])
    return result
