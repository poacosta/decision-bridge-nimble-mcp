"""Small, opt-in evaluation of a live Nimble model through Decision Bridge.

This measures agreement with a tiny synthetic fixture set and request latency. It is a sanity
check on your own machine, not a benchmark and not a calibration study.

    uv run python scripts/evaluate.py --confirm-live [--dataset examples/evaluation.jsonl] [--json]

It sends real requests to the Ollama server and model configured through DECISION_BRIDGE_*
environment variables. It never downloads anything, and it refuses to run without --confirm-live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

# Question set applied to every sample that does not bring its own `questions`.
QUESTIONS: dict[str, Any] = {
    "route": {
        "type": "choice",
        "instructions": "Select the most appropriate handling category.",
        "criteria": {
            "routine": "A known mechanical task with no behavior change.",
            "investigate": "A problem requiring investigation.",
            "unknown": "The evidence is insufficient to choose.",
        },
    },
    "mentions_payment": {
        "type": "noul",
        "instructions": "Does the request explicitly mention a payment problem?",
    },
    "evidence_detail": {
        "type": "score",
        "instructions": "Rate the amount of diagnostic detail provided.",
        "criteria": [
            "A general report without reproduction details.",
            "Some concrete diagnostic details.",
            "Clear reproduction steps and supporting evidence.",
        ],
    },
}


def observe(
    labels: dict[str, Any], questions: dict[str, Any], answers: dict[str, Any]
) -> list[dict]:
    """Compare a sample's answers with its labels: one observation per labelled question."""
    out: list[dict] = []
    for qid, expected in labels.items():
        answer = answers[qid]
        kind = questions[qid]["type"]
        if kind == "choice":
            out.append({"type": kind, "agree": answer["choice"] == expected})
        elif kind == "noul":
            p, y = float(answer["noul"]), 1.0 if expected else 0.0
            out.append(
                {"type": kind, "correct": (p >= 0.5) == bool(expected), "brier": (p - y) ** 2}
            )
        else:
            s = float(answer["score"])
            out.append({"type": kind, "abs_err": abs(s - expected), "exact": round(s) == expected})
    return out


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(samples: list[dict]) -> dict[str, Any]:
    """Aggregate per-sample results: each is {ok, error_code, wall_ms, observations}."""
    ok = [s for s in samples if s["ok"]]
    failures: dict[str, int] = {}
    for s in samples:
        if not s["ok"]:
            failures[s["error_code"]] = failures.get(s["error_code"], 0) + 1
    obs = [o for s in ok for o in s["observations"]]
    choice = [o for o in obs if o["type"] == "choice"]
    noul = [o for o in obs if o["type"] == "noul"]
    score = [o for o in obs if o["type"] == "score"]
    latencies = [s["wall_ms"] for s in samples if s.get("wall_ms") is not None]
    return {
        "samples": len(samples),
        "succeeded": len(ok),
        "failed": len(samples) - len(ok),
        "failures_by_code": failures,
        "latency_ms": {
            "first": latencies[0] if latencies else None,
            "median": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "choice": {
            "n": len(choice),
            "agreement": _mean([1.0 if o["agree"] else 0.0 for o in choice]),
        },
        "noul": {
            "n": len(noul),
            "accuracy_at_0.5": _mean([1.0 if o["correct"] else 0.0 for o in noul]),
            "brier": _mean([o["brier"] for o in noul]),
        },
        "score": {
            "n": len(score),
            "mean_abs_error": _mean([o["abs_err"] for o in score]),
            "exact_match_after_rounding": _mean([1.0 if o["exact"] else 0.0 for o in score]),
        },
        "notes": [
            "Tiny synthetic fixture set: results are observations, not calibrated accuracy.",
            "Latency is wall time per request, run sequentially; the first request may include "
            "model load.",
        ],
    }


def load_dataset(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise SystemExit(f"{path}:{number}: invalid JSON ({exc})") from None
    return rows


async def run_live(rows: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    from decision_bridge import __version__
    from decision_bridge.config import load_settings
    from decision_bridge.errors import BridgeError
    from decision_bridge.service import DecisionService

    settings = load_settings(os.environ)
    service = DecisionService(settings)
    try:
        info = await service.inspect()
        environment = {
            "bridge_version": __version__,
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "ollama_version": info.version,
            "model": settings.model,
            "model_digest": info.model.digest if info.model else None,
        }
        samples: list[dict] = []
        for row in rows:
            questions = row.get("questions", QUESTIONS)
            started = time.monotonic()
            try:
                result = await service.decide({"state": row["state"], "questions": questions})
                wall_ms = round((time.monotonic() - started) * 1000)
                samples.append(
                    {
                        "id": row["id"],
                        "ok": True,
                        "error_code": None,
                        "wall_ms": wall_ms,
                        "observations": observe(row["labels"], questions, result["answers"]),
                    }
                )
            except BridgeError as exc:
                samples.append(
                    {
                        "id": row["id"],
                        "ok": False,
                        "error_code": exc.code.value,
                        "wall_ms": round((time.monotonic() - started) * 1000),
                        "observations": [],
                    }
                )
        return samples, environment
    finally:
        await service.aclose()


def render(report: dict[str, Any], environment: dict[str, Any]) -> str:
    def fmt(value: Any, digits: int = 3) -> str:
        return (
            "n/a"
            if value is None
            else f"{value:.{digits}f}"
            if isinstance(value, float)
            else str(value)
        )

    lat = report["latency_ms"]
    lines = [
        "Decision Bridge evaluation (synthetic fixtures; observations only)",
        f"environment: {json.dumps(environment)}",
        f"samples: {report['samples']}  succeeded: {report['succeeded']}  "
        f"failed: {report['failed']}"
        + (f"  failures: {report['failures_by_code']}" if report["failed"] else ""),
        f"latency ms: first={fmt(lat['first'])} median={fmt(lat['median'])} "
        f"p95={fmt(lat['p95'])} max={fmt(lat['max'])}",
        f"choice  n={report['choice']['n']}  agreement={fmt(report['choice']['agreement'])}",
        f"noul    n={report['noul']['n']}  accuracy@0.5={fmt(report['noul']['accuracy_at_0.5'])}"
        f"  brier={fmt(report['noul']['brier'])}",
        f"score   n={report['score']['n']}  MAE={fmt(report['score']['mean_abs_error'])}"
        f"  exact(rounded)={fmt(report['score']['exact_match_after_rounding'])}",
        *report["notes"],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required: acknowledge that real inference requests will be sent",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "examples" / "evaluation.jsonl",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if not args.confirm_live:
        print(
            "Refusing to run: this sends real requests to Ollama. Re-run with --confirm-live.",
            file=sys.stderr,
        )
        return 2
    samples, environment = asyncio.run(run_live(load_dataset(args.dataset)))
    report = summarize(samples)
    print(
        json.dumps({"environment": environment, "report": report}, indent=2)
        if args.json
        else render(report, environment)
    )
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
