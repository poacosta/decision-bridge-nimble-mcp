"""Command-line entry point: `serve`, `doctor`, `decide`, and `--version`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from typing import Any

from decision_bridge import __version__
from decision_bridge.config import Settings, load_settings
from decision_bridge.diagnostics import run_doctor
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.service import DecisionService

# Raw input cap for `decide`. The serialized request is limited to 64 KiB separately; this bound
# only stops a runaway file or pipe from being read into memory, leaving room for whitespace.
MAX_INPUT_BYTES = 256 * 1024


def _connection_options() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("connection (override DECISION_BRIDGE_* environment)")
    group.add_argument("--ollama-url", help="Ollama origin, e.g. http://127.0.0.1:11434")
    group.add_argument("--model", help="Installed local Nimble model tag")
    group.add_argument("--connect-timeout", help="Connect timeout in seconds")
    group.add_argument("--request-timeout", help="Total decision deadline in seconds")
    group.add_argument("--keep-alive", help="Ollama keep_alive duration, e.g. 5m")
    group.add_argument("--max-concurrency", help="Maximum in-flight model calls")
    group.add_argument(
        "--allow-remote",
        action="store_const",
        const=True,
        default=None,
        help="Permit a non-loopback Ollama origin",
    )
    group.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-bridge",
        description="Ask a local Nimble model, through Ollama, for typed decisions.",
    )
    parser.add_argument("--version", action="version", version=f"decision-bridge {__version__}")
    common = _connection_options()
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "serve", parents=[common], help="Run the MCP server over stdio (protocol on stdout)."
    )
    doctor = sub.add_parser(
        "doctor", parents=[common], help="Check configuration, Ollama, and the configured model."
    )
    doctor.add_argument("--json", action="store_true", help="Machine-readable output.")
    doctor.add_argument(
        "--smoke-test",
        action="store_true",
        help="Also send one tiny synthetic decision request (may load the model).",
    )
    decide = sub.add_parser(
        "decide", parents=[common], help="Run one decision request from a JSON file or stdin."
    )
    decide.add_argument(
        "--input", required=True, metavar="PATH|-", help="JSON file, or - for stdin"
    )
    return parser


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    keys = (
        "ollama_url",
        "model",
        "connect_timeout",
        "request_timeout",
        "keep_alive",
        "max_concurrency",
        "allow_remote",
        "log_level",
    )
    return {key: getattr(args, key, None) for key in keys}


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # Transport libraries log URLs and connection details; keep them quiet regardless of level.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _fail(error: BridgeError) -> int:
    print(json.dumps(error.to_payload(), ensure_ascii=True), file=sys.stderr)
    return error.exit_code


def _read_input(spec: str) -> str:
    try:
        if spec == "-":
            data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with open(spec, "rb") as handle:
                data = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise BridgeError(
            ErrorCode.INVALID_INPUT, f"Cannot read the input: {exc.strerror or 'I/O error'}."
        ) from None
    if len(data) > MAX_INPUT_BYTES:
        raise BridgeError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"Input is larger than {MAX_INPUT_BYTES} bytes; reduce the evidence or questions.",
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BridgeError(ErrorCode.INVALID_INPUT, "Input is not valid UTF-8.") from None


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    service = DecisionService(settings)
    try:
        if args.command == "decide":
            result = await service.decide(_read_input(args.input))
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        report = await run_doctor(service, smoke_test=args.smoke_test)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=True, indent=2))
        else:
            print(report.render_text())
        return 0 if report.ok else 1
    except BridgeError as exc:
        return _fail(exc)
    finally:
        await service.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command is None:
        parser.print_help(sys.stdout)
        return 0
    reconfigure: Any = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    try:
        settings = load_settings(os.environ, _overrides(args))
    except BridgeError as exc:
        return _fail(exc)
    _configure_logging(settings)
    if args.command == "serve":
        from decision_bridge.server import run_stdio

        return run_stdio(settings)
    try:
        return asyncio.run(_run(args, settings))
    except KeyboardInterrupt:
        return 130
