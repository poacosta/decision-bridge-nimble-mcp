## What and why

<!-- Describe the change and the problem it solves. Link an issue if there is one. -->

## Checklist

- [ ] Tests were written first and cover the change (the default suite needs no Ollama, network, or model)
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and `uv run pytest` pass
- [ ] Nothing logs prompts, evidence, answers, or raw upstream error bodies, and stdout stays protocol-only while serving
- [ ] Docs are updated where behavior changed
- [ ] No secrets, private evidence, or model weights are included
