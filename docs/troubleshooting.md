# Troubleshooting

Start with `decision-bridge doctor`. Add `--smoke-test` to confirm a real decision works.

## Setup problems

| Symptom | Likely cause | Next step |
|---|---|---|
| `OLLAMA_UNAVAILABLE`; doctor says it cannot reach Ollama | Ollama is not running, or the URL is wrong | Start the Ollama app or `ollama serve` (only if nothing is listening), and check `DECISION_BRIDGE_OLLAMA_URL` |
| `OLLAMA_INCOMPATIBLE` (HTTP 404, "page not found") | Ollama older than 0.35 has no `/v1/systemone` | Upgrade Ollama: <https://ollama.com/download> |
| `MODEL_NOT_FOUND` | The configured model is not installed | `ollama pull nimble:latest` (the bridge never downloads models) |
| `MODEL_NOT_LOCAL` | Cloud selector or a remotely served model | Set `DECISION_BRIDGE_MODEL` to an installed local model |
| `DECISION_UNSUPPORTED` | Model does not report decision support | Use a Nimble model tag |
| `INVALID_CONFIGURATION` | Bad environment variable or option | The message names the variable; see [configuration](configuration.md) |
| Refused "non-loopback endpoint" | Remote Ollama not enabled | Read the remote-mode notes, then set `DECISION_BRIDGE_ALLOW_REMOTE=true` |

## During use

| Symptom | Next step |
|---|---|
| `TIMEOUT`, slow first request | The first call may load the model into memory. Try `doctor --smoke-test` once to warm it, raise `DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS`, or set `DECISION_BRIDGE_KEEP_ALIVE` (for example `30m`) so the model stays loaded. Ollama may keep computing after a timeout |
| `CONTEXT_LIMIT` | Shorten the evidence or reduce the questions. The 64 KiB body limit does not guarantee the prompt fits the model's ~8K-token budget |
| `PAYLOAD_TOO_LARGE` | Serialized request over 64 KiB (UTF-8 bytes). Send a smaller excerpt |
| `BUSY` | More calls than `max_concurrency` plus the small queue. Retry shortly or serialize calls |
| `INVALID_UPSTREAM_RESPONSE` | Ollama returned something outside the contract. Note your Ollama version and model digest and report it |
| Tool call rejected with a plain-text message | Arguments violated the advertised input schema; see the two error layers in [tool-contract](tool-contract.md) |

## Client connection

- **Tools do not appear:** run `decision-bridge serve` in a terminal. It should print nothing and wait for input (press Ctrl+D or Ctrl+C to exit). Then check the client's MCP log for the launch error.
- **"command not found" / spawn ENOENT:** use an absolute path. Desktop apps often do not inherit your terminal `PATH` ([finding the executable](installation.md#finding-the-executable)).
- **Windows JSON errors:** escape backslashes in paths (`C:\\Users\\...`).
- **Agent or bridge in a container or another machine:** `localhost` there is not the Ollama host. Set the URL and `DECISION_BRIDGE_ALLOW_REMOTE=true`, and make Ollama listen on that interface ([Ollama FAQ](https://docs.ollama.com/faq)).
- **Stdout pollution:** the bridge never writes to stdout while serving. If a client reports invalid JSON-RPC, check that nothing else (a wrapper script, a shell profile that prints) writes to the launch command's stdout.

## Reading CLI errors

`decision-bridge decide` prints results to stdout. On failure it prints a JSON error to stderr, and the error JSON is the **last** stderr line (log records may precede it). Exit codes: `2` input/configuration, `1` service/execution.
