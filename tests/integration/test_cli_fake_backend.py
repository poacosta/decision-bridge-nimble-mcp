import io
import json
import socket
import subprocess
import sys
import time

import pytest

from decision_bridge.cli import MAX_INPUT_BYTES, main
from tests.fakes.fake_ollama import FakeResponse
from tests.helpers import subprocess_env

REQ = {
    "state": "Please look into the failing payment.",
    "questions": {
        "route": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"routine": "mechanical", "investigate": "needs work"},
        },
        "mentions_payment": {"type": "noul", "instructions": "payment mentioned?"},
    },
}


@pytest.fixture(autouse=True)
def backend_env(monkeypatch, fake_ollama):
    for name in list(__import__("os").environ):
        if name.startswith("DECISION_BRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DECISION_BRIDGE_OLLAMA_URL", fake_ollama.url)


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def last_json(stderr):
    """The error payload is the last stderr line; earlier lines are metadata-only log records."""
    return json.loads(stderr.strip().splitlines()[-1])


def write(tmp_path, name="request.json", data=REQ):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_decide_from_file_with_spaces_and_unicode(tmp_path, capsys):
    path = write(tmp_path, "mi decisión 日本 .json")
    code, out, err = run(capsys, "decide", "--input", str(path))
    assert code == 0 and err == ""
    result = json.loads(out)
    assert result["schema_version"] == "1"
    assert set(result["answers"]) == {"route", "mentions_payment"}


def test_decide_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(REQ).encode())))
    code, out, _ = run(capsys, "decide", "--input", "-")
    assert code == 0
    assert json.loads(out)["answers"]["route"]["type"] == "choice"


def test_decide_reads_utf8_bom_files(tmp_path, capsys):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(REQ).encode())
    assert run(capsys, "decide", "--input", str(path))[0] == 0


def test_oversized_stdin_is_rejected_before_any_backend_call(monkeypatch, capsys, fake_ollama):
    blob = b" " * (MAX_INPUT_BYTES + 10)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(blob)))
    code, out, err = run(capsys, "decide", "--input", "-")
    assert code == 2 and out == ""
    assert last_json(err)["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert fake_ollama.requests == []


def test_oversized_file_rejected(tmp_path, capsys):
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (MAX_INPUT_BYTES + 10))
    code, _, err = run(capsys, "decide", "--input", str(path))
    assert code == 2 and last_json(err)["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_duplicate_keys_in_file_exit_2(tmp_path, capsys, fake_ollama):
    text = (
        '{"state": "s", "questions": {"q": {"type": "noul", "instructions": "a"},'
        ' "q": {"type": "noul", "instructions": "b"}}}'
    )
    code, _, err = run(capsys, "decide", "--input", str(write(tmp_path, data=text)))
    assert code == 2 and last_json(err)["error"]["code"] == "INVALID_INPUT"
    assert fake_ollama.calls("/v1/systemone") == []


def test_missing_input_file_exit_2(tmp_path, capsys):
    code, _, err = run(capsys, "decide", "--input", str(tmp_path / "nope.json"))
    assert code == 2 and last_json(err)["error"]["code"] == "INVALID_INPUT"


def test_invalid_input_exit_2(tmp_path, capsys):
    code, _, err = run(capsys, "decide", "--input", str(write(tmp_path, data={"state": 1})))
    assert code == 2 and last_json(err)["error"]["code"] == "INVALID_INPUT"


def test_backend_unavailable_exit_1(tmp_path, capsys, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        monkeypatch.setenv("DECISION_BRIDGE_OLLAMA_URL", f"http://127.0.0.1:{s.getsockname()[1]}")
    code, out, err = run(capsys, "decide", "--input", str(write(tmp_path)))
    assert code == 1 and out == ""
    payload = last_json(err)["error"]
    assert payload["code"] == "OLLAMA_UNAVAILABLE" and payload["retryable"] is True


def test_missing_model_exit_1(tmp_path, capsys):
    code, _, err = run(capsys, "decide", "--model", "other:1", "--input", str(write(tmp_path)))
    assert code == 1 and last_json(err)["error"]["code"] == "MODEL_NOT_FOUND"


def test_invalid_config_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DECISION_BRIDGE_MAX_CONCURRENCY", "banana")
    code, out, err = run(capsys, "decide", "--input", str(write(tmp_path)))
    assert code == 2 and out == ""
    assert last_json(err)["error"]["code"] == "INVALID_CONFIGURATION"


def test_cli_option_overrides_environment(tmp_path, capsys, monkeypatch, fake_ollama):
    monkeypatch.setenv("DECISION_BRIDGE_MODEL", "env-model:1")
    code, _, _ = run(capsys, "decide", "--model", "nimble:latest", "--input", str(write(tmp_path)))
    assert code == 0
    assert fake_ollama.calls("/v1/systemone")[-1].json()["model"] == "nimble:latest"


def test_backend_error_body_not_shown(tmp_path, capsys, fake_ollama):
    fake_ollama.set("POST", "/v1/systemone", FakeResponse(500, "SECRET_BACKEND_TEXT"))
    code, out, err = run(capsys, "decide", "--input", str(write(tmp_path)))
    assert code == 1 and "SECRET_BACKEND_TEXT" not in out + err


def test_doctor_text_without_inference(capsys, fake_ollama):
    code, out, err = run(capsys, "doctor")
    assert code == 0 and err == ""
    assert "inference readiness is unverified" in out.lower()
    assert fake_ollama.calls("/v1/systemone") == []


def test_doctor_json(capsys):
    code, out, _ = run(capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True and data["inference_verified"] is False
    assert "\x1b[" not in out


def test_doctor_json_smoke_test(capsys, fake_ollama):
    code, out, _ = run(capsys, "doctor", "--json", "--smoke-test")
    assert code == 0 and json.loads(out)["inference_verified"] is True
    assert len(fake_ollama.calls("/v1/systemone")) == 1


def test_doctor_failing_readiness_exit_1(capsys):
    code, out, _ = run(capsys, "doctor", "--model", "other:1")
    assert code == 1 and "ollama pull other:1" in out


def test_doctor_unreachable_exit_1(capsys, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        monkeypatch.setenv("DECISION_BRIDGE_OLLAMA_URL", f"http://127.0.0.1:{s.getsockname()[1]}")
    code, out, _ = run(capsys, "doctor", "--json")
    assert code == 1 and json.loads(out)["ok"] is False


def test_doctor_bad_config_exit_2(capsys, monkeypatch):
    monkeypatch.setenv("DECISION_BRIDGE_OLLAMA_URL", "http://example.com:11434")
    code, _, err = run(capsys, "doctor")
    assert code == 2 and last_json(err)["error"]["code"] == "INVALID_CONFIGURATION"


def test_help_and_version_work_offline_and_fast(monkeypatch):
    env = subprocess_env(DECISION_BRIDGE_OLLAMA_URL="http://127.0.0.1:9", PATH="")
    for args in (["--help"], ["--version"], ["doctor", "--help"], ["decide", "--help"]):
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "decision_bridge", *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert time.monotonic() - started < 5
