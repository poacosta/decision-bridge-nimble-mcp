import subprocess
import sys

from decision_bridge import __version__
from decision_bridge.cli import main
from tests.helpers import subprocess_env


def test_version_flag_prints_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out.strip() == f"decision-bridge {__version__}"


def test_no_arguments_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    assert "serve" in capsys.readouterr().out


def test_module_help_works_offline():
    env = subprocess_env(DECISION_BRIDGE_OLLAMA_URL="http://127.0.0.1:9", PATH="")
    proc = subprocess.run(
        [sys.executable, "-m", "decision_bridge", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert proc.returncode == 0
    assert "doctor" in proc.stdout
