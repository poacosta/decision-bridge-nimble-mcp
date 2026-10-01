"""Real-subprocess MCP tests: the packaged stdio server, driven by the official SDK client."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import anyio
import jsonschema
import pytest
from mcp import Client, StdioServerParameters

from tests.fakes.fake_ollama import FakeResponse, default_decision
from tests.helpers import subprocess_env

pytestmark = pytest.mark.filterwarnings("ignore")


def server_params(tmp_path, url, **env):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "decision_bridge", "serve"],
        env={"DECISION_BRIDGE_OLLAMA_URL": url, **env},
        cwd=tmp_path,  # unrelated working directory: nothing may depend on the source tree
    )


@asynccontextmanager
async def connect(tmp_path, url, **env):
    async with Client(server_params(tmp_path, url, **env)) as client:
        yield client


def closed_port_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def question_args(mixed_example):
    return {"state": mixed_example["state"], "questions": mixed_example["questions"]}


async def test_initializes_and_lists_tools_with_ollama_offline(tmp_path):
    async with connect(tmp_path, closed_port_url()) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {"decide", "bridge_status"}
        status = await client.call_tool("bridge_status", {})
        assert status.is_error is False
        assert status.structured_content["ollama"]["state"] == "unavailable"
        assert status.structured_content["inference"]["state"] == "not_checked"


async def test_tool_metadata_schemas_and_annotations(tmp_path, fake_ollama, mixed_example):
    async with connect(tmp_path, fake_ollama.url) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    decide = tools["decide"]
    ann = decide.annotations
    assert ann.read_only_hint is True and ann.destructive_hint is False
    assert ann.idempotent_hint is None  # inference output is not promised to be repeatable
    assert set(decide.input_schema["required"]) == {"state", "questions"}
    schema_text = json.dumps(decide.input_schema)
    for variant in ('"choice"', '"noul"', '"score"'):
        assert variant in schema_text
    jsonschema.validate(question_args(mixed_example), decide.input_schema)
    bad = question_args(mixed_example)
    bad["questions"]["route"]["type"] = "rank"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, decide.input_schema)
    assert "https://ollama.com/library/nimble:latest" in decide.description
    assert decide.output_schema and "answers" in decide.output_schema["properties"]
    assert tools["bridge_status"].input_schema.get("properties", {}) == {}
    assert tools["bridge_status"].output_schema


async def test_decide_success_is_structured_and_mirrored_as_text(
    tmp_path, fake_ollama, mixed_example
):
    async with connect(tmp_path, fake_ollama.url) as client:
        result = await client.call_tool("decide", question_args(mixed_example))
    assert result.is_error is False
    data = result.structured_content
    assert data["schema_version"] == "1"
    assert list(data["answers"]) == ["route", "mentions_payment", "evidence_detail"]
    assert data["answers"]["mentions_payment"]["type"] == "noul"
    assert json.loads(result.content[0].text) == data
    (call,) = fake_ollama.calls("/v1/systemone")
    assert call.json()["model"] == "nimble:latest"
    assert list(call.json()["questions"]) == ["route", "mentions_payment", "evidence_detail"]


async def test_execution_failure_is_tool_error_with_stable_code(
    tmp_path, fake_ollama, mixed_example
):
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(500, "SECRET_BACKEND_TEXT"))
    async with connect(tmp_path, fake_ollama.url) as client:
        result = await client.call_tool("decide", question_args(mixed_example))
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "UPSTREAM_ERROR" and error["request_id"]
    assert json.loads(result.content[0].text) == result.structured_content
    assert "SECRET_BACKEND_TEXT" not in json.dumps(result.structured_content)


async def test_missing_model_is_reported_as_model_not_found(tmp_path, fake_ollama, mixed_example):
    async with connect(tmp_path, fake_ollama.url, DECISION_BRIDGE_MODEL="other:1") as client:
        result = await client.call_tool("decide", question_args(mixed_example))
    assert result.structured_content["error"]["code"] == "MODEL_NOT_FOUND"
    assert fake_ollama.calls("/v1/systemone") == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a["questions"]["route"].update(type="rank"),
        lambda a: a.update(questions={}),
        lambda a: a.update(state=5),
        lambda a: a["questions"]["route"].update(surprise=1),
    ],
)
async def test_schema_invalid_arguments_are_sdk_errors_and_never_reach_the_backend(
    tmp_path, fake_ollama, mixed_example, mutate
):
    args = question_args(mixed_example)
    mutate(args)
    async with connect(tmp_path, fake_ollama.url) as client:
        result = await client.call_tool("decide", args)
    # Argument-schema violations are rejected by the SDK before the tool body runs: an isError
    # result with plain text, not the bridge's structured error body. Documented distinction.
    assert result.is_error is True
    assert "error" not in (result.structured_content or {})
    assert fake_ollama.calls("/v1/systemone") == []


@pytest.mark.parametrize("extra", [{"model": "other:1"}, {"ollama_url": "http://evil.example"}])
async def test_unknown_arguments_are_rejected_with_structured_error(
    tmp_path, fake_ollama, mixed_example, extra
):
    async with connect(tmp_path, fake_ollama.url) as client:
        decide = await client.call_tool("decide", {**question_args(mixed_example), **extra})
        status = await client.call_tool("bridge_status", {"anything": 1})
    for result in (decide, status):
        assert result.is_error is True
        assert result.structured_content["error"]["code"] == "INVALID_INPUT"
        assert "Unsupported argument" in result.structured_content["error"]["message"]
    assert fake_ollama.requests == []  # neither diagnostics nor inference ran


async def test_bridge_status_never_runs_inference(tmp_path, fake_ollama):
    async with connect(tmp_path, fake_ollama.url) as client:
        first = await client.call_tool("bridge_status", {})
        second = await client.call_tool("bridge_status", {})
    assert fake_ollama.calls("/v1/systemone") == []
    data = second.structured_content
    assert first.structured_content["ollama"] == {"state": "available", "version": "0.35.0"}
    assert data["model_status"]["state"] == "available"
    assert data["model_status"]["decision_capable"] is True
    assert "some-cloud" not in json.dumps(data)


async def test_cancelling_a_call_frees_the_capacity_slot(tmp_path, fake_ollama, mixed_example):
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(body={"x": 1}, delay=60))
    env = {"DECISION_BRIDGE_MAX_CONCURRENCY": "1"}
    async with connect(tmp_path, fake_ollama.url, **env) as client:
        async with anyio.create_task_group() as tg:

            async def call():
                await client.call_tool("decide", question_args(mixed_example))

            tg.start_soon(call)
            # Cancel only once the request is really in flight, however slow the machine is.
            with anyio.fail_after(30):
                # The fake server's thread sets this condition, not an anyio event.
                while not fake_ollama.calls("/v1/systemone"):  # noqa: ASYNC110
                    await anyio.sleep(0.05)
            tg.cancel_scope.cancel()
        assert len(fake_ollama.calls("/v1/systemone")) == 1
        fake_ollama.set("POST", "/v1/systemone", default_decision)
        with anyio.fail_after(30):  # a leaked slot would leave this waiting for the 120 s deadline
            result = await client.call_tool("decide", question_args(mixed_example))
    assert result.is_error is False


def raw_server(tmp_path, url):
    return subprocess.Popen(
        [sys.executable, "-m", "decision_bridge", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=subprocess_env(DECISION_BRIDGE_OLLAMA_URL=url),
    )


def send(proc, message):
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def test_stdout_carries_only_protocol_messages_and_shutdown_is_clean(
    tmp_path, fake_ollama, mixed_example
):
    proc = raw_server(tmp_path, fake_ollama.url)
    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "raw", "version": "0"},
                },
            },
        )
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "decide", "arguments": question_args(mixed_example)},
            },
        )
        seen = {}
        deadline = time.monotonic() + 20
        while len(seen) < 3 and time.monotonic() < deadline:
            line = proc.stdout.readline()
            assert line, "server closed stdout early"
            message = json.loads(line)  # any non-JSON line on stdout fails right here
            assert message["jsonrpc"] == "2.0"
            seen[message["id"]] = message
        assert set(seen) == {1, 2, 3}
        assert seen[3]["result"]["isError"] is False
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
        assert proc.stdout.read() == b""
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()


def test_no_banner_or_prompt_on_stdout_when_stdin_closes_immediately(tmp_path, fake_ollama):
    proc = raw_server(tmp_path, fake_ollama.url)
    out, _ = proc.communicate(input=b"", timeout=15)
    assert proc.returncode == 0 and out == b""


@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
def test_verbose_server_logs_never_contain_evidence_or_answers(tmp_path, fake_ollama, level):
    secret_evidence = "TOP-SECRET-EVIDENCE-XYZ"
    request = {
        "state": secret_evidence,
        "questions": {
            "q": {
                "type": "choice",
                "instructions": "SECRET-INSTRUCTIONS-QRS",
                "criteria": {"alpha-label": "SECRET-DESCRIPTION-ABC", "beta-label": None},
            }
        },
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "decision_bridge", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=subprocess_env(
            DECISION_BRIDGE_OLLAMA_URL=fake_ollama.url,
            DECISION_BRIDGE_LOG_LEVEL=level,
        ),
    )
    try:
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "raw", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "decide", "arguments": request},
            },
        ):
            send(proc, message)
        replies = {}
        while len(replies) < 2:
            line = proc.stdout.readline()
            assert line
            msg = json.loads(line)
            replies[msg["id"]] = msg
        assert replies[2]["result"]["isError"] is False
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
        stderr = proc.stderr.read().decode()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
    for secret in (
        secret_evidence,
        "SECRET-INSTRUCTIONS-QRS",
        "SECRET-DESCRIPTION-ABC",
        "alpha-label",
    ):
        assert secret not in stderr, f"{secret!r} leaked into stderr at {level}"
