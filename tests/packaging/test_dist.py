"""Build, inspect, and install the distributions. Run with `pytest -m packaging` (slower)."""

from __future__ import annotations

import email
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.packaging

ROOT = Path(__file__).resolve().parent.parent.parent
REPO_URL = "https://github.com/poacosta/decision-bridge-nimble-mcp"
FORBIDDEN = (
    ".env",
    ".venv",
    ".superpowers",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git/",
    ".gguf",
    ".safetensors",
    "docs/superpowers",
)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)


def exe(venv: Path) -> Path:
    return venv / ("Scripts/decision-bridge.exe" if os.name == "nt" else "bin/decision-bridge")


def python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def make_venv(path: Path) -> Path:
    done = run(
        ["uv", "venv", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", str(path)]
    )
    assert done.returncode == 0, done.stderr
    return path


def pip_install(venv: Path, target: Path | str):
    done = run(["uv", "pip", "install", "--python", str(python(venv)), str(target)])
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture(scope="module")
def dist_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    done = run(["uv", "build", "--out-dir", str(out)], cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return out


@pytest.fixture(scope="module")
def wheel(dist_dir) -> Path:
    (path,) = dist_dir.glob("*.whl")
    return path


@pytest.fixture(scope="module")
def sdist(dist_dir) -> Path:
    (path,) = dist_dir.glob("*.tar.gz")
    return path


def test_wheel_contents_and_metadata(wheel):
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta = email.message_from_string(
            zf.read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode()
        )
    assert all(n.startswith(("decision_bridge/", "decision_bridge_nimble_mcp-")) for n in names)
    assert not [n for n in names for bad in FORBIDDEN if bad in n]
    assert meta["Name"] == "decision-bridge-nimble-mcp"
    assert meta["Version"] == "0.1.0"
    assert meta["Requires-Python"] == ">=3.11"
    requires = " ".join(meta.get_all("Requires-Dist") or [])
    for dep in ("mcp", "pydantic", "httpx"):
        assert dep in requires
    for banned in ("torch", "transformers", "mlx", "cuda"):
        assert banned not in requires.lower()
    project_urls = dict(u.split(", ", 1) for u in meta.get_all("Project-URL") or [])
    assert set(project_urls) == {"Homepage", "Source", "Issues", "Changelog"}
    assert all(url.startswith(REPO_URL) for url in project_urls.values())
    assert meta["License-Expression"] == "MIT"  # an SPDX expression, not a License :: classifier
    classifiers = meta.get_all("Classifier") or []
    assert "Development Status :: 3 - Alpha" in classifiers
    assert not [c for c in classifiers if c.startswith("License ::")]
    # The owner's MIT license ships with the wheel, and it is the repository's own file.
    assert meta.get_all("License-File") == ["LICENSE"]
    with zipfile.ZipFile(wheel) as zf:
        shipped = next(n for n in zf.namelist() if n.endswith("licenses/LICENSE"))
        # Compare bytes: a Windows checkout may convert the file to CRLF, and the wheel carries
        # the file exactly as checked out. Text mode would hide that and compare the wrong thing.
        assert zf.read(shipped) == (ROOT / "LICENSE").read_bytes()


def test_classifiers_are_valid_trove_classifiers(wheel):
    from trove_classifiers import classifiers as valid

    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = email.message_from_string(zf.read(meta_name).decode())
    declared = meta.get_all("Classifier") or []
    assert declared
    assert [c for c in declared if c not in valid] == []  # PyPI rejects unknown classifiers


def test_sdist_contents(sdist):
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    assert not [n for n in names for bad in FORBIDDEN if bad in n]
    joined = "\n".join(names)
    for expected in (
        "pyproject.toml",
        "README.md",
        "examples/mixed-questions.json",
        "src/decision_bridge/cli.py",
        "LICENSE",
    ):
        assert expected in joined


def test_installed_wheel_runs_offline_commands_and_serves_mcp(tmp_path, wheel, fake_ollama):
    venv = make_venv(tmp_path / "venv")
    pip_install(venv, wheel)
    where = run(
        [str(python(venv)), "-c", "import decision_bridge; print(decision_bridge.__file__)"],
        cwd=tmp_path,
    )
    assert str(venv) in where.stdout  # the installed wheel, not the source tree

    for args in (["--version"], ["--help"]):
        assert run([str(exe(venv)), *args], cwd=tmp_path).returncode == 0

    env = {**os.environ, "DECISION_BRIDGE_OLLAMA_URL": fake_ollama.url}
    doctor = run([str(exe(venv)), "doctor", "--json", "--smoke-test"], cwd=tmp_path, env=env)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert json.loads(doctor.stdout)["inference_verified"] is True

    import anyio

    async def call():
        params = StdioServerParameters(
            command=str(exe(venv)),
            args=["serve"],
            cwd=tmp_path,
            env={"DECISION_BRIDGE_OLLAMA_URL": fake_ollama.url},
        )
        example = json.loads((ROOT / "examples" / "mixed-questions.json").read_text("utf-8"))
        async with Client(params) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("decide", example)
        return tools, result

    tools, result = anyio.run(call)
    assert tools == {"decide", "bridge_status"}
    assert result.is_error is False
    assert list(result.structured_content["answers"]) == [
        "route",
        "mentions_payment",
        "evidence_detail",
    ]


def test_sdist_without_git_metadata_installs(tmp_path, sdist):
    with tarfile.open(sdist) as tf:
        tf.extractall(tmp_path / "unpacked", filter="data")
    (project,) = (tmp_path / "unpacked").iterdir()
    assert not (project / ".git").exists()
    venv = make_venv(tmp_path / "venv")
    pip_install(venv, project)
    assert (
        run([str(exe(venv)), "--version"], cwd=tmp_path).stdout.strip() == "decision-bridge 0.1.0"
    )


def test_repository_zip_style_copy_without_git_installs_from_a_path_with_spaces(tmp_path):
    copy = tmp_path / "extracted repo (zip)"
    shutil.copytree(
        ROOT,
        copy,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "dist",
            "build",
            "__pycache__",
            ".superpowers",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "*.egg-info",
        ),
    )
    assert not (copy / ".git").exists()
    venv = make_venv(tmp_path / "venv")
    pip_install(venv, copy)
    assert (
        run([str(exe(venv)), "--version"], cwd=tmp_path).stdout.strip() == "decision-bridge 0.1.0"
    )
