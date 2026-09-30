# Decision Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, single session — the brief forbids subagents). Steps use checkbox syntax.

**Goal:** Ship `decision-bridge-nimble-mcp` v0.1.0: an installable stdio MCP server and CLI that send typed decision requests to Nimble through Ollama's `/v1/systemone`.

**Architecture:** `schemas` (input/output models, request building, size check) and `errors`/`config` are pure and dependency-light. `ollama` is the only module that speaks HTTP. `service` composes them (bounded concurrency, deadline, envelope, response validation) and is shared by `cli` and `server` (MCP). `diagnostics` powers `doctor` and `bridge_status`.

**Tech Stack:** Python >=3.11, `uv`, hatchling (static version), `mcp` 2.x (`MCPServer`), pydantic 2, httpx, pytest (+pytest-asyncio), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-30-decision-bridge-design.md` plus the owner's brief (sections cited as §N below).

## Global Constraints

- Package `decision-bridge-nimble-mcp`; module `decision_bridge`; executable `decision-bridge`; version `0.1.0`, output envelope `schema_version` `"1"`.
- Runtime deps only: `mcp`, `pydantic`, `httpx`. No torch/transformers/CUDA/MLX. No Node, DB, or Docker.
- Endpoint is `POST /v1/systemone` only. Body has only `model`, `state`, `questions`, optional `keep_alive`. Never `/api/chat`, `/api/generate`, or OpenAI routes.
- Limits: 1–64 questions, 2–26 choice/score options, 64 KiB (65,536 bytes) serialized body, response cap 1 MiB. No silent truncation. No automatic retry of inference.
- Config defaults per §6 (`127.0.0.1:11434`, `nimble:latest`, connect 5 s, request 120 s, concurrency 1, remote off, log `WARNING`). Precedence CLI > env > default. No `.env` loading.
- Redirects off; `trust_env=False`. Tool callers cannot set URL/model/headers/timeouts.
- stdout is protocol-only while serving; logs go to stderr and never contain prompts, evidence, answers, or raw upstream bodies.
- Exit codes: 0 ok, 2 input/config error, 1 unavailable/execution failure.
- English everywhere. No assistant attribution or co-author trailers. No push/publish. No `LICENSE` file (owner decision pending). No model pulls, no Ollama lifecycle changes, no global MCP config edits.

## Review Focus

1. Evidence containing non-ASCII / emoji / lone surrogates right at the 65,536-byte boundary — byte count, not char count; lone surrogates must be `INVALID_INPUT`, not a crash. (Task 3)
2. Duplicate keys in CLI input JSON (question IDs, choice labels, state keys) must not be silently last-wins. (Task 3)
3. Upstream 404 for a missing endpoint vs. a missing model must not be conflated. (Task 4)
4. A cancelled or timed-out call must release its concurrency slot and pending count so the next call succeeds. (Task 6)
5. `doctor`/`bridge_status`/MCP `initialize` must perform zero `/v1/systemone` requests, and startup must succeed with Ollama offline. (Tasks 6–8)

---

### Task 1: Scaffold, packaging, offline CLI shell

**Files:** Create `pyproject.toml`, `.gitignore`, `AGENTS.md`, `src/decision_bridge/{__init__,__main__,cli}.py`, `tests/unit/test_cli_basic.py`, `tests/conftest.py`.

**Interfaces:** Produces `decision_bridge.__version__: str`; `cli.main(argv: list[str] | None = None) -> int`; console script `decision-bridge = decision_bridge.cli:main`.

- [ ] Write failing tests: `--version` prints `decision-bridge 0.1.0`, exit 0; no args prints help, exit 0; `python -m decision_bridge --help` works via subprocess with no network. Run → FAIL (no package).
- [ ] `pyproject.toml`: hatchling, `[tool.hatch.version] path = "src/decision_bridge/__init__.py"`, `requires-python = ">=3.11"`, deps `mcp>=2,<3`, `pydantic>=2.7,<3`, `httpx>=0.27,<1`; dev group `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `build`; ruff + mypy strict + pytest (`asyncio_mode=auto`, marker `live`, `-m "not live"` default) config; no author URLs.
- [ ] Implement argparse shell with subcommands `serve`, `doctor`, `decide` (handlers raise `NotImplementedError` for now, replaced in later tasks); `uv sync`, run tests → PASS; `uv build` succeeds.

