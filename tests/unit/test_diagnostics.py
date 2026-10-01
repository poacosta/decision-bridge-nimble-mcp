import json
import socket

import pytest

from decision_bridge.config import Settings
from decision_bridge.diagnostics import parse_version, run_doctor
from decision_bridge.service import DecisionService
from tests.fakes.fake_ollama import FakeResponse, default_decision, default_tags


@pytest.fixture
async def make_service(fake_ollama):
    made = []

    def make(**kw):
        kw.setdefault("ollama_url", fake_ollama.url)
        service = DecisionService(Settings(**kw))
        made.append(service)
        return service

    yield make
    for service in made:
        await service.aclose()


def states(report):
    return {c.name: c.state for c in report.checks}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.35.0", (0, 35, 0)),
        ("0.35", (0, 35, 0)),
        ("0.36.1-rc2", (0, 36, 1)),
        ("garbage", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


async def test_healthy_without_smoke_test_is_ok_but_inference_unverified(make_service, fake_ollama):
    report = await run_doctor(make_service(), smoke_test=False)
    assert report.ok is True
    assert report.inference_verified is False
    assert set(states(report).values()) == {"available"} | {"not_checked"}
    assert states(report)["decision_smoke_test"] == "not_checked"
    assert fake_ollama.calls("/v1/systemone") == []
    assert "inference readiness is unverified" in report.render_text().lower()


async def test_smoke_test_success_verifies_inference(make_service, fake_ollama):
    report = await run_doctor(make_service(), smoke_test=True)
    assert report.ok and report.inference_verified
    assert len(fake_ollama.calls("/v1/systemone")) == 1
    assert "unverified" not in report.render_text().lower()


async def test_smoke_test_failure_makes_report_not_ok(make_service, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(body={"answers": {}}))
    report = await run_doctor(make_service(), smoke_test=True)
    assert report.ok is False and report.inference_verified is False
    assert states(report)["decision_smoke_test"] == "unavailable"
    assert "INVALID_UPSTREAM_RESPONSE" in report.render_text()


async def test_smoke_test_result_validated_not_label_judged(make_service, fake_ollama):
    def handler(rec):
        resp = default_decision(rec)
        first = resp.body["answers"]
        for a in first.values():  # pick a different label: still a valid decision
            labels = list(a["probabilities"])
            a["choice"] = labels[-1]
        return resp

    fake_ollama.set("POST", "/v1/systemone", handler)
    assert (await run_doctor(make_service(), smoke_test=True)).ok


async def test_unreachable_ollama(make_service):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{s.getsockname()[1]}"
    report = await run_doctor(make_service(ollama_url=url), smoke_test=False)
    assert report.ok is False
    assert states(report)["ollama_reachable"] == "unavailable"
    assert states(report)["model_installed"] == "unknown"
    assert "ollama" in report.render_text().lower()


async def test_old_ollama_version_flagged(make_service, fake_ollama):
    fake_ollama.set("GET", "/api/version", FakeResponse(body={"version": "0.34.9"}))
    report = await run_doctor(make_service(), smoke_test=False)
    assert states(report)["ollama_version"] == "unavailable"
    assert report.ok is False


async def test_missing_model_hint_names_pull_command(make_service):
    report = await run_doctor(make_service(model="other:1"), smoke_test=False)
    assert states(report)["model_installed"] == "unavailable"
    assert "ollama pull other:1" in report.render_text()


async def test_capability_states(make_service, fake_ollama):
    def tags(rec):
        resp = default_tags(rec)
        resp.body["models"][0]["capabilities"] = ["completion"]
        return resp

    fake_ollama.set("GET", "/api/tags", tags)
    assert (
        states(await run_doctor(make_service(), smoke_test=False))["model_decision_capable"]
        == "unavailable"
    )

    def no_caps(rec):
        resp = default_tags(rec)
        del resp.body["models"][0]["capabilities"]
        return resp

    fake_ollama.set("GET", "/api/tags", no_caps)
    assert (
        states(await run_doctor(make_service(), smoke_test=False))["model_decision_capable"]
        == "unknown"
    )


async def test_json_report_is_machine_readable_without_ansi(make_service):
    report = await run_doctor(make_service(), smoke_test=False)
    text = json.dumps(report.to_dict())
    assert "\x1b[" not in text and "\x1b[" not in report.render_text()
    data = json.loads(text)
    assert data["ok"] is True and data["inference_verified"] is False
    assert {"name", "state", "detail"} <= set(data["checks"][0])
