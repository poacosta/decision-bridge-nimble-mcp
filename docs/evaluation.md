# Evaluation and live checks

Two separate things, both opt-in, both needing a running Ollama with the model already installed. Neither ever downloads a model.

## 1. Live contract tests

```sh
DECISION_BRIDGE_LIVE=1 uv run pytest -m live
```

These send real requests for each question type, a mixed request, `doctor --smoke-test`, and one MCP stdio round trip, and check **protocol and contract** behavior only (labels come from your submitted set, probabilities are in range, and so on). They never assert which label the model prefers, so a probabilistic answer cannot make them flaky. Each run writes `live-report.json` (git-ignored) recording bridge, Python, and Ollama versions, the model tag and digest, the OS, and whether the model was already loaded (warm) or not (cold).

Without `DECISION_BRIDGE_LIVE=1` they are skipped, and they are excluded from the default run and from CI. If no backend is available, live validation is "not run"; it is never simulated.

## 2. Evaluation script

```sh
uv run python scripts/evaluate.py --confirm-live
uv run python scripts/evaluate.py --confirm-live --json
```

It refuses to run without `--confirm-live`. It uses the synthetic, redistributable fixtures in `examples/evaluation.jsonl` (12 short invented requests with one label per question type; no external dataset, no account) and reports:

| Metric | Definition |
|---|---|
| samples / failures | requests attempted, and failures counted by stable error code |
| latency | wall time per request, sequential; first, median, p95, max |
| `choice` agreement | fraction where the selected label equals the fixture label |
| `noul` accuracy@0.5 and Brier | correct at a 0.5 cut-off, and mean squared error of the probability against 0/1 (lower is better) |
| `score` MAE and exact match | mean absolute error of the numeric score against the labelled level, and how often the rounded score equals it |

A line of the dataset may bring its own `questions`; otherwise the script's built-in question set is used.

### Observations from one run

Not a benchmark and not a calibration result. Twelve samples cannot support either; the labels are one person's judgment and the "detail" rubric is subjective.

Run on 2026-09-30: macOS (Darwin 27.0.0, arm64), Python 3.12.3, Ollama 0.35.0, `nimble:latest` digest `24e550a16a7081881be2f1f0d91e8cc13a597472735c04119f035a0a85c67e0c`, model already loaded (warm), local default endpoint.

| | Observed |
|---|---|
| Requests succeeded | 12 of 12 |
| Latency per request | about 1.9 s (median 1904 ms, p95 1979 ms), measured on a warm model |
| `choice` agreement | 0.75 (9 of 12) |
| `noul` accuracy@0.5 / Brier | 1.00 (12 of 12) / 0.011 |
| `score` MAE / exact after rounding | 0.36 / 0.75 |

In the same session, live contract tests ran single requests between roughly 1.1 s and 7.0 s for different examples (the slowest was the first request of that run), and a direct CLI call took about 9 s. Latency varies with load, request size, and whether the model is loaded; expect a cold start to be slower. Treat these as observations from one machine, not expectations for yours.

## Claims about speed or token savings

None are made. A latency comparison or token-saving claim needs a stated baseline, a measured workload, and, when an agent calls the tool, the cost of the extra tool turns ([agent guidance](agent-usage.md)).