### Task 2: Errors and configuration

**Files:** Create `src/decision_bridge/errors.py`, `config.py`; tests `tests/unit/test_errors.py`, `test_config.py`.

**Interfaces:**
- `class ErrorCode(StrEnum)` with the 13 codes of §8.
- `class BridgeError(Exception)`: `code: ErrorCode`, `message: str`, `retryable: bool`, `request_id: str | None`; `.to_payload() -> dict` returns `{"error": {"code","message","retryable","request_id"}}`; `.exit_code -> int` (2 for `INVALID_INPUT|PAYLOAD_TOO_LARGE|INVALID_CONFIGURATION`, else 1).
- `@dataclass(frozen=True) class Settings`: `ollama_url: str`, `model: str`, `connect_timeout: float`, `request_timeout: float`, `keep_alive: str | None`, `max_concurrency: int`, `allow_remote: bool`, `log_level: str`.
- `load_settings(env: Mapping[str,str], overrides: Mapping[str, object] | None = None) -> Settings` raising `BridgeError(INVALID_CONFIGURATION)`.

- [ ] Tests: defaults; env beats default; override beats env; bad number/bool/duration (`"5s"`, `"1h30m"`, `"0"` ok for keep_alive, `"abc"` bad), non-positive timeout, concurrency outside 1–32, URL with creds/query/fragment/path/non-http scheme; loopback (`localhost`, `127.0.0.1`, `[::1]`) allowed; `http://192.168.1.5:11434` and hostnames rejected unless `allow_remote`; cloud selectors (`*-cloud`, `*:cloud`) rejected with `MODEL_NOT_LOCAL`. Payload shape test; `retryable` true only for `TIMEOUT`, `BUSY`, `OLLAMA_UNAVAILABLE`.
- [ ] Implement; run → PASS.

### Task 3: Input schemas, request serialization, size check

**Files:** Create `schemas.py`, `tests/unit/test_schemas.py`, `test_request_size.py`, `test_json_input.py`.

**Interfaces:**
- Pydantic models `ChoiceQuestion(type="choice", instructions, criteria: dict[str, str|None])`, `NoulQuestion(type="noul", instructions, criteria: NoulCriteria | None)` (keys `true`/`false`), `ScoreQuestion(type="score", instructions, criteria: list[str])`; `Question = Annotated[Union[...], Field(discriminator="type")]`; all `extra="forbid"`, `instructions` non-blank.
- `class DecisionInput(BaseModel)`: `state: str | dict | list` (top-level number/bool/null rejected, non-finite numbers rejected, depth ≤ 16), `questions: dict[str, Question]` (1–64, non-empty IDs).
- `parse_json_strict(text: str) -> object` — rejects duplicate keys at any depth and `NaN`/`Infinity`; raises `BridgeError(INVALID_INPUT)`.
- `build_request_body(inp: DecisionInput, model: str, keep_alive: str | None) -> bytes` — compact UTF-8 JSON (`ensure_ascii=False`, `allow_nan=False`), preserves question/label order, omits `keep_alive` when `None`, raises `PAYLOAD_TOO_LARGE` when `len(body) > 65536`; lone surrogates → `INVALID_INPUT`.
- `decision_input_json_schema() -> dict` for tool listing.

- [ ] Tests (each variant, both criteria forms, `null` choice descriptions, extras rejected, 1/64/0/65 questions, 2/26/1/27 options, empty IDs, Unicode, exact boundary: build a state whose body is exactly 65,536 bytes → OK, 65,537 → `PAYLOAD_TOO_LARGE`, including a 4-byte-emoji case; duplicate question ID / label / state key via `parse_json_strict`; key order preserved; generated JSON Schema validates the mixed example and rejects an unknown `type` (use `jsonschema` as a dev dependency only)).
- [ ] Implement; run → PASS.

### Task 4: Ollama HTTP adapter

**Files:** Create `ollama.py`, `tests/unit/test_ollama.py`, `tests/fakes/fake_ollama.py` (in-process ASGI-free `http.server` thread + `httpx.MockTransport` helpers).

