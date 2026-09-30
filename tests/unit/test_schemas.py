import copy

import jsonschema
import pytest

from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.schemas import DecisionInput, decision_input_json_schema, parse_decision_input


def choice(n=2, **extra):
    return {
        "type": "choice",
        "instructions": "pick",
        "criteria": {f"c{i}": f"desc {i}" for i in range(n)},
        **extra,
    }


def noul(**extra):
    return {"type": "noul", "instructions": "is it?", **extra}


def score(n=3, **extra):
    return {
        "type": "score",
        "instructions": "rate",
        "criteria": [f"level {i}" for i in range(n)],
        **extra,
    }


def parse(state="evidence", questions=None):
    questions = {"q": choice()} if questions is None else questions
    return parse_decision_input({"state": state, "questions": questions})


def invalid(**kw):
    with pytest.raises(BridgeError) as exc:
        parse(**kw)
    assert exc.value.code is ErrorCode.INVALID_INPUT
    return exc.value


def test_mixed_example_parses_and_keeps_order(mixed_example):
    inp = parse_decision_input(mixed_example)
    assert list(inp.questions) == ["route", "mentions_payment", "evidence_detail"]
    assert list(inp.questions["route"].criteria) == ["routine", "investigate", "unknown"]


@pytest.mark.parametrize(
    "state", ["text", {"a": 1}, ["x", 2, None, True, 1.5], {"a": {"b": [1, {"c": "d"}]}}]
)
def test_valid_states(state):
    assert parse(state=state).state == state


@pytest.mark.parametrize("state", [1, 1.5, True, None])
def test_top_level_scalar_states_rejected(state):
    invalid(state=state)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_rejected(bad):
    invalid(state={"x": [bad]})


def test_excessive_nesting_rejected():
    deep: object = "x"
    for _ in range(40):
        deep = {"k": deep}
    invalid(state=deep)


def test_nesting_at_limit_ok():
    deep: object = "x"
    for _ in range(15):
        deep = [deep]
    parse(state=deep)


def test_unicode_and_noul_criteria():
    inp = parse(
        state="日本語 😀",
        questions={
            "q": noul(criteria={"true": "はい", "false": "いいえ"}),
            "r": noul(criteria={"true": "only"}),
        },
    )
    assert inp.state == "日本語 😀"


def test_choice_null_descriptions_allowed():
    assert parse(
        questions={"q": {"type": "choice", "instructions": "i", "criteria": {"a": None, "b": None}}}
    )


@pytest.mark.parametrize("n,ok", [(1, False), (2, True), (26, True), (27, False)])
def test_choice_option_bounds(n, ok):
    if ok:
        parse(questions={"q": choice(n)})
    else:
        invalid(questions={"q": choice(n)})


@pytest.mark.parametrize("n,ok", [(1, False), (2, True), (26, True), (27, False)])
def test_score_level_bounds(n, ok):
    if ok:
        parse(questions={"q": score(n)})
    else:
        invalid(questions={"q": score(n)})


@pytest.mark.parametrize("n,ok", [(0, False), (1, True), (64, True), (65, False)])
def test_question_count_bounds(n, ok):
    qs = {f"q{i}": noul() for i in range(n)}
    if ok:
        parse(questions=qs)
    else:
        invalid(questions=qs)


@pytest.mark.parametrize(
    "questions",
    [
        {"q": {"type": "rank", "instructions": "x", "criteria": ["a", "b"]}},
        {"": noul()},
        {"q": noul(instructions="")},
        {"q": noul(instructions="   ")},
        {"q": choice(extra_field=1)},
        {"q": noul(criteria={"true": "a", "maybe": "b"})},
        {"q": {"type": "noul"}},
        {"q": {"type": "choice", "instructions": "i"}},
        {"q": {"type": "score", "instructions": "i", "criteria": {"a": "b"}}},
        {"q": {"type": "choice", "instructions": "i", "criteria": ["a", "b"]}},
        {"q": {"instructions": "i"}},
    ],
)
def test_invalid_questions_rejected(questions):
    invalid(questions=questions)


def test_top_level_extra_fields_rejected():
    with pytest.raises(BridgeError):
        parse_decision_input({"state": "x", "questions": {"q": noul()}, "model": "other"})


def test_error_message_does_not_echo_evidence():
    secret = "SECRET-EVIDENCE-123"
    err = invalid(state=secret, questions={"q": {"type": "bogus", "instructions": secret}})
    assert secret not in err.message


def test_json_string_input_is_accepted():
    inp = parse_decision_input(
        '{"state": "x", "questions": {"q": {"type": "noul", "instructions": "i"}}}'
    )
    assert isinstance(inp, DecisionInput)


def test_generated_schema_validates_example_and_rejects_bad(mixed_example):
    schema = decision_input_json_schema()
    jsonschema.validate(mixed_example, schema)
    bad = copy.deepcopy(mixed_example)
    bad["questions"]["route"]["type"] = "rank"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = copy.deepcopy(mixed_example)
    bad["questions"]["route"]["surprise"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = copy.deepcopy(mixed_example)
    bad["state"] = 5
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
