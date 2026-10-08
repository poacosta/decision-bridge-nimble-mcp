# Installation

Every route below installs only the bridge. None of them downloads a model, starts or configures Ollama, or edits any MCP client's configuration.

Before any install: install [Ollama](https://ollama.com/download), make sure its server is running (the desktop app usually does this; run `ollama serve` only if nothing answers on port 11434, so you do not start a second server), and pull the model yourself:

```sh
ollama pull nimble:latest
```

## Path A: from a source download (works today)

1. Install `uv`: <https://docs.astral.sh/uv/getting-started/installation/>.
2. Download the [repository ZIP](https://github.com/poacosta/decision-bridge-nimble-mcp/archive/refs/heads/main.zip) and extract it (Git is not required), or clone it: `git clone https://github.com/poacosta/decision-bridge-nimble-mcp.git`.
3. In the extracted directory:

   ```sh
   uv tool install .
   decision-bridge doctor
   decision-bridge doctor --smoke-test
   decision-bridge decide --input examples/task-routing.json
   ```

   To pick up local changes later: `uv tool install --reinstall .`

Upgrade and removal:

```sh
uv tool install --reinstall .   # after updating the source
uv tool uninstall decision-bridge-nimble-mcp
uv tool list
```

### Development checkout

```sh
uv sync --locked
uv run decision-bridge doctor
uv run decision-bridge serve
```

### Local wheel

```sh
uv build
uv tool install dist/decision_bridge_nimble_mcp-0.1.0-py3-none-any.whl
```

### pip and a virtual environment

macOS / Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
decision-bridge --version
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install .
decision-bridge --version
```

Installing a built wheel works the same way: `python -m pip install dist/<wheel file>`.

## Path B: package registry (only after an authorized release)

Nothing has been published, and the package name has not been confirmed. Once a release exists, the following are designed to work:

```sh
uvx --from decision-bridge-nimble-mcp decision-bridge doctor
uvx --from decision-bridge-nimble-mcp decision-bridge serve
```

After publication these docs will show an explicitly pinned, tested release for reproducible client setups (`decision-bridge-nimble-mcp==<version>`). Do not use these commands today. See [uv's tool documentation](https://docs.astral.sh/uv/guides/tools/).

## Finding the executable

MCP clients need to launch `decision-bridge`. GUI applications (desktop clients) often do **not** inherit your terminal's `PATH`, so prefer an absolute path in client configuration.

| Install method | macOS / Linux | Windows |
|---|---|---|
| `uv tool install` | `uv tool dir --bin` prints the directory (commonly `~/.local/bin`); the file is `decision-bridge` there | same command; the file is `decision-bridge.exe` there |
| Look it up | `which decision-bridge` | `where decision-bridge` |
| virtualenv | `.venv/bin/decision-bridge` (absolute path of your project) | `.venv\Scripts\decision-bridge.exe` |

If `decision-bridge` is not found in a terminal after `uv tool install`, run `uv tool update-shell` and open a new terminal.

In JSON client configs on Windows, escape backslashes: `"C:\\Users\\you\\.local\\bin\\decision-bridge.exe"`. Paths with spaces are fine as a single JSON string; in a shell, quote them (`decision-bridge decide --input "C:\My Files\request.json"`).

## Supported platforms

The bridge is pure Python and aims to work on macOS, Linux, and Windows. Continuous integration runs the test suite on Linux, macOS, and Windows against a fake Ollama server; runs with a real model have so far been done on macOS only (see [evaluation](evaluation.md)). Whether Ollama and the Nimble model run acceptably on your hardware is a separate question the bridge cannot answer; see the [hardware guide](hardware.md) and the [model page](https://ollama.com/library/nimble:latest).

## Docker, remote agents, and `localhost`

Native Ollama is the easiest route. If the agent or bridge runs inside a container or on another machine, its `localhost` is not the host running Ollama: set `DECISION_BRIDGE_OLLAMA_URL` to a reachable address and `DECISION_BRIDGE_ALLOW_REMOTE=true` ([configuration](configuration.md)), and make sure Ollama listens on that interface (see the [Ollama FAQ](https://docs.ollama.com/faq)). On macOS, running Ollama in a Linux container does not automatically keep native Metal acceleration.
