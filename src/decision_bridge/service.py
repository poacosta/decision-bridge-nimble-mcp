"""The shared decision behaviour used by both the CLI and the MCP server."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from decision_bridge import SCHEMA_VERSION, __version__
from decision_bridge.config import Settings
from decision_bridge.diagnostics import ProbeResult, check_model, probe
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.ollama import InstalledModel, OllamaClient
from decision_bridge.responses import validate_response
from decision_bridge.schemas import build_request_body, parse_decision_input

log = logging.getLogger("decision_bridge")

# Calls allowed to wait behind the in-flight ones before new calls are refused with BUSY.
QUEUE_DEPTH = 4

_INFERENCE_FAILURES = frozenset(
    {
        ErrorCode.OLLAMA_UNAVAILABLE,
        ErrorCode.OLLAMA_INCOMPATIBLE,
        ErrorCode.MODEL_NOT_FOUND,
        ErrorCode.MODEL_NOT_LOCAL,
        ErrorCode.DECISION_UNSUPPORTED,
        ErrorCode.TIMEOUT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.INVALID_UPSTREAM_RESPONSE,
    }
)


class DecisionService:
    def __init__(self, settings: Settings, client: OllamaClient | None = None) -> None:
        self.settings = settings
        self._client = client or OllamaClient(settings)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._pending = 0
        self._max_pending = settings.max_concurrency + QUEUE_DEPTH
        self._verified_model: InstalledModel | None = None
        self._inference = "not_checked"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def decide(self, raw_input: object) -> dict[str, Any]:
        request_id = secrets.token_hex(6)
        started = time.monotonic()
        try:
            inp = parse_decision_input(raw_input)
            body = build_request_body(inp, self.settings.model, self.settings.keep_alive)
            upstream = await self._run(body)
            result = validate_response(inp, upstream, model_fallback=self.settings.model)
        except BridgeError as exc:
            exc.request_id = request_id
            if exc.code in _INFERENCE_FAILURES:
                self._inference = "failed"
            log.warning(
                "decision failed request_id=%s code=%s duration_ms=%d",
                request_id,
                exc.code.value,
                _elapsed_ms(started),
            )
            raise
        self._inference = "verified"
        duration_ms = _elapsed_ms(started)
        log.info(
            "decision ok request_id=%s duration_ms=%d questions=%d",
            request_id,
            duration_ms,
            len(inp.questions),
        )
        envelope: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "model": result["model"],
            "answers": result["answers"],
        }
        if "usage" in result:
            envelope["usage"] = result["usage"]
        envelope["metadata"] = {"duration_ms": duration_ms, "request_id": request_id}
        return envelope

    async def _run(self, body: bytes) -> dict[str, Any]:
        if self._pending >= self._max_pending:
            raise BridgeError(
                ErrorCode.BUSY,
                "Too many decisions are already running or queued. Try again shortly.",
            )
        self._pending += 1
        try:
            # One deadline covers queue wait and the in-flight call.
            async with asyncio.timeout(self.settings.request_timeout), self._semaphore:
                await self._ensure_model()
                try:
                    return await self._client.post_decision(body)
                except BridgeError as exc:
                    if exc.code is ErrorCode.MODEL_NOT_FOUND:
                        self._verified_model = None
                    raise
        except TimeoutError:
            raise BridgeError(
                ErrorCode.TIMEOUT,
                "The request deadline expired while queued or running. Check hardware load or "
                "raise DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS. Ollama may still be computing.",
            ) from None
        finally:
            self._pending -= 1

    async def _ensure_model(self) -> None:
        """Verify the model is installed, local and decision-capable; cache only good verdicts."""
        if self._verified_model is not None:
            return
        try:
            models = await self._client.list_models()
        except BridgeError as exc:
            if exc.code in (ErrorCode.OLLAMA_UNAVAILABLE, ErrorCode.TIMEOUT):
                raise
            return  # model list unavailable or odd: let the decision call itself decide
        model, problem = check_model(models, self.settings.model)
        if problem is not None:
            raise problem
        self._verified_model = model

    async def inspect(self) -> ProbeResult:
        """Run the cheap diagnostics and refresh the cached model verdict."""
        result = await probe(self._client, self.settings)
        self._verified_model = result.model
        return result

    async def status(self) -> dict[str, Any]:
        result = await self.inspect()
        reachable = result.get("ollama_reachable").state
        installed = result.get("model_installed").state
        capable = result.get("model_decision_capable").state
        local = result.get("model_local").state
        return {
            "bridge_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "model": self.settings.model,
            "endpoint": self.settings.ollama_url,
            "ollama": {"state": reachable, "version": result.version},
            "model_status": {
                "state": installed,
                "local": None if local == "unknown" else local == "available",
                "decision_capable": None if capable == "unknown" else capable == "available",
            },
            "inference": {"state": self._inference},
        }


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
