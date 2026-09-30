"""HTTP adapter for Ollama. The only module that talks to the network.

Uses the dedicated decision endpoint (`POST /v1/systemone`) plus two cheap diagnostics
(`GET /api/version`, `GET /api/tags`). Upstream error bodies are classified but never echoed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from decision_bridge.config import Settings
from decision_bridge.errors import BridgeError, ErrorCode

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
DIAGNOSTIC_TIMEOUT_SECONDS = 5.0
DECISION_PATH = "/v1/systemone"


@dataclass(frozen=True)
class InstalledModel:
    name: str
    digest: str | None
    is_remote: bool
    capabilities: tuple[str, ...]


class _TooLarge(Exception):
    pass


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _TooLarge
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise _TooLarge
    return bytes(buffer)


def _error_text(raw: bytes) -> tuple[bool, str]:
    """Return (was_json_error_object, lowercased error text) without keeping the raw body."""
    try:
        data = json.loads(raw)
    except ValueError:
        return False, ""
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return True, data["error"].lower()
    return isinstance(data, dict), ""


def _status_error(status: int, raw: bytes) -> BridgeError:
    is_json, text = _error_text(raw)
    if 300 <= status < 400:
        return BridgeError(
            ErrorCode.UPSTREAM_ERROR,
            "Ollama answered with a redirect, which the bridge does not follow. "
            "Check DECISION_BRIDGE_OLLAMA_URL.",
            http_status=status,
        )
    if status == 404:
        if is_json and "model" in text and "not found" in text:
            return BridgeError(
                ErrorCode.MODEL_NOT_FOUND,
                "The configured model is not installed. Install it explicitly with "
                "`ollama pull <model>`.",
            )
        if not is_json:
            return BridgeError(
                ErrorCode.OLLAMA_INCOMPATIBLE,
                f"Ollama has no {DECISION_PATH} endpoint. Nimble decisions need Ollama 0.35 or "
                "newer; upgrade Ollama.",
            )
    if status == 413:
        return BridgeError(ErrorCode.PAYLOAD_TOO_LARGE, "Ollama rejected the request as too large.")
    if status in (400, 422):
        if any(word in text for word in ("context", "token", "too long")):
            return BridgeError(
                ErrorCode.CONTEXT_LIMIT,
                "The evidence and questions do not fit the model's context window "
                "(about 8K tokens). Shorten the evidence or reduce the question set.",
            )
        if "decision" in text and any(w in text for w in ("support", "capab")):
            return BridgeError(
                ErrorCode.DECISION_UNSUPPORTED,
                "The configured model does not support decision requests.",
            )
    if status in (401, 403):
        return BridgeError(
            ErrorCode.UPSTREAM_ERROR,
            f"Ollama refused the request (HTTP {status}); check the endpoint's access rules.",
            http_status=status,
        )
    return BridgeError(
        ErrorCode.UPSTREAM_ERROR,
        f"Ollama returned HTTP {status}. Check the Ollama server logs.",
        http_status=status,
    )


def _transport_error(exc: httpx.HTTPError) -> BridgeError:
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return BridgeError(
            ErrorCode.OLLAMA_UNAVAILABLE,
            "Cannot reach Ollama. Start it (or check DECISION_BRIDGE_OLLAMA_URL) and try again.",
        )
    if isinstance(exc, httpx.TimeoutException):
        return BridgeError(
            ErrorCode.TIMEOUT,
            "Ollama did not answer in time. Check hardware load or raise "
            "DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS.",
        )
    return BridgeError(
        ErrorCode.UPSTREAM_ERROR, "The connection to Ollama failed while the request was running."
    )


class OllamaClient:
    """One shared async HTTP client per process. Redirects and environment proxies are off."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._diag_timeout = httpx.Timeout(
            DIAGNOSTIC_TIMEOUT_SECONDS,
            connect=min(settings.connect_timeout, DIAGNOSTIC_TIMEOUT_SECONDS),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def post_decision(self, body: bytes) -> dict[str, Any]:
        try:
            async with self._client.stream(
                "POST", DECISION_PATH, content=body, headers={"content-type": "application/json"}
            ) as response:
                if response.status_code >= 300:
                    try:
                        raw = await _read_capped(response, MAX_ERROR_BODY_BYTES)
                    except _TooLarge:
                        raw = b""
                    raise _status_error(response.status_code, raw)
                try:
                    raw = await _read_capped(response, MAX_RESPONSE_BYTES)
                except _TooLarge:
                    raise BridgeError(
                        ErrorCode.INVALID_UPSTREAM_RESPONSE,
                        "Ollama's response exceeded the 1 MiB safety limit.",
                    ) from None
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None
        return _json_object(raw)

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path, timeout=self._diag_timeout)
            raw = response.content[:MAX_RESPONSE_BYTES]
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None
        if response.status_code >= 300:
            raise _status_error(response.status_code, raw)
        return _json_object(raw)

    async def get_version(self) -> str | None:
        version = (await self._get_json("/api/version")).get("version")
        return version if isinstance(version, str) else None

    async def list_models(self) -> list[InstalledModel]:
        models = (await self._get_json("/api/tags")).get("models")
        if not isinstance(models, list):
            raise BridgeError(
                ErrorCode.INVALID_UPSTREAM_RESPONSE, "Ollama returned an unexpected model list."
            )
        result: list[InstalledModel] = []
        for entry in models:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            caps = entry.get("capabilities")
            digest = entry.get("digest")
            result.append(
                InstalledModel(
                    name=entry["name"],
                    digest=digest if isinstance(digest, str) else None,
                    is_remote=bool(entry.get("remote_model") or entry.get("remote_host")),
                    capabilities=tuple(c for c in caps if isinstance(c, str))
                    if isinstance(caps, list)
                    else (),
                )
            )
        return result


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise BridgeError(
            ErrorCode.INVALID_UPSTREAM_RESPONSE,
            "Ollama returned a response that is not a JSON object.",
        )
    return data
