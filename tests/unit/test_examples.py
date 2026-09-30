from pathlib import Path

import pytest

from decision_bridge.schemas import build_request_body, parse_decision_input

EXAMPLES = sorted((Path(__file__).parent.parent.parent / "examples").glob("*.json"))


def test_expected_examples_exist():
    names = {p.name for p in EXAMPLES}
    assert {
        "task-routing.json",
        "evidence-check.json",
        "relevance-score.json",
        "mixed-questions.json",
    } <= names


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_is_valid_and_fits_the_request_limit(path):
    inp = parse_decision_input(path.read_text(encoding="utf-8"))
    assert build_request_body(inp, "nimble:latest", None)
