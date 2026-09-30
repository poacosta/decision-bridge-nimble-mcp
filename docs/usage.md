# Using Decision Bridge

A practical guide: install it, connect it to your agent, and use it from an agent or from a terminal. Reference material lives elsewhere: [tool contract](tool-contract.md), [configuration](configuration.md), [client setup details](clients.md), [troubleshooting](troubleshooting.md).

## The mental model

```text
your agent (Claude Code, Codex, ...)  --calls tool "decide"-->  decision-bridge  -->  Ollama  -->  Nimble
        you decide when to call it          validates in/out        local model
```

- **You (or the agent) supply the evidence and the allowed answers.** The bridge does not guess what to classify.
- **The model returns probabilities, not verdicts.** Your agent or script decides what to do with them.
- **Nothing is executed for you.** The result is advice. It is never permission to skip approvals, tests, or checks.
- **The agent chooses when to call it.** Installing the MCP does not intercept prompts or change which model your agent runs.

## Setup at a glance

1. Have Ollama running and the model installed (`ollama pull nimble:latest`, done by you).
2. Install the bridge from a source download (details in [installation](installation.md)):

   ```sh
   uv tool install .
   ```

3. Check it works, including one real decision:

   ```sh
   decision-bridge doctor --smoke-test
   ```

   Expect `Result: ready` and `Inference readiness: verified by a real decision request.`
4. Find the absolute path to the executable (`which decision-bridge`, or `where decision-bridge` on Windows). Desktop apps often do not inherit your terminal `PATH`, so use the absolute path when registering.
5. Register it in your client. Replace the path with yours:

   | Client | Command or file |
   |---|---|
   | Claude Code | `claude mcp add -s user decision-bridge -- /absolute/path/to/decision-bridge serve` |
   | Codex | `codex mcp add decision-bridge -- /absolute/path/to/decision-bridge serve` |
   | Claude Desktop | edit `claude_desktop_config.json`, then fully restart the app |
   | Cursor | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
   | Antigravity | `~/.gemini/config/mcp_config.json` |
   | Anything else | the generic `mcpServers` block |

   Exact steps, JSON, TOML, verification and removal for each: [clients.md](clients.md).
6. Start a **new** agent session (already-running sessions do not pick up new servers) and confirm the two tools, `decide` and `bridge_status`, are listed.

Registering only adds a launch command to your client's configuration; the client starts `decision-bridge serve` itself when needed. Nothing runs in the background.

## Using it from an agent

### A first request

Ask in plain language, and give the evidence and the categories:

> Use Decision Bridge to classify the supplied request into one of the provided categories. Return the selected category and probabilities. Do not execute the selected action.
>
> Request: "Rename the internal helper `fmt_date` to `format_date` across the codebase and update the imports."
> Categories: routine (mechanical, no behavior change), investigate (needs investigation first), design (needs design decisions first), unknown (not enough evidence).

The agent turns that into a `decide` call. This is the shape of the arguments ([`examples/task-routing.json`](../examples/task-routing.json)):

```json
{
  "state": "Rename the internal helper `fmt_date` to `format_date` across the codebase and update the imports.",
  "questions": {
    "route": {
      "type": "choice",
      "instructions": "Select the handling category that best fits the request.",
      "criteria": {
        "routine": "A known mechanical task with no behavior change.",
        "investigate": "A problem that needs investigation before any change.",
        "design": "A change that needs design decisions first.",
        "unknown": "The evidence is insufficient to choose."
      }
    }
  }
}
```

Your agent builds these arguments; you do not normally write them by hand. You can run the same file yourself from the terminal (below).

### What comes back

A structured result, with the model's numbers passed through untouched. Real output for the request above (local run, 2026-09-30, `nimble:latest`):

- `route`: `choice` = `routine`, with probabilities `routine` 0.967, `investigate` 0.009, `design` 0.020, `unknown` 0.004, and `confidence` 0.874
- `usage`: 221 input tokens and 1 output token; `metadata.duration_ms`: 1229 (warm model)

The agent should report the category and probabilities, note that it is advisory, and continue with its normal work.

### Standing instructions

For everyday use, add the block from [agent-usage.md](agent-usage.md) to your agent's instructions (Claude Code: `CLAUDE.md`; Codex: `AGENTS.md`). It tells the agent to use the bridge for bounded questions where evidence and alternatives already exist, to include an unknown option, to treat results as advisory, and to fall back to normal analysis when the bridge fails.

### Writing good questions

| You want | Use | Tips |
|---|---|---|
| Pick one of several handling paths | `choice` | Describe every option concretely. Always include an `unknown`/`no_match` option. |
| Ask a yes/no-style question | `noul` | You get a **probability**, not a boolean. Pick your own cut-off in your own code. |
| Rate something on an ordered scale | `score` | List levels **lowest first**. You get a number from 0 to levels-1, plus per-level probabilities. |

- Ask several independent questions about the same evidence in **one** call (up to 64).
- Send the relevant excerpt, not the repository. The model has roughly an 8K-token prompt budget and nothing is truncated for you.
- Use the real evidence: a paraphrase changes the answer.

### Reading results sensibly

