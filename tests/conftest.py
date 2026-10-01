from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mixed_example() -> dict:
    return json.loads((ROOT / "examples" / "mixed-questions.json").read_text(encoding="utf-8"))


@pytest.fixture
def fake_ollama():
    from tests.fakes.fake_ollama import FakeOllama

    with FakeOllama() as server:
        yield server
