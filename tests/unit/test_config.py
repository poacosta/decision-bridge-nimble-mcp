import pytest

from decision_bridge.config import load_settings
from decision_bridge.errors import BridgeError, ErrorCode


def test_defaults():
    s = load_settings({})
    assert s.ollama_url == "http://127.0.0.1:11434"
    assert s.model == "nimble:latest"
    assert s.connect_timeout == 5
    assert s.request_timeout == 120
    assert s.keep_alive is None
    assert s.max_concurrency == 1
    assert s.allow_remote is False
    assert s.log_level == "WARNING"


def test_env_beats_default_and_override_beats_env():
    env = {"DECISION_BRIDGE_MODEL": "nimble:q4", "DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS": "30"}
    assert load_settings(env).model == "nimble:q4"
    assert load_settings(env).request_timeout == 30
    assert load_settings(env, {"model": "nimble:q8"}).model == "nimble:q8"
    assert load_settings(env, {"model": None}).model == "nimble:q4"


@pytest.mark.parametrize(
    "var,value",
    [
        ("DECISION_BRIDGE_CONNECT_TIMEOUT_SECONDS", "abc"),
        ("DECISION_BRIDGE_CONNECT_TIMEOUT_SECONDS", "0"),
        ("DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS", "-3"),
        ("DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS", "nan"),
        ("DECISION_BRIDGE_MAX_CONCURRENCY", "0"),
        ("DECISION_BRIDGE_MAX_CONCURRENCY", "33"),
        ("DECISION_BRIDGE_MAX_CONCURRENCY", "1.5"),
        ("DECISION_BRIDGE_ALLOW_REMOTE", "maybe"),
        ("DECISION_BRIDGE_KEEP_ALIVE", "abc"),
        ("DECISION_BRIDGE_KEEP_ALIVE", "5 minutes"),
        ("DECISION_BRIDGE_LOG_LEVEL", "LOUD"),
        ("DECISION_BRIDGE_MODEL", "  "),
    ],
)
def test_invalid_values_raise_invalid_configuration(var, value):
    with pytest.raises(BridgeError) as exc:
        load_settings({var: value})
    assert exc.value.code is ErrorCode.INVALID_CONFIGURATION
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("value", ["5m", "1h30m", "0", "-1", "300", "2.5s"])
def test_keep_alive_accepts_duration_syntax(value):
    assert load_settings({"DECISION_BRIDGE_KEEP_ALIVE": value}).keep_alive == value


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_bool_true_forms(value):
    url = "http://192.168.1.5:11434"
    env = {"DECISION_BRIDGE_ALLOW_REMOTE": value, "DECISION_BRIDGE_OLLAMA_URL": url}
    assert load_settings(env).allow_remote is True


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/?a=1",
        "http://127.0.0.1:11434/#frag",
        "http://127.0.0.1:11434/prefix",
        "ftp://127.0.0.1:11434",
        "127.0.0.1:11434",
        "http://",
        "http://127.0.0.1:99999",
    ],
)
def test_bad_urls_rejected(url):
    with pytest.raises(BridgeError) as exc:
        load_settings({"DECISION_BRIDGE_OLLAMA_URL": url})
    assert exc.value.code is ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://127.0.0.2:1",
    ],
)
def test_loopback_allowed_by_default(url):
    assert load_settings({"DECISION_BRIDGE_OLLAMA_URL": url}).ollama_url == url


def test_trailing_slash_is_normalised():
    s = load_settings({"DECISION_BRIDGE_OLLAMA_URL": "http://localhost:11434/"})
    assert s.ollama_url == "http://localhost:11434"


@pytest.mark.parametrize(
    "url", ["http://192.168.1.5:11434", "http://ollama.internal:11434", "https://example.com"]
)
def test_remote_rejected_unless_enabled(url):
    with pytest.raises(BridgeError) as exc:
        load_settings({"DECISION_BRIDGE_OLLAMA_URL": url})
    assert exc.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "ALLOW_REMOTE" in exc.value.message
    assert (
        load_settings(
            {"DECISION_BRIDGE_OLLAMA_URL": url, "DECISION_BRIDGE_ALLOW_REMOTE": "true"}
        ).ollama_url
        == url
    )


@pytest.mark.parametrize("model", ["qwen3-coder:480b-cloud", "foo:cloud", "x-cloud"])
def test_cloud_model_selectors_rejected(model):
    with pytest.raises(BridgeError) as exc:
        load_settings({"DECISION_BRIDGE_MODEL": model})
    assert exc.value.code is ErrorCode.MODEL_NOT_LOCAL
