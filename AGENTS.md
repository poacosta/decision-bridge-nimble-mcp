# Instructions for coding agents

Decision Bridge: a small Python MCP server and CLI that send typed decision requests to Nimble through Ollama's `/v1/systemone`.

- Source in `src/decision_bridge/`: `schemas` (input, request bytes), `responses` (upstream validation), `ollama` (only module that does HTTP), `service` (shared by CLI and MCP), `diagnostics`, `server` (MCP), `cli`.
- Commands: `uv sync --locked`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -m packaging`.
- Tests first. The default suite must never need Ollama, network, or a model; live tests are opt-in (`DECISION_BRIDGE_LIVE=1`).
- Never download models or start/stop/configure Ollama. Never edit a user's global MCP configuration.
- stdout is protocol-only while serving. Never log prompts, evidence, answers, or raw upstream error bodies.
- Do not silently truncate input, retry inference, round upstream numbers, or accept invalid upstream answers.
- Do not push, publish, or open pull requests without explicit permission. The license and security contact are owner decisions (see `docs/release-checklist.md`).
- Commits: plain, human-voiced messages; no assistant/model attribution or co-author trailers.