**Interfaces:**
- `class OllamaClient(settings, transport: httpx.AsyncBaseTransport | None = None)`; `async aclose()`.
- `async post_decision(body: bytes) -> dict` — `POST {url}/v1/systemone`, `content-type: application/json`, response capped at 1 MiB (stream and count), returns parsed JSON object.
- `async get_version() -> str | None`; `async list_models() -> list[InstalledModel]` where `InstalledModel(name, digest, is_remote: bool, capabilities: tuple[str,...])`; both use the shorter diagnostic timeout (≤ 5 s).
- Error mapping in `post_decision`: connect refused/DNS → `OLLAMA_UNAVAILABLE`; timeout → `TIMEOUT`; 404 with JSON `{"error": "...model ... not found"}` → `MODEL_NOT_FOUND`; 404 with non-JSON/plain body → `OLLAMA_INCOMPATIBLE`; other 404 → `UPSTREAM_ERROR`; 400/413 whose error text mentions context/token/length → `CONTEXT_LIMIT`; text mentioning decision/unsupported → `DECISION_UNSUPPORTED`; other ≥400 → `UPSTREAM_ERROR` with a sanitized fixed message (never the raw body); non-JSON 200 or oversized → `INVALID_UPSTREAM_RESPONSE`.
- Client built with `follow_redirects=False`, `trust_env=False`; a 3xx response → `UPSTREAM_ERROR`.

- [ ] Tests via `MockTransport`: URL/method/headers/body bytes exactly as sent; no proxy env influence (set `HTTPS_PROXY`, assert unused); redirect not followed; each mapping above; 1 MiB+1 response; leak test — raw upstream error text (with a secret marker) never appears in exception message or captured logs.
- [ ] **Learning-mode contribution point:** the `retryable` flag choice per code (`UPSTREAM_ERROR` for 5xx vs 4xx) is left as a 6-line function `classify_retryable(code, status)` in `errors.py` for the owner to write; ship a conservative default first.
- [ ] Implement; run → PASS.

### Task 5: Upstream response validation and envelope

**Files:** Create `responses.py`, `tests/unit/test_responses.py`.

**Interfaces:** `validate_response(inp: DecisionInput, raw: dict, *, model_fallback: str) -> dict` returning the envelope's `model`, `answers`, optional `usage`; raises `INVALID_UPSTREAM_RESPONSE`. `PROB_SUM_TOLERANCE = 1e-3` (documented).

- [ ] Tests: happy path per type with values preserved bit-for-bit (no rounding); missing/extra answer; wrong `type`; choice label not in submitted set; probability map with unknown/missing label; probabilities NaN/negative/>1/sum off by more than tolerance; `noul` outside [0,1] or bool; score outside index range, legend/probability keys not matching rubric; missing required fields; extra upstream fields ignored; `usage` absent → key absent (no zeros); `usage` non-integer → invalid.
- [ ] Implement using the shapes captured in the live run (Task 10 may adjust); run → PASS.

### Task 6: Decision service and diagnostics

**Files:** Create `service.py`, `diagnostics.py`, `tests/unit/test_service.py`, `test_diagnostics.py`.

**Interfaces:**
- `class DecisionService(settings, client: OllamaClient | None = None)`: `async decide(raw_input: object | str) -> dict` (full envelope with `schema_version`, `model`, `answers`, `usage?`, `metadata{duration_ms, request_id}`); `async status() -> dict`; `async aclose()`.
- Concurrency: `asyncio.Semaphore(max_concurrency)` + pending counter capped at `max_concurrency + 4`; overflow → `BUSY` immediately. One `asyncio.timeout(request_timeout)` around wait + call. `finally` blocks always release slot/pending. `time.monotonic()` for duration.
- Model check: `list_models()` result cached per process; refresh on `MODEL_NOT_FOUND`. Remote (`is_remote`) → `MODEL_NOT_LOCAL`; capabilities present but lacking `decision` → `DECISION_UNSUPPORTED`.
- `diagnostics.run_doctor(service, *, smoke_test: bool) -> DoctorReport` (`ok`, `checks[]` with states `available|unavailable|unknown|not_checked`, `inference_verified: bool`); smoke test sends a tiny synthetic `choice` request and validates structure only.

- [ ] Tests: success envelope; BUSY when queue full; timeout while queued and while in-flight; cancellation releases slot (next call succeeds); concurrency bound respected (max in-flight observed = 1); no `/v1/systemone` request during `status()`/`doctor`; smoke test does one; offline Ollama → diagnostics report `unavailable`, no exception; model list cached (one `/api/tags` for two calls) and refreshed after `MODEL_NOT_FOUND`; logs contain request ID/duration/code only.
- [ ] Implement; run → PASS.