- **`confidence` is concentration, not correctness.** A confident answer can still be wrong.
- **Set an acceptance threshold in the consumer and test it.** For example, an automation might act only when a `noul` is at least 0.9 or at most 0.1 and otherwise fall back to normal analysis. That is an illustration, not a recommendation: measure on your own data first ([evaluation](evaluation.md)).
- **A failure is not an answer.** If the tool returns an error, the agent should say so and continue without a decision, never invent one.

### When something goes wrong

A failed call returns an error with a stable `code`, a short `message`, and a `retryable` hint. The most common ones:

| Code | What to do |
|---|---|
| `OLLAMA_UNAVAILABLE` | Start Ollama, then retry |
| `MODEL_NOT_FOUND` | `ollama pull nimble:latest` (the bridge never downloads models) |
| `TIMEOUT` | The first call may be loading the model; retry, or raise the timeout / set a keep-alive |
| `CONTEXT_LIMIT` / `PAYLOAD_TOO_LARGE` | Send less evidence or fewer questions |
| `INVALID_INPUT` | Fix the question shape (the message says which field) |

Full table: [tool-contract.md](tool-contract.md#failure). Ask the agent to call `bridge_status` for a quick health check; it never runs inference.

## Using it from the command line

The CLI shares the decision logic with the MCP tool, so it is a good way to try a request before wiring it into an agent, or to use the model from scripts and pre-agent routing.

```sh
decision-bridge decide --input examples/task-routing.json
```

```sh
cat request.json | decision-bridge decide --input -
```

- Input is the same JSON as the tool arguments (`state` and `questions`). Duplicate keys, wrong types, and out-of-range sizes are rejected with a clear error instead of being repaired. Paths with spaces or non-ASCII characters are fine.
- The result is one JSON document on stdout. Errors are one JSON document on stderr; **the error JSON is the last stderr line**, and log lines may precede it.
- Exit codes: `0` success, `2` input or configuration error, `1` service or execution failure.

A small script pattern (uses `jq`; any JSON tool works):

```sh
if result=$(decision-bridge decide --input request.json 2>err.log); then
  echo "$result" | jq -r '.answers.route.choice'
else
  status=$?                # 2 = fix the request or config, 1 = service problem
  tail -n 1 err.log        # the error JSON
fi
```

Diagnostics:

```sh
decision-bridge doctor               # config, endpoint, version, installed model; no inference
decision-bridge doctor --json        # machine-readable
decision-bridge doctor --smoke-test  # also sends one tiny real request (may load the model)
```

`doctor` without `--smoke-test` says explicitly that inference readiness is unverified.

## Real examples

Outputs from a local run on 2026-09-30 (macOS, Ollama 0.35.0, `nimble:latest` digest `24e550a16a70`, warm model). They vary between runs and machines, and one run proves nothing about accuracy.

| Example file | Question | Observed answer | Time |
|---|---|---|---|
| [`task-routing.json`](../examples/task-routing.json) | `choice` | `routine` (0.967) | 1.2 s |
| [`evidence-check.json`](../examples/evidence-check.json) | two `noul` | 0.994 (error code mentioned), 1.000 (discount involved) | 2.5 s |
| [`relevance-score.json`](../examples/relevance-score.json) | `score`, 4 levels | 2.975 (level 3: 0.977) | 1.3 s |
| [`mixed-questions.json`](../examples/mixed-questions.json) | `choice` + `noul` + `score` | see the [README](../README.md#example) | 9.3 s in one run, about 1.5 s in a later warm run |

## Day-to-day operations

| Task | How |
|---|---|
| Check the connection in Claude Code | `claude mcp list` (health check) or `claude mcp get decision-bridge` |
| Check the connection in Codex | `codex mcp list`, `codex mcp get decision-bridge`, or `/mcp` inside Codex |
| Pick up code changes | `uv tool install --reinstall .` in the project directory, then start a new agent session |
| Keep the model loaded between calls | Set `DECISION_BRIDGE_KEEP_ALIVE` (for example `30m`); see below |
| Use another installed local model | Set `DECISION_BRIDGE_MODEL` |
| Unregister | `claude mcp remove decision-bridge -s user` and/or `codex mcp remove decision-bridge` |
| Uninstall | `uv tool uninstall decision-bridge-nimble-mcp` |

Passing configuration through a registration (shape taken from each CLI's `--help`; not run end to end):

```sh
claude mcp add -s user decision-bridge -e DECISION_BRIDGE_KEEP_ALIVE=30m -- /absolute/path/to/decision-bridge serve
```

```sh
codex mcp add decision-bridge --env DECISION_BRIDGE_KEEP_ALIVE=30m -- /absolute/path/to/decision-bridge serve
```

All variables and their rules: [configuration.md](configuration.md).

## Boundaries worth remembering

- The model can be wrong, and the bridge does not calibrate it. Advisory only.
- Each agent tool call is an extra turn. It can cost more than a cheap decision saves; measure before claiming savings.
- Inference is local, but your agent may already have sent your text to a hosted model, and may send the decisions there too.
- Only stdio MCP, only Ollama, only Nimble models that report decision support.
