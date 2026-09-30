# Release checklist

Reported as separate states. Nothing below has been pushed, published, or released.

## Status at handoff

| Area | State |
|---|---|
| Implementation (0.1.0) | Complete for the scope in the brief |
| Local validation | 2026-09-30, macOS arm64: `ruff` and `mypy` clean; 288 default tests (unit, integration, real-subprocess MCP protocol) pass on Python 3.11.14, 3.12.3, and 3.13.12; 5 packaging tests pass (wheel and sdist contents/metadata, installed-wheel MCP round trip, Git-less sdist, ZIP-style copy in a path with spaces) on 3.12; `uv tool install .` verified in isolated directories |
| Cross-platform validation | **macOS only** so far. Linux and Windows are covered by the CI matrix definition but have not been run |
| Live-model validation | Run once on 2026-09-30: macOS, Ollama 0.35.0, `nimble:latest` digest `24e550a16a7081881be2f1f0d91e8cc13a597472735c04119f035a0a85c67e0c` (see `docs/evaluation.md`) |
| MCP client validation | Official SDK client end to end. Claude Code: registered and its health check reports connected. Codex: registered, not connected end to end. Claude Desktop, Cursor, Antigravity: untested. No tool call from a live agent session yet ([clients](clients.md)) |
| Publication | Not published; not authorized |

## Blocking decisions (owner)

- [x] **License**: MIT, added by the owner as `LICENSE` and shipped in the wheel and sdist (a packaging test asserts it).
- [ ] **License metadata**: add the matching `license` field (SPDX `MIT`) to `pyproject.toml` so registries show it; the `LICENSE` file alone is not declared in the package metadata's license field.
- [ ] **Repository identity**: real repository URL and owner. No URL, username, or project links are currently in package metadata or docs.
- [ ] **Package name**: confirm `decision-bridge-nimble-mcp` is available and desired on the registry.
- [ ] **Security contact**: an owner-approved private contact or an enabled private advisory mechanism, then update `SECURITY.md`.
- [ ] **Explicit permission to publish** a release and/or package.

## Before tagging

- [ ] All required checks pass on a clean checkout: `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run pytest -m packaging`.
- [ ] CI green on macOS, Linux, and Windows for every Python in the matrix. Pin third-party GitHub Actions by commit SHA after reviewing them.
- [ ] Re-verify the MCP SDK behavior the server relies on (the middleware hook is documented as subject to change in a 2.x minor release) and that `uv.lock` is current.
- [ ] Re-check the Nimble/Ollama contract at [ollama.com/library/nimble](https://ollama.com/library/nimble:latest) and record the Ollama version and model digest used for live verification. `latest` can change.
- [ ] Run live tests and the evaluation script on a machine with the model (`DECISION_BRIDGE_LIVE=1 uv run pytest -m live`).
- [ ] Try at least one real MCP client end to end, and update the status table in [clients](clients.md) only with what was actually run.
- [ ] Update `CHANGELOG.md`, confirm the version in `src/decision_bridge/__init__.py`, and inspect the built wheel and sdist contents (the packaging tests assert no `.env`, caches, or weights).
- [ ] Confirm docs contain no unverified claims, fake badges, or invented benchmark figures.

## Publishing (only if authorized)

- [ ] Use a protected, explicitly triggered workflow with registry-supported trusted publishing. Do not add automatic publication to the regular CI.
- [ ] After publication, add the pinned `uvx --from decision-bridge-nimble-mcp==<version>` instructions and upgrade/uninstall notes.

## Attribution and notices

Original project code is attributed to Pedro Acosta. No model weights are redistributed. Ollama, the Nimble weights, and Python dependencies keep their own licenses; retain notices if any upstream material is ever copied. The project does not imply endorsement by Ollama, Bespoke Labs, the MCP maintainers, or any agent vendor.
