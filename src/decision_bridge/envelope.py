"""Typed shape of the successful output envelope and of `bridge_status`.

These models exist so the MCP tools can advertise an output schema. The service builds plain dicts;
a test keeps the two in sync.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class Metadata(BaseModel):
    duration_ms: int = Field(description="Elapsed decision-service time, including queue wait.")
    request_id: str


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float | None = None


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(description="A probability between 0 and 1, not a boolean verdict.")
    confidence: float | None = None


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float | None = None


Answer = Annotated[ChoiceAnswer | NoulAnswer | ScoreAnswer, Field(discriminator="type")]


class DecisionEnvelope(BaseModel):
    schema_version: Literal["1"]
    model: str
    answers: dict[str, Answer]
    usage: Usage | None = None
    metadata: Metadata


class OllamaStatus(BaseModel):
    state: Literal["available", "unavailable", "unknown", "not_checked"]
    version: str | None


class ModelStatus(BaseModel):
    state: Literal["available", "unavailable", "unknown", "not_checked"]
    local: bool | None
    decision_capable: bool | None


class InferenceStatus(BaseModel):
    state: Literal["not_checked", "verified", "failed"] = Field(
        description="Outcome of the most recent decision in this process."
    )


class BridgeStatus(BaseModel):
    bridge_version: str
    schema_version: str
    model: str
    endpoint: str
    ollama: OllamaStatus
    model_status: ModelStatus
    inference: InferenceStatus
