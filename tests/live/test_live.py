"""Live checks against a real Ollama with the model already installed.

Opt-in: set DECISION_BRIDGE_LIVE=1 and run `pytest -m live`. Nothing is ever pulled or started.
These validate protocol and contract behavior; they deliberately never assert which label the
model prefers, so a probabilistic answer cannot make them flaky.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from mcp import Client, StdioServerParameters

from decision_bridge import __version__
from decision_bridge.config import load_settings
from decision_bridge.diagnostics import run_doctor
from decision_bridge.service import DecisionService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("DECISION_BRIDGE_LIVE") != "1",
        reason="live tests are opt-in: set DECISION_BRIDGE_LIVE=1",
    ),
]

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"
REPORT = Path(os.environ.get("DECISION_BRIDGE_LIVE_REPORT", "live-report.json"))


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def settings():
    return load_settings(os.environ)


@pytest.fixture(scope="module")
def run_log(settings):
    """Collect environment facts before the first request, and write a report afterwards."""
    facts: dict = {
        "bridge_version": __version__,
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "model": settings.model,
        "endpoint": settings.ollama_url,
    }
    with httpx.Client(base_url=settings.ollama_url, timeout=10, trust_env=False) as http:
        facts["ollama_version"] = http.get("/api/version").json().get("version")
        tags = http.get("/api/tags").json().get("models", [])
        facts["model_digest"] = next(
            (m.get("digest") for m in tags if m.get("name") == settings.model), None
        )
        loaded = [m.get("name") for m in http.get("/api/ps").json().get("models", [])]
        facts["model_loaded_before_first_request"] = settings.model in loaded
    facts["start"] = "warm" if facts["model_loaded_before_first_request"] else "cold"
    results: list[dict] = []
    yield facts, results
    REPORT.write_text(json.dumps({"environment": facts, "results": results}, indent=2))


@pytest.fixture
async def service(settings):
    svc = DecisionService(settings)
    yield svc
    await svc.aclose()


def check_contract(request: dict, result: dict) -> None:
    assert result["schema_version"] == "1"
    assert list(result["answers"]) == list(request["questions"])
    for qid, question in request["questions"].items():
        answer = result["answers"][qid]
        assert answer["type"] == question["type"]
        if question["type"] == "choice":
            assert answer["choice"] in question["criteria"]
            assert set(answer["probabilities"]) == set(question["criteria"])
        elif question["type"] == "noul":
            assert 0.0 <= answer["noul"] <= 1.0
        else:
            assert 0.0 <= answer["score"] <= len(question["criteria"]) - 1
            assert set(answer["legend"]) == {str(i) for i in range(len(question["criteria"]))}


@pytest.mark.parametrize(
    "name",
    ["task-routing.json", "evidence-check.json", "relevance-score.json", "mixed-questions.json"],
)
async def test_live_decision_for_each_example(service, run_log, name):
    request = example(name)
    result = await service.decide(request)
    check_contract(request, result)
    run_log[1].append(
        {
            "example": name,
            "duration_ms": result["metadata"]["duration_ms"],
            "usage": result.get("usage"),
        }
    )


async def test_live_bridge_status_reports_a_usable_model(service):
    status = await service.status()
    assert status["ollama"]["state"] == "available"
    assert status["model_status"]["state"] == "available"
    assert status["model_status"]["local"] is True


async def test_live_doctor_smoke_test_verifies_inference(service):
    report = await run_doctor(service, smoke_test=True)
    assert report.ok and report.inference_verified


def test_live_mcp_stdio_round_trip(tmp_path, settings, run_log):
    request = example("mixed-questions.json")

    async def call():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "decision_bridge", "serve"],
            cwd=tmp_path,
            env={
                "DECISION_BRIDGE_OLLAMA_URL": settings.ollama_url,
                "DECISION_BRIDGE_MODEL": settings.model,
            },
        )
        async with Client(params) as client:
            return await client.call_tool("decide", request)

    result = anyio.run(call)
    assert result.is_error is False
    check_contract(request, result.structured_content)
