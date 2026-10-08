# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Not yet released.

## [Unreleased] - 0.1.0

### Added

- MCP stdio server with exactly two tools: `decide` and `bridge_status`.
- CLI: `serve`, `doctor` (with `--json` and `--smoke-test`), `decide`, `--version`.
- `choice`, `noul` (probability), and `score` question types over Ollama's `POST /v1/systemone`.
- Strict input validation (including duplicate JSON keys), exact 64 KiB request-size enforcement, and full validation of upstream responses against the submitted questions.
- Bounded concurrency and queue, one total deadline, cancellation, and stable error codes.
- Environment/CLI configuration with loopback-only default and local-model enforcement.
- MIT License, with `license`, project URLs, and classifiers in the package metadata.
- Community files: code of conduct, issue forms, pull request template, and Dependabot configuration. CI actions are pinned by commit SHA.
- `.gitattributes` to normalize line endings to LF.
- Hardware guide (`docs/hardware.md`): memory tiers for Apple Silicon, NVIDIA, AMD, and CPU-only machines, and how to check your own.
- Usage guide (`docs/usage.md`) and client setup for Claude Code, Codex, Claude Desktop, Cursor, and Antigravity.
- Deterministic, protocol (real subprocess), packaging, and opt-in live tests; a small evaluation script.
