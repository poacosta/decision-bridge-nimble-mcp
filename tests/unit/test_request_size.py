import json

import pytest

from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import MAX_BODY_BYTES, build_request_body, parse_decision_input

QUESTIONS = {
    "q": {"type": "choice", "instructions": "pick", "criteria": {"a": "first", "b": "second"}},
    "n": {"type": "noul", "instructions": "yes?"},
}


def body_for(state, model="nimble:latest", keep_alive=None):
    inp = parse_decision_input({"state": state, "questions": QUESTIONS})
    return build_request_body(inp, model, keep_alive)


def pad_to(target, filler, model="nimble:latest"):
    """Return a string state whose request body is exactly `target` bytes (filler is 1 byte)."""
    base = len(body_for("", model))
    unit = len(filler.encode())
    n, rest = divmod(target - base, unit)
    return filler * n + "a" * rest


def test_max_is_64_kib():
    assert MAX_BODY_BYTES == 65536


def test_body_shape_and_order():
    body = json.loads(body_for("hello"))
    assert list(body) == ["model", "state", "questions"]
    assert body["model"] == "nimble:latest"
    assert list(body["questions"]) == ["q", "n"]
    assert list(body["questions"]["q"]["criteria"]) == ["a", "b"]
    assert "criteria" not in body["questions"]["n"]


def test_keep_alive_only_when_configured():
    assert "keep_alive" not in json.loads(body_for("x"))
    assert json.loads(body_for("x", keep_alive="5m"))["keep_alive"] == "5m"


def test_null_choice_description_preserved():
    inp = parse_decision_input(
        {
            "state": "s",
            "questions": {
                "q": {"type": "choice", "instructions": "i", "criteria": {"a": None, "b": None}}
            },
        }
    )
    assert json.loads(build_request_body(inp, "m", None))["questions"]["q"]["criteria"] == {
        "a": None,
        "b": None,
    }


def test_unicode_not_escaped():
    body = body_for("日本語")
    assert "日本語".encode() in body


@pytest.mark.parametrize("filler", ["a", "é", "日", "😀"])
def test_exact_boundary_ok_and_one_over_rejected(filler):
    ok = pad_to(MAX_BODY_BYTES, filler)
    assert len(body_for(ok)) == MAX_BODY_BYTES
    with pytest.raises(BridgeError) as exc:
        body_for(ok + "a")
    assert exc.value.code is ErrorCode.PAYLOAD_TOO_LARGE


def test_counts_bytes_not_characters():
    state = "😀" * (MAX_BODY_BYTES // 4)  # far fewer chars than the byte limit, more bytes
    assert len(state) < MAX_BODY_BYTES
    with pytest.raises(BridgeError) as exc:
        body_for(state)
    assert exc.value.code is ErrorCode.PAYLOAD_TOO_LARGE


def test_model_name_and_keep_alive_count_toward_limit():
    state = pad_to(MAX_BODY_BYTES, "a")
    assert len(body_for(state)) == MAX_BODY_BYTES
    with pytest.raises(BridgeError) as exc:
        body_for(state, model="nimble:latest2")
    assert exc.value.code is ErrorCode.PAYLOAD_TOO_LARGE
    with pytest.raises(BridgeError):
        body_for(state, keep_alive="5m")


def test_lone_surrogate_is_invalid_input_not_crash():
    inp = parse_decision_input({"state": "bad \ud800 text", "questions": QUESTIONS})
    with pytest.raises(BridgeError) as exc:
        build_request_body(inp, "nimble:latest", None)
    assert exc.value.code is ErrorCode.INVALID_INPUT


def test_lone_surrogate_in_question_id_is_invalid_input():
    inp = parse_decision_input(
        {"state": "x", "questions": {"q\ud800": {"type": "noul", "instructions": "i"}}}
    )
    with pytest.raises(BridgeError) as exc:
        build_request_body(inp, "nimble:latest", None)
    assert exc.value.code is ErrorCode.INVALID_INPUT