### Task 7: CLI commands

**Files:** Modify `cli.py`; create `tests/integration/test_cli_fake_backend.py`.

- [ ] Tests (run `main()` in-process against the fake HTTP backend; a few via subprocess): `decide --input file` (path with spaces + Unicode), `--input -` from stdin with bounded read (> 64 KiB + slack → exit 2), duplicate-key file → exit 2, unreachable backend → exit 1 with JSON error on stderr and stable code, invalid config → exit 2; `doctor` human output states "inference readiness is unverified"; `doctor --json` has no ANSI and parses; `doctor --json --smoke-test`; failing readiness → exit 1; `--help`/`--version` with `DECISION_BRIDGE_OLLAMA_URL` pointing at a closed port still succeed instantly.
- [ ] Implement; run → PASS.

### Task 8: MCP server and protocol tests

**Files:** Create `server.py`; modify `cli.py` (`serve`); create `tests/protocol/test_stdio_server.py`.

**Interfaces:** `build_server(service: DecisionService) -> MCPServer` registering exactly `decide` (annotations `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint` unset, `openWorldHint=False`) and `bridge_status`. Tool failures return `isError=True` with the compact error payload as structured content + JSON text.

- [ ] First verify the installed SDK's real API (`MCPServer`, `ToolAnnotations`, output-schema and error-result mechanics, `mcp.client` stdio helpers) by reading the installed package; record deviations in the spec file.
- [ ] Tests (real subprocess through the SDK client, cwd = `tmp_path`, fake backend on a random port): initialize succeeds with backend **offline**; `tools/list` returns exactly two tools with complete input schema including all three question variants; `decide` mixed request success (structured + text); `decide` with backend error → `isError` and stable code; schema-invalid arguments → SDK-level error (documented distinction); `bridge_status` makes zero decision calls (fake records requests); cancellation of an in-flight call frees the slot; stdin close → clean exit code 0; raw stdout bytes parse as JSON-RPC lines only.
- [ ] Implement; run → PASS.

### Task 9: Packaging tests, examples, docs, CI

**Files:** Create `examples/*.json`, `docs/*.md` per §5, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `.github/workflows/ci.yml`, `tests/packaging/test_dist.py`.

- [ ] Packaging tests (marked `packaging`, run in CI and locally): `uv build`; inspect wheel/sdist file lists (no `.env`, caches, `tests/live` secrets, weights); wheel installs in a fresh venv and `decision-bridge --version` / `doctor --json` / an MCP call through the installed executable work against the fake backend; sdist unpacked into a dir with no `.git` installs.
- [ ] Examples validated by a test that loads each through `DecisionInput`. Docs written per §§6–12 (README order per §14; Path A first; Path B labeled "after an authorized release"; clients marked tested/untested by evidence only; `SECURITY.md` without invented contact; release checklist with license/identity/contact/publish items pending).
- [ ] CI: matrix ubuntu/macos/windows × Python 3.11 and 3.13, `permissions: contents: read`, ruff, mypy, pytest (non-live), packaging job, no publish step, actions pinned by tag with a note to pin by SHA before release.

### Task 10: Evaluation harness, live tests, final verification

**Files:** Create `scripts/evaluate.py`, `examples/evaluation.jsonl`, `tests/live/test_live.py`, `docs/evaluation.md`.

- [ ] `evaluate.py --confirm-live`: refuses to run without the flag; reports sample count, failures, latency (median/p95), choice agreement, noul Brier score + accuracy at 0.5, score MAE/exact match; states it is not a calibration claim.
- [ ] Live tests skipped unless `DECISION_BRIDGE_LIVE=1`; record versions, digest, OS, warm/cold; one test per type + mixed. Run once (authorized) against the running Ollama; adjust `responses.py` to real captured shapes and re-run deterministic suites; store one sanitized real capture as a fixture.
- [ ] Final verification: `ruff check`, `ruff format --check`, `mypy`, full non-live pytest, packaging tests, min-Python (3.11) run via `uv run --python 3.11`. Report implementation / local validation / cross-platform / live / publication as separate states.
