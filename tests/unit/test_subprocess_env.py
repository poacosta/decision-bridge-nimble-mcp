from tests.helpers import subprocess_env


def test_keeps_windows_essentials_that_asyncio_needs(monkeypatch):
    # On Windows, importing asyncio in a child with no SYSTEMROOT fails with WinError 10106
    # (Winsock cannot initialize), so a "minimal" child environment must carry it over.
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("TEMP", r"C:\Temp")
    env = subprocess_env(DECISION_BRIDGE_OLLAMA_URL="http://127.0.0.1:9", PATH="")
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["TEMP"] == r"C:\Temp"
    assert env["DECISION_BRIDGE_OLLAMA_URL"] == "http://127.0.0.1:9"
    assert env["PATH"] == ""


def test_stays_minimal_and_does_not_leak_the_parent_environment(monkeypatch):
    monkeypatch.setenv("SOME_PARENT_SECRET", "x")
    monkeypatch.setenv("DECISION_BRIDGE_MODEL", "from-parent")
    env = subprocess_env()
    assert "SOME_PARENT_SECRET" not in env
    assert "DECISION_BRIDGE_MODEL" not in env


def test_explicit_values_win_over_inherited_ones(monkeypatch):
    monkeypatch.setenv("TEMP", "inherited")
    assert subprocess_env(TEMP="explicit")["TEMP"] == "explicit"


def test_skips_essentials_that_are_not_set(monkeypatch):
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    assert "SYSTEMROOT" not in subprocess_env()
