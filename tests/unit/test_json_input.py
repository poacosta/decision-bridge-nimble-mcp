import pytest

from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import parse_decision_input, parse_json_strict


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',
        '{"state": "x", "questions": {'
        '"q": {"type": "noul", "instructions": "i"}, '
        '"q": {"type": "noul", "instructions": "j"}}}',
        '{"x": [{"k": 1, "k": 1}]}',
        '{"state": {"a": 1, "a": 2}}',
        '{"a": NaN}',
        '{"a": Infinity}',
        "[1, 2",
        "",
    ],
)
def test_strict_parser_rejects(text):
    with pytest.raises(BridgeError) as exc:
        parse_json_strict(text)
    assert exc.value.code is ErrorCode.INVALID_INPUT


def test_duplicate_choice_label_rejected():
    text = (
        '{"state": "s", "questions": {"q": {"type": "choice", "instructions": "i",'
        ' "criteria": {"a": "one", "a": "two", "b": "three"}}}}'
    )
    with pytest.raises(BridgeError) as exc:
        parse_decision_input(text)
    assert "duplicate" in exc.value.message.lower()


def test_duplicate_state_key_rejected():
    text = '{"state": {"a": 1, "a": 2}, "questions": {"q": {"type": "noul", "instructions": "i"}}}'
    with pytest.raises(BridgeError):
        parse_decision_input(text)


def test_deeply_nested_json_text_is_invalid_input_not_a_crash():
    text = "[" * 100000 + "]" * 100000
    with pytest.raises(BridgeError) as exc:
        parse_json_strict(text)
    assert exc.value.code is ErrorCode.INVALID_INPUT


def test_valid_json_parses():
    assert parse_json_strict('{"a": [1, 2.5, "x", null]}') == {"a": [1, 2.5, "x", None]}
