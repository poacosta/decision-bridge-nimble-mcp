import copy
import json
import math
from pathlib import Path

import pytest

from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.responses import PROB_SUM_TOLERANCE, validate_response
from decision_bridge.schemas import parse_decision_input

CAPTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "live_mixed_capture.json").read_text("utf-8")
)
INPUT = parse_decision_input(CAPTURE["request"])


def raw():
    return copy.deepcopy(CAPTURE["response"])


def check(mutate=None, inp=INPUT):
    data = raw()
    if mutate:
        mutate(data)
    return validate_response(inp, data, model_fallback="fallback:tag")


def rejected(mutate, inp=INPUT):
    with pytest.raises(BridgeError) as exc:
        check(mutate, inp)
    assert exc.value.code is ErrorCode.INVALID_UPSTREAM_RESPONSE
    return exc.value


def test_real_capture_validates_and_values_are_preserved_exactly():
    out = check()
    up = raw()
    assert out["model"] == "nimble:latest"
    assert out["answers"] == up["answers"]
    assert out["usage"] == up["usage"]
    assert list(out["answers"]) == ["route", "mentions_payment", "detail"]
    # untouched floats, not rounded or recomputed
    assert out["answers"]["route"]["probabilities"]["investigate"] == 0.98165653665675
    assert out["answers"]["detail"]["score"] == 1.0425619366824663


def test_model_falls_back_to_configured_when_upstream_omits_it():
    out = check(lambda d: d.pop("model"))
    assert out["model"] == "fallback:tag"


def test_usage_absent_means_key_absent():
    assert "usage" not in check(lambda d: d.pop("usage"))


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": "1", "output_tokens": 2},
        {"input_tokens": 1},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 1.5, "output_tokens": 1},
        [1, 2],
    ],
)
def test_bad_usage_rejected(usage):
    rejected(lambda d: d.update(usage=usage))


def test_additive_upstream_fields_are_ignored():
    def mutate(d):
        d["extra_top"] = 1
        d["answers"]["route"]["rationale"] = "x"
        d["usage"]["cached"] = 3

    out = check(mutate)
    assert "extra_top" not in out and "rationale" not in out["answers"]["route"]
    assert out["usage"] == {"input_tokens": 784, "output_tokens": 4}


def test_confidence_is_optional_but_validated_when_present():
    out = check(lambda d: d["answers"]["route"].pop("confidence"))
    assert "confidence" not in out["answers"]["route"]
    for bad in (1.2, -0.1, "0.5", None, True, math.nan):
        rejected(lambda d, b=bad: d["answers"]["route"].update(confidence=b))


def test_missing_answer():
    rejected(lambda d: d["answers"].pop("route"))


def test_extra_answer():
    rejected(lambda d: d["answers"].update(surprise={"type": "noul", "noul": 0.5}))


@pytest.mark.parametrize("field", ["answers"])
def test_answers_wrong_container(field):
    rejected(lambda d: d.update({field: []}))
    rejected(lambda d: d.pop(field))


def test_wrong_type_for_question():
    rejected(lambda d: d["answers"]["route"].update(type="noul"))


def test_choice_label_not_submitted():
    rejected(lambda d: d["answers"]["route"].update(choice="banana"))


def test_choice_probability_map_labels_must_match():
    rejected(lambda d: d["answers"]["route"]["probabilities"].pop("unknown"))
    rejected(lambda d: d["answers"]["route"]["probabilities"].update(extra=0.0))


@pytest.mark.parametrize("bad", [math.nan, math.inf, -0.01, 1.01, "0.5", True, None])
def test_choice_probability_values_must_be_valid(bad):
    rejected(lambda d: d["answers"]["route"]["probabilities"].update(routine=bad))


def test_probability_sum_tolerance_boundary():
    assert PROB_SUM_TOLERANCE == 1e-3

    def shift(delta):
        def m(d):
            d["answers"]["route"]["probabilities"]["routine"] += delta

        return m

    check(shift(PROB_SUM_TOLERANCE / 2))
    check(shift(-PROB_SUM_TOLERANCE / 2))
    rejected(shift(PROB_SUM_TOLERANCE * 5))


@pytest.mark.parametrize("bad", [-0.1, 1.5, math.nan, "0.9", True, None])
def test_noul_must_be_probability(bad):
    rejected(lambda d: d["answers"]["mentions_payment"].update(noul=bad))


def test_noul_boundaries_ok():
    check(lambda d: d["answers"]["mentions_payment"].update(noul=0))
    check(lambda d: d["answers"]["mentions_payment"].update(noul=1.0))


def test_noul_field_missing():
    rejected(lambda d: d["answers"]["mentions_payment"].pop("noul"))


@pytest.mark.parametrize("bad", [-0.5, 2.01, math.nan, math.inf, "1", True, None])
def test_score_range(bad):
    rejected(lambda d: d["answers"]["detail"].update(score=bad))


def test_score_boundaries_ok():
    check(lambda d: d["answers"]["detail"].update(score=0))
    check(lambda d: d["answers"]["detail"].update(score=2))


def test_score_legend_and_probability_keys_must_match_rubric():
    rejected(lambda d: d["answers"]["detail"]["legend"].pop("2"))
    rejected(lambda d: d["answers"]["detail"]["legend"].update({"3": "extra"}))
    rejected(lambda d: d["answers"]["detail"].pop("legend"))
    rejected(lambda d: d["answers"]["detail"]["probabilities"].pop("0"))
    rejected(lambda d: d["answers"]["detail"]["probabilities"].update({"9": 0.0}))
    rejected(lambda d: d["answers"]["detail"]["legend"].update({"0": 5}))


def test_score_probability_sum_checked():
    rejected(lambda d: d["answers"]["detail"]["probabilities"].update({"0": 0.9}))


def test_answer_not_an_object():
    rejected(lambda d: d["answers"].update(route="investigate"))


def test_error_messages_do_not_echo_upstream_values():
    err = rejected(lambda d: d["answers"]["route"].update(choice="SECRET_LABEL_VALUE"))
    assert "SECRET_LABEL_VALUE" not in err.message
