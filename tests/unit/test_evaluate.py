import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_refuses_without_confirm_live():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evaluate.py")],
        capture_output=True,
        text=True,
        timeout=30,
        env={"DECISION_BRIDGE_OLLAMA_URL": "http://127.0.0.1:9"},
    )
    assert proc.returncode == 2 and "--confirm-live" in proc.stderr


def test_dataset_is_synthetic_and_consistent():
    rows = evaluate.load_dataset(ROOT / "examples" / "evaluation.jsonl")
    assert len(rows) >= 10
    assert len({r["id"] for r in rows}) == len(rows)
    for row in rows:
        assert set(row["labels"]) == set(evaluate.QUESTIONS)
        assert row["labels"]["route"] in evaluate.QUESTIONS["route"]["criteria"]
        assert isinstance(row["labels"]["mentions_payment"], bool)
        assert row["labels"]["evidence_detail"] in (0, 1, 2)


def test_dataset_questions_are_valid_decision_input():
    from decision_bridge.schemas import parse_decision_input

    assert parse_decision_input({"state": "x", "questions": evaluate.QUESTIONS})


def test_observe_metrics():
    answers = {
        "route": {"type": "choice", "choice": "routine"},
        "mentions_payment": {"type": "noul", "noul": 0.9},
        "evidence_detail": {"type": "score", "score": 1.4},
    }
    obs = evaluate.observe(
        {"route": "routine", "mentions_payment": True, "evidence_detail": 2},
        evaluate.QUESTIONS,
        answers,
    )
    assert obs[0] == {"type": "choice", "agree": True}
    assert obs[1]["correct"] is True and obs[1]["brier"] == pytest.approx(0.01)
    assert obs[2]["abs_err"] == pytest.approx(0.6) and obs[2]["exact"] is False
    wrong = evaluate.observe(
        {"route": "investigate", "mentions_payment": False, "evidence_detail": 1},
        evaluate.QUESTIONS,
        answers,
    )
    assert wrong[0]["agree"] is False and wrong[1]["correct"] is False
    assert wrong[1]["brier"] == pytest.approx(0.81) and wrong[2]["exact"] is True


def test_percentile_nearest_rank():
    assert evaluate.percentile([], 50) is None
    assert evaluate.percentile([5], 95) == 5
    values = list(range(1, 21))
    assert evaluate.percentile(values, 50) == 10
    assert evaluate.percentile(values, 95) == 19
    assert evaluate.percentile([1, 2, 3], 50) == 2  # rank rounds up, not down
    assert evaluate.percentile([10, 20, 30, 40], 95) == 40


def sample(ok, wall, code=None, obs=()):
    return {"ok": ok, "error_code": code, "wall_ms": wall, "observations": list(obs)}


def test_summarize_counts_failures_and_metrics():
    obs_a = evaluate.observe(
        {"route": "routine", "mentions_payment": False, "evidence_detail": 0},
        evaluate.QUESTIONS,
        {
            "route": {"choice": "routine"},
            "mentions_payment": {"noul": 0.2},
            "evidence_detail": {"score": 0.0},
        },
    )
    report = evaluate.summarize(
        [sample(True, 100, obs=obs_a), sample(False, 5000, "TIMEOUT"), sample(True, 300, obs=obs_a)]
    )
    assert report["samples"] == 3 and report["succeeded"] == 2 and report["failed"] == 1
    assert report["failures_by_code"] == {"TIMEOUT": 1}
    assert report["choice"] == {"n": 2, "agreement": 1.0}
    assert report["noul"]["accuracy_at_0.5"] == 1.0
    assert report["noul"]["brier"] == pytest.approx(0.04)
    assert report["score"]["mean_abs_error"] == 0.0
    assert report["latency_ms"]["first"] == 100 and report["latency_ms"]["max"] == 5000
    json.dumps(report)


def test_summarize_handles_no_successes():
    report = evaluate.summarize([sample(False, 10, "OLLAMA_UNAVAILABLE")])
    assert report["choice"]["agreement"] is None and report["succeeded"] == 0
