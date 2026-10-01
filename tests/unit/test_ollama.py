import logging

import httpx
import pytest

from decision_bridge.config import Settings
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.ollama import MAX_RESPONSE_BYTES, OllamaClient

BODY = b'{"model":"nimble:latest","state":"x","questions":{}}'


def client_with(handler, **settings):
    return OllamaClient(Settings(**settings), transport=httpx.MockTransport(handler))


async def post(handler, **settings):
    c = client_with(handler, **settings)
    try:
        return await c.post_decision(BODY)
    finally:
        await c.aclose()


async def code_of(handler, **settings):
    with pytest.raises(BridgeError) as exc:
        await post(handler, **settings)
    return exc.value


async def test_request_url_method_headers_and_exact_bytes():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"answers": {}})

    assert await post(handler) == {"answers": {}}
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "http://127.0.0.1:11434/v1/systemone"
    assert req.headers["content-type"] == "application/json"
    assert req.content == BODY
    assert "authorization" not in req.headers


async def test_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(302, headers={"location": "http://evil.example/v1/systemone"})

    err = await code_of(handler)
    assert err.code is ErrorCode.UPSTREAM_ERROR
    assert seen == ["/v1/systemone"]


@pytest.mark.parametrize(
    "response,code",
    [
        (
            httpx.Response(404, json={"error": 'model "nope:1" not found, try pulling it first'}),
            ErrorCode.MODEL_NOT_FOUND,
        ),
        (
            httpx.Response(404, text="404 page not found", headers={"content-type": "text/plain"}),
            ErrorCode.OLLAMA_INCOMPATIBLE,
        ),
        (httpx.Response(404, json={"error": "something else"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(413, text="too big"), ErrorCode.PAYLOAD_TOO_LARGE),
        (
            httpx.Response(400, json={"error": "prompt exceeds the context window of 8192 tokens"}),
            ErrorCode.CONTEXT_LIMIT,
        ),
        (httpx.Response(400, json={"error": "input has too many tokens"}), ErrorCode.CONTEXT_LIMIT),
        (
            httpx.Response(400, json={"error": "model does not support decision requests"}),
            ErrorCode.DECISION_UNSUPPORTED,
        ),
        (
            httpx.Response(400, json={"error": "questions must contain 1–64 fields"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(401, json={"error": "unauthorized"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(500, text="boom"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(503, text="busy"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, text="<html>not json</html>"), ErrorCode.INVALID_UPSTREAM_RESPONSE),
        (httpx.Response(200, json=["not", "an", "object"]), ErrorCode.INVALID_UPSTREAM_RESPONSE),
        (httpx.Response(200, content=b""), ErrorCode.INVALID_UPSTREAM_RESPONSE),
    ],
)
async def test_status_and_body_mapping(response, code):
    err = await code_of(lambda request: response)
    assert err.code is code


async def test_503_is_retryable_500_is_not():
    assert (await code_of(lambda r: httpx.Response(503))).retryable is True
    assert (await code_of(lambda r: httpx.Response(500))).retryable is False


@pytest.mark.parametrize(
    "exc,code",
    [
        (httpx.ConnectError("refused"), ErrorCode.OLLAMA_UNAVAILABLE),
        (httpx.ConnectTimeout("slow"), ErrorCode.OLLAMA_UNAVAILABLE),
        (httpx.ReadTimeout("slow"), ErrorCode.TIMEOUT),
        (httpx.RemoteProtocolError("closed"), ErrorCode.UPSTREAM_ERROR),
        (httpx.ReadError("reset"), ErrorCode.UPSTREAM_ERROR),
    ],
)
async def test_transport_failures(exc, code):
    def handler(request):
        raise exc

    assert (await code_of(handler)).code is code


async def test_oversized_response_rejected_by_streaming_count():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(MAX_RESPONSE_BYTES // 65536 + 2):
                yield b"a" * 65536

    err = await code_of(lambda r: httpx.Response(200, stream=Stream()))
    assert err.code is ErrorCode.INVALID_UPSTREAM_RESPONSE


async def test_oversized_declared_content_length_rejected():
    err = await code_of(
        lambda r: httpx.Response(
            200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}, content=b"{}"
        )
    )
    assert err.code is ErrorCode.INVALID_UPSTREAM_RESPONSE


async def test_raw_upstream_error_body_never_leaks(caplog):
    caplog.set_level(logging.DEBUG)
    err = await code_of(lambda r: httpx.Response(500, text="SECRET_UPSTREAM_TOKEN stack trace"))
    assert "SECRET_UPSTREAM_TOKEN" not in err.message
    assert "SECRET_UPSTREAM_TOKEN" not in str(err.to_payload())
    assert "SECRET_UPSTREAM_TOKEN" not in caplog.text


async def test_get_version_and_models():
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.0"})
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "nimble:latest", "digest": "abc", "capabilities": ["decision"]},
                    {
                        "name": "x:cloud",
                        "remote_model": "x",
                        "remote_host": "https://ollama.com:443",
                    },
                    {"name": "plain:1"},
                    {"digest": "nameless"},
                ]
            },
        )

    c = client_with(handler)
    assert await c.get_version() == "0.35.0"
    models = {m.name: m for m in await c.list_models()}
    await c.aclose()
    assert set(models) == {"nimble:latest", "x:cloud", "plain:1"}
    assert models["nimble:latest"].capabilities == ("decision",)
    assert models["nimble:latest"].digest == "abc"
    assert models["nimble:latest"].is_remote is False
    assert models["x:cloud"].is_remote is True
    assert models["plain:1"].capabilities == ()


async def test_diagnostic_calls_raise_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    c = client_with(handler)
    for call in (c.get_version, c.list_models):
        with pytest.raises(BridgeError) as exc:
            await call()
        assert exc.value.code is ErrorCode.OLLAMA_UNAVAILABLE
    await c.aclose()


async def test_diagnostic_calls_reject_garbage():
    c = client_with(lambda r: httpx.Response(200, text="nope"))
    with pytest.raises(BridgeError) as exc:
        await c.list_models()
    assert exc.value.code is ErrorCode.INVALID_UPSTREAM_RESPONSE
    await c.aclose()


async def test_environment_proxies_are_ignored(fake_ollama, monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    c = OllamaClient(Settings(ollama_url=fake_ollama.url))
    try:
        assert await c.get_version() == "0.35.0"
    finally:
        await c.aclose()
