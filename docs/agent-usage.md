# Guidance for calling agents

Copy this block into your agent's instructions (for example a system prompt or `AGENTS.md`):

```text
Use Decision Bridge for bounded classification questions when the evidence
and candidate answers are already available. Supply the actual evidence,
define clear alternatives, and include an unknown/no-match option where
appropriate. Batch independent questions about the same state when useful.
Treat the result as advisory. Keep tool output compact. If the bridge fails,
continue normal analysis or report the uncertainty; never invent a decision.
Do not use the model's output to bypass required approvals, tests, or checks.
Do not call it for arithmetic or deterministic checks that ordinary code can
resolve. Do not send an entire repository when a small excerpt is enough.
```

## What adding the tool does not do

Adding an MCP tool does not intercept your prompts, replace the main model, or reduce an account's usage. The calling agent still chooses when to invoke it. Each tool call is an extra turn: for a cheap decision, the extra turn and its output tokens can cost more than they save. Any saving must be measured against a stated baseline on your own workload, including the cost of those extra turns.

For routing *before* an agent starts, a separate application can call `decision-bridge decide` from a script. Building that orchestrator is outside v0.1.

## Writing good questions

- Give every alternative a concrete description; include an `unknown` choice so the model is not forced to guess.
- Put independent questions about the same evidence in one request (up to 64) instead of many calls.
- `noul` returns a probability. Choose your own cut-off in your code; do not treat 0.5 as a verdict.
- The model has roughly an 8K-token prompt budget. Send the relevant excerpt, not the repository.

## Privacy boundary

The model inference can stay on the configured local machine, but the calling agent may already have sent your input to a hosted model and may send the returned decisions there too. This one step being local does not make the entire agent workflow private.
