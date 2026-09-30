# Contributing

## Setup

```sh
uv sync --locked
```

## Checks (run before proposing a change)

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                 # deterministic, protocol, and unit tests; no Ollama needed
uv run pytest -m packaging    # builds the wheel/sdist and installs them in fresh environments
```

The default suite uses a fake Ollama HTTP server (`tests/fakes/fake_ollama.py`). It never needs network access, a model download, or a GPU.

## Live tests (opt-in)

They need a running Ollama with the model **already installed**; they never pull anything.

```sh
DECISION_BRIDGE_LIVE=1 uv run pytest -m live
```

They check protocol/contract behavior, not prediction quality, and record versions and the model digest. Probabilistic answers must not make the deterministic suite flaky, so do not assert specific labels there.

## Guidelines

- Write tests first; watch them fail; keep changes small.
- Keep the runtime dependency list short. Do not import PyTorch, Transformers, CUDA, or MLX: the bridge only speaks HTTP.
- Never log evidence, answers, or raw upstream error bodies. Add a leak test for new logging.
- Keep stdout clean while serving.
- Do not add features listed as deferred in the brief without discussion.
- Documentation and code are in English. Commit messages should describe the change plainly.
