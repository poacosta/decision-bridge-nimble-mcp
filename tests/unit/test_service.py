import asyncio
import logging
import socket
import threading
import time

import pytest

from decision_bridge.config import Settings
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import build_request_body, parse_decision_input
from decision_bridge.service import QUEUE_DEPTH, DecisionService
from tests.fakes.fake_ollama import FakeResponse, default_decision, default_tags

REQ = {
    "state": "SECRET_EVIDENCE",
    "questions": {
        "q": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"a": "first", "b": "second"},
        }
    },
}


@pytest.fixture
async def make_service(fake_ollama):
    made = []

    def make(**kw):
        service = DecisionService(Settings(ollama_url=fake_ollama.url, **kw))
        made.append(service)
        return service

    yield make
    for service in made:
        await service.aclose()


async def wait_for(predicate, limit=3.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def slow(delay):
    return FakeResponse(body={"never": "used"}, delay=delay)


async def code_of(coro):
    with pytest.raises(BridgeError) as exc:
        await coro
    return exc.value


async def test_success_envelope_and_exact_bytes_sent(make_service, fake_ollama):
    service = make_service()
    out = await service.decide(REQ)
    assert out["schema_version"] == "1"
    assert out["model"] == "nimble:latest"
    assert out["answers"]["q"]["choice"] == "a"
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert isinstance(out["metadata"]["duration_ms"], int)
    assert out["metadata"]["request_id"]
    (call,) = fake_ollama.calls("/v1/systemone")
    assert call.body == build_request_body(parse_decision_input(REQ), "nimble:latest", None)


async def test_keep_alive_forwarded_only_when_configured(make_service, fake_ollama):
    await make_service(keep_alive="10m").decide(REQ)
    assert fake_ollama.calls("/v1/systemone")[-1].json()["keep_alive"] == "10m"
    await make_service().decide(REQ)
    assert "keep_alive" not in fake_ollama.calls("/v1/systemone")[-1].json()


async def test_json_text_input_accepted(make_service):
    import json

    assert (await make_service().decide(json.dumps(REQ)))["answers"]["q"]["type"] == "choice"


async def test_invalid_input_never_reaches_backend(make_service, fake_ollama):
    err = await code_of(make_service().decide({"state": 5, "questions": {}}))
    assert err.code is ErrorCode.INVALID_INPUT
    assert fake_ollama.calls("/v1/systemone") == []
    assert err.request_id


async def test_duration_includes_backend_time(make_service, fake_ollama):
    def handler(rec):
        time.sleep(0.2)
        return default_decision(rec)

    fake_ollama.set("POST", "/v1/systemone", handler)
    out = await make_service().decide(REQ)
    assert out["metadata"]["duration_ms"] >= 190


async def test_busy_when_queue_is_full_and_recovers(make_service, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", slow(30))
    service = make_service(max_concurrency=1, request_timeout=60)
    tasks = [asyncio.create_task(service.decide(REQ)) for _ in range(1 + QUEUE_DEPTH)]
    await wait_for(lambda: len(fake_ollama.calls("/v1/systemone")) == 1)
    await asyncio.sleep(0.05)
    err = await code_of(service.decide(REQ))
    assert err.code is ErrorCode.BUSY and err.retryable is True
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    fake_ollama.set("POST", "/v1/systemone", default_decision)
    assert (await service.decide(REQ))["answers"]


async def test_cancellation_releases_slot_and_pending_count(make_service, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", slow(30))
    service = make_service(max_concurrency=1, request_timeout=60)
    for _ in range(3):  # more cycles than the pending cap would allow if slots leaked
        tasks = [asyncio.create_task(service.decide(REQ)) for _ in range(1 + QUEUE_DEPTH)]
        await asyncio.sleep(0.1)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    fake_ollama.set("POST", "/v1/systemone", default_decision)
    assert (await asyncio.wait_for(service.decide(REQ), 3))["answers"]


async def test_timeout_while_running(make_service, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", slow(30))
    started = time.monotonic()
    err = await code_of(make_service(request_timeout=0.4).decide(REQ))
    assert err.code is ErrorCode.TIMEOUT and err.retryable is True
    assert time.monotonic() - started < 3


async def test_timeout_while_queued(make_service, fake_ollama):
    service = make_service(max_concurrency=1, request_timeout=0.4)
    await service._semaphore.acquire()  # deterministically occupy the only slot
    try:
        err = await code_of(service.decide(REQ))
    finally:
        service._semaphore.release()
    assert err.code is ErrorCode.TIMEOUT
    assert fake_ollama.calls("/v1/systemone") == []  # the queued call never reached Ollama
    assert (await service.decide(REQ))["answers"]  # and the service is healthy afterwards


@pytest.mark.parametrize("limit", [1, 2])
async def test_concurrency_bound_is_respected(make_service, fake_ollama, limit):
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def handler(rec):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return default_decision(rec)

    fake_ollama.set("POST", "/v1/systemone", handler)
    service = make_service(max_concurrency=limit)
    await asyncio.gather(*(service.decide(REQ) for _ in range(4)))
    assert state["max"] == limit


async def test_model_verdict_cached_and_refreshed_after_model_not_found(make_service, fake_ollama):
    service = make_service()
    await service.decide(REQ)
    await service.decide(REQ)
    assert len(fake_ollama.calls("/api/tags")) == 1
    fake_ollama.set(
        "POST",
        "/v1/systemone",
        FakeResponse(404, {"error": 'model "nimble:latest" not found, try pulling it first'}),
    )
    assert (await code_of(service.decide(REQ))).code is ErrorCode.MODEL_NOT_FOUND
    fake_ollama.set("POST", "/v1/systemone", default_decision)
    await service.decide(REQ)
    assert len(fake_ollama.calls("/api/tags")) == 2


async def test_missing_model_reported_without_inference_and_not_cached(make_service, fake_ollama):
    service = make_service(model="other:1")
    err = await code_of(service.decide(REQ))
    assert err.code is ErrorCode.MODEL_NOT_FOUND
    assert fake_ollama.calls("/v1/systemone") == []
    await code_of(service.decide(REQ))
    assert len(fake_ollama.calls("/api/tags")) == 2  # negative verdicts are re-checked


async def test_remote_model_refused(make_service, fake_ollama):
    err = await code_of(make_service(model="some-cloud:cloud").decide(REQ))
    assert err.code is ErrorCode.MODEL_NOT_LOCAL
    assert fake_ollama.calls("/v1/systemone") == []


async def test_model_without_decision_capability(make_service, fake_ollama):
    def tags(rec):
        resp = default_tags(rec)
        resp.body["models"][0]["capabilities"] = ["completion"]
        return resp

    fake_ollama.set("GET", "/api/tags", tags)
    err = await code_of(make_service().decide(REQ))
    assert err.code is ErrorCode.DECISION_UNSUPPORTED
    assert fake_ollama.calls("/v1/systemone") == []


async def test_unknown_capabilities_do_not_block(make_service, fake_ollama):
    def tags(rec):
        resp = default_tags(rec)
        del resp.body["models"][0]["capabilities"]
        return resp

    fake_ollama.set("GET", "/api/tags", tags)
    assert (await make_service().decide(REQ))["answers"]


async def test_unavailable_model_list_does_not_block_decision(make_service, fake_ollama):
    fake_ollama.routes.pop(("GET", "/api/tags"))
    assert (await make_service().decide(REQ))["answers"]


def closed_port_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


async def test_offline_ollama_is_a_clean_error_and_status_is_data():
    service = DecisionService(Settings(ollama_url=closed_port_url()))
    try:
        err = await code_of(service.decide(REQ))
        assert err.code is ErrorCode.OLLAMA_UNAVAILABLE
        status = await service.status()
        assert status["ollama"]["state"] == "unavailable"
        assert status["inference"]["state"] == "failed"
    finally:
        await service.aclose()


async def test_invalid_upstream_response_is_never_a_success(make_service, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(body={"answers": {}}))
    service = make_service()
    err = await code_of(service.decide(REQ))
    assert err.code is ErrorCode.INVALID_UPSTREAM_RESPONSE
    assert (await service.status())["inference"]["state"] == "failed"


async def test_status_makes_no_inference_and_reports_states(make_service, fake_ollama):
    service = make_service()
    before = (await service.status())["inference"]["state"]
    assert before == "not_checked"
    status = await service.status()
    assert fake_ollama.calls("/v1/systemone") == []
    assert status["bridge_version"] == "0.1.0"
    assert status["model"] == "nimble:latest"
    assert status["ollama"] == {"state": "available", "version": "0.35.0"}
    assert status["model_status"]["state"] == "available"
    await service.decide(REQ)
    assert (await service.status())["inference"]["state"] == "verified"


async def test_status_does_not_dump_other_models(make_service):
    text = str(await make_service().status())
    assert "some-cloud" not in text


async def test_logs_carry_metadata_only(make_service, fake_ollama, caplog):
    caplog.set_level(logging.DEBUG, logger="decision_bridge")
    service = make_service()
    ok = await service.decide(REQ)
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(500, "SECRET_UPSTREAM_BODY"))
    err = await code_of(service.decide(REQ))
    assert ok["metadata"]["request_id"] in caplog.text
    assert err.request_id in caplog.text
    assert "UPSTREAM_ERROR" in caplog.text
    for secret in ("SECRET_EVIDENCE", "SECRET_UPSTREAM_BODY", "first", '"choice"'):
        assert secret not in caplog.text


async def test_service_output_matches_advertised_envelope_models(make_service):
    from decision_bridge.envelope import BridgeStatus, DecisionEnvelope

    service = make_service()
    envelope = DecisionEnvelope.model_validate(await service.decide(REQ))
    assert envelope.answers["q"].type == "choice"
    BridgeStatus.model_validate(await service.status())
