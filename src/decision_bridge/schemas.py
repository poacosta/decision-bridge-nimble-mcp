"""Decision input models, strict JSON parsing, and request-body construction.

Limits mirror the Nimble-on-Ollama contract: 1-64 questions, 2-26 choice/score alternatives and a
64 KiB serialized request body. See https://ollama.com/library/nimble:latest.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
)

from decision_bridge.errors import BridgeError, ErrorCode

MAX_BODY_BYTES = 64 * 1024
MAX_QUESTIONS = 64
MIN_OPTIONS = 2
MAX_OPTIONS = 26
MAX_STATE_DEPTH = 16


def _invalid(message: str) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_INPUT, message)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _labels_nonempty(value: dict[str, str | None]) -> dict[str, str | None]:
    if any(not key for key in value):
        raise ValueError("labels must not be empty")
    return value


def _descriptions_nonblank(value: list[str]) -> list[str]:
    if any(not item.strip() for item in value):
        raise ValueError("level descriptions must not be blank")
    return value


def _ids_nonempty(value: dict[str, Any]) -> dict[str, Any]:
    if any(not key for key in value):
        raise ValueError("question IDs must not be empty")
    return value


def _check_state(value: Any) -> Any:
    if not isinstance(value, str | dict | list):
        raise ValueError("state must be a string, object, or array")
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            if depth > MAX_STATE_DEPTH:
                raise ValueError(f"state is nested deeper than {MAX_STATE_DEPTH} levels")
            for key, child in node.items():
                if not isinstance(key, str):
                    raise ValueError("state object keys must be strings")
                stack.append((child, depth + 1))
        elif isinstance(node, list):
            if depth > MAX_STATE_DEPTH:
                raise ValueError(f"state is nested deeper than {MAX_STATE_DEPTH} levels")
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise ValueError("state contains a non-finite number")
        elif node is not None and not isinstance(node, str | int | bool):
            raise ValueError("state contains a non-JSON value")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Instructions = Annotated[str, AfterValidator(_nonblank), Field(min_length=1)]


class ChoiceQuestion(_Strict):
    type: Literal["choice"]
    instructions: Instructions
    criteria: Annotated[
        dict[str, str | None],
        Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS),
        AfterValidator(_labels_nonempty),
    ]


class NoulCriteria(_Strict):
    true: str | None = None
    false: str | None = None


class NoulQuestion(_Strict):
    type: Literal["noul"]
    instructions: Instructions
    criteria: NoulCriteria | None = None


class ScoreQuestion(_Strict):
    type: Literal["score"]
    instructions: Instructions
    criteria: Annotated[
        list[str],
        Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS),
        AfterValidator(_descriptions_nonblank),
    ]


Question = Annotated[ChoiceQuestion | NoulQuestion | ScoreQuestion, Field(discriminator="type")]

State = Annotated[
    Any,
    AfterValidator(_check_state),
    WithJsonSchema(
        {
            "type": ["string", "object", "array"],
            "description": "The evidence to judge: a string, JSON object, or JSON array.",
        }
    ),
]


Questions = Annotated[
    dict[str, Question],
    Field(min_length=1, max_length=MAX_QUESTIONS),
    AfterValidator(_ids_nonempty),
]


class DecisionInput(_Strict):
    """Caller-supplied evidence and fixed questions. Advisory input only."""

    state: State
    questions: Questions


def decision_input_json_schema() -> dict[str, Any]:
    return DecisionInput.model_json_schema()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise _invalid(f"Duplicate key in JSON object: {key[:40]!r}")
        seen.add(key)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise _invalid(f"Non-finite number {name} is not valid JSON")


def parse_json_strict(text: str) -> Any:
    """Parse JSON, rejecting duplicate object keys and NaN/Infinity instead of overwriting."""
    try:
        return json.loads(
            text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except BridgeError:
        raise
    except RecursionError:
        raise _invalid("JSON input is nested too deeply") from None
    except ValueError as exc:
        raise _invalid(f"Input is not valid JSON: {exc}") from None


def _summarise(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:5]:
        loc = ".".join(str(p) for p in err["loc"]) or "input"
        parts.append(f"{loc}: {err['msg']}")
    more = exc.error_count() - len(parts)
    text = "; ".join(parts) + (f"; and {more} more" if more > 0 else "")
    return text[:600]


def parse_decision_input(raw: object) -> DecisionInput:
    """Validate a decision request given as JSON text, a dict, or an existing model."""
    if isinstance(raw, DecisionInput):
        return raw
    if isinstance(raw, str | bytes | bytearray):
        text = raw.decode("utf-8", "strict") if not isinstance(raw, str) else raw
        raw = parse_json_strict(text)
    try:
        return DecisionInput.model_validate(raw)
    except ValidationError as exc:
        raise _invalid(_summarise(exc)) from None


def build_request_body(inp: DecisionInput, model: str, keep_alive: str | None) -> bytes:
    """Serialize the exact upstream request once; the same bytes are measured and sent."""
    payload: dict[str, Any] = {
        "model": model,
        "state": inp.state,
        "questions": {
            qid: q.model_dump(mode="json", exclude_unset=True) for qid, q in inp.questions.items()
        },
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    try:
        body = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        raise _invalid("Input contains text that cannot be encoded as UTF-8") from None
    if len(body) > MAX_BODY_BYTES:
        raise BridgeError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"Serialized request is {len(body)} bytes; the limit is {MAX_BODY_BYTES}. "
            "Reduce the evidence or the number of questions.",
        )
    return body
