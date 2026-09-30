"""MCP server (stdio) exposing exactly two tools: `decide` and `bridge_status`."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from decision_bridge import __version__
from decision_bridge.config import Settings
from decision_bridge.envelope import BridgeStatus, DecisionEnvelope
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import Questions, State, parse_decision_input
from decision_bridge.service import DecisionService

INSTRUCTIONS = (
    "Decision Bridge asks a local Nimble model, through Ollama, to answer fixed questions about "
    "evidence you supply. Results are advisory: they never authorize or execute an action."
)

DECIDE_DESCRIPTION = (
    "Evaluate caller-supplied evidence against caller-supplied fixed questions using the "
    "configured local decision model. The result is advisory and does not authorize or execute "
    "actions. Question types: 'choice' (pick one labelled alternative; returns probabilities), "
    "'noul' (returns a probability between 0 and 1, not a boolean verdict), and 'score' (rate "
    "against an ordered rubric, lowest level first; returns a numeric score). Limits: 1-64 "
    "questions, 2-26 alternatives per choice/score question, a 64 KiB request body, and roughly "
    "an 8K-token prompt budget; nothing is truncated for you. Failures are returned as tool "
    'errors with a compact {"error": {"code", "message", "retryable", "request_id"}} body. '
    "Upstream contract: https://ollama.com/library/nimble:latest"
)

STATUS_DESCRIPTION = (
    "Report bridge version, configured model, Ollama reachability and version, and installed-model "
    "status. Performs only cheap checks: it never loads the model or runs inference."
)

_ANNOTATIONS = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _error_result(exc: BridgeError) -> CallToolResult:
    payload = exc.to_payload()
    return CallToolResult(
        is_error=True,
        structured_content=payload,
        content=[TextContent(type="text", text=_compact(payload))],
    )


_ALLOWED_ARGUMENTS: dict[str, frozenset[str]] = {
    "decide": frozenset({"state", "questions"}),
    "bridge_status": frozenset(),
}


async def _reject_unknown_arguments(
    ctx: ServerRequestContext[Any, Any], call_next: CallNext
) -> HandlerResult:
    """Refuse unsupported tool arguments instead of letting the SDK silently drop them.

    Callers must not be able to smuggle in settings such as a model or URL, and an ignored
    property would hide a caller's mistake. Runs before the SDK's own argument validation.
    """
    if ctx.method == "tools/call" and isinstance(ctx.params, dict):
        allowed = _ALLOWED_ARGUMENTS.get(str(ctx.params.get("name")))
        arguments = ctx.params.get("arguments") or {}
        if allowed is not None and isinstance(arguments, dict):
            extra = sorted(str(key)[:40] for key in set(arguments) - allowed)
            if extra:
                listed = ", ".join(repr(name) for name in extra[:5])
                return _error_result(
                    BridgeError(
                        ErrorCode.INVALID_INPUT,
                        f"Unsupported argument(s): {listed}. "
                        f"Accepted: {', '.join(sorted(allowed)) or 'none'}.",
                    )
                )
    return await call_next(ctx)


def build_server(service: DecisionService) -> MCPServer:
    server = MCPServer(
        "decision-bridge",
        version=__version__,
        instructions=INSTRUCTIONS,
        middleware=[_reject_unknown_arguments],
    )

    @server.tool(
        name="decide",
        title="Decide",
        description=DECIDE_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Decide",
            read_only_hint=_ANNOTATIONS.read_only_hint,
            destructive_hint=_ANNOTATIONS.destructive_hint,
            open_world_hint=_ANNOTATIONS.open_world_hint,
        ),
    )
    async def decide(
        state: Annotated[State, Field(description="The evidence to judge.")],
        questions: Annotated[
            Questions,
            Field(description="Insertion-ordered map of unique question ID to question."),
        ],
    ) -> Annotated[CallToolResult, DecisionEnvelope]:
        try:
            result = await service.decide(
                parse_decision_input({"state": state, "questions": questions})
            )
        except BridgeError as exc:
            return _error_result(exc)
        return CallToolResult(
            structured_content=result, content=[TextContent(type="text", text=_compact(result))]
        )

    @server.tool(
        name="bridge_status",
        title="Bridge status",
        description=STATUS_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Bridge status",
            read_only_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )
    async def bridge_status() -> Annotated[CallToolResult, BridgeStatus]:
        status = await service.status()
        return CallToolResult(
            structured_content=status, content=[TextContent(type="text", text=_compact(status))]
        )

    return server


async def _serve(settings: Settings) -> None:
    service = DecisionService(settings)
    try:
        await build_server(service).run_stdio_async()
    finally:
        await service.aclose()


def run_stdio(settings: Settings) -> int:
    """Serve MCP over stdio until the client disconnects. Returns the process exit code."""
    try:
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:
        return 130
    return 0
