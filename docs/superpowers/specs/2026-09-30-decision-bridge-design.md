# Decision Bridge — design notes

The implementation brief (owner-supplied, dated 2026-09-30) is the specification. This file records only
what was verified at implementation start and any deliberate deviations. It does not restate the brief.

## Verified at implementation start (2026-09-30)

| Item | Observation |
|---|---|
| Ollama | 0.35.0 at `127.0.0.1:11434` |
| Model | `nimble:latest`, digest `24e550a16a7081881be2f1f0d91e8cc13a597472735c04119f035a0a85c67e0c`, Q8_0, 9.5 GB |
| Model capabilities | `/api/tags` and `/api/show` report `["decision","tools","thinking","completion"]` |
| Cloud models | `/api/tags` marks cloud models with a `remote_model` field (used for `MODEL_NOT_LOCAL`) |
| Nimble contract | `POST /v1/systemone`; `model`, `state`, `questions`, optional `keep_alive`; types `choice`/`noul`/`score`; 1–64 questions; 2–26 options; 64 KiB body; 8,192-token window |
| MCP SDK | `mcp` 2.2.0 (v2 line), `requires-python >=3.10`, high-level server class `MCPServer` |
| Toolchain | uv 0.10.4, Python 3.12.3 |

## Decisions

- **Model capability check:** `doctor`/`bridge_status` read `capabilities` from `/api/tags`. A model that lacks
  `decision` is reported as `DECISION_UNSUPPORTED`; a model with it is still not proof of inference readiness
  (only `--smoke-test` establishes that).
- **Response `model` field:** preserved as returned by upstream, not rewritten to the configured tag.
- **Python floor:** 3.11 (per brief), tested on 3.11 and 3.12+.
- **Type checker:** mypy (strict on `src/`).
- **Licence:** pending owner decision; no `LICENSE` file is added. MIT recommended.
- **Git:** local `git init` only, no remote, no commits unless the owner asks.
- **Live tests:** run once, opt-in, against the owner's running Ollama and the already-installed model.
