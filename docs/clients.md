# Connecting an MCP client

An MCP client starts `decision-bridge serve`; the bridge connects to Ollama; Ollama runs Nimble. Ollama's API address is not an MCP server URL, and this project does not provide an HTTP/SSE MCP endpoint.

Find your executable's absolute path first ([installation](installation.md#finding-the-executable)) and run `decision-bridge doctor` before configuring a client. Nothing here is done for you: installing the package never edits client configuration.

## Status

| Client | Status | Notes |
|---|---|---|
| Any stdio MCP client (official Python SDK client) | **Tested**: 2026-09-30, `mcp` SDK 2.2.0, macOS, against a fake Ollama in the automated suite | Initialization, `tools/list`, both tools, errors, cancellation, shutdown |
| Codex CLI | **Documented, untested end-to-end** | Command syntax checked against `codex mcp add --help` (codex-cli 0.153.4) and the vendor docs on 2026-09-30; no Codex session was run against this server |
| Claude Desktop | **Documented, untested** | From the official MCP docs, 2026-09-30 |
| Cursor | **Documented, untested** | From Cursor's docs, 2026-09-30 |

"Untested" means the protocol is standard and should work, but nobody has run that client against this server. Report results if you try one.

## Generic stdio configuration

This is the *common* configuration shape, not a universal file format. Some clients use different root keys, fields, settings locations, or approval flows. Replace the path.

```json
{
  "mcpServers": {
    "decision-bridge": {
      "command": "/absolute/path/to/decision-bridge",
      "args": ["serve"],
      "env": {
        "DECISION_BRIDGE_OLLAMA_URL": "http://127.0.0.1:11434",
        "DECISION_BRIDGE_MODEL": "nimble:latest"
      }
    }
  }
}
```

On Windows, escape backslashes: `"command": "C:\\Users\\you\\.local\\bin\\decision-bridge.exe"`. The `env` block is optional when the defaults suit you.

## Codex

Current command shape (sources: [Codex MCP docs](https://developers.openai.com/codex/mcp), and `codex mcp add --help`):

```sh
codex mcp add decision-bridge -- /absolute/path/to/decision-bridge serve
codex mcp list
```

Optional environment values: `codex mcp add decision-bridge --env DECISION_BRIDGE_MODEL=nimble:latest -- /absolute/path/to/decision-bridge serve`. Inspect with `codex mcp get decision-bridge`; remove with `codex mcp remove decision-bridge`.

Equivalent `~/.codex/config.toml` entry:

```toml
[mcp_servers.decision-bridge]
command = "/absolute/path/to/decision-bridge"
args = ["serve"]

[mcp_servers.decision-bridge.env]
DECISION_BRIDGE_MODEL = "nimble:latest"
```

Codex documents that config changes require restarting the client; inside the Codex terminal UI, `/mcp` shows active servers.

## Claude Desktop

From the [official MCP guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers): open the Claude menu, then Settings, then Developer, then Edit Config. The file is:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add the generic block above under `mcpServers` (use an absolute path; desktop apps do not reliably inherit your shell `PATH`), save, then **fully quit and restart** Claude Desktop. MCP logs are in `~/Library/Logs/Claude` (macOS) or `%APPDATA%\Claude\logs` (Windows); `mcp-server-decision-bridge.log` holds the bridge's stderr.

## Cursor

From [Cursor's MCP docs](https://cursor.com/docs/context/mcp): put the generic block in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global). Cursor's documented fields for a stdio server are `command`, `args`, and `env` (plus an optional `envFile`). Servers can be toggled in Cursor's settings UI.

## Verify

1. `decision-bridge doctor --smoke-test` in a terminal: it should end with `Result: ready`.
2. In the client, confirm the server shows two tools: `decide` and `bridge_status`.
3. Ask:

   > Use Decision Bridge to classify the supplied request into one of the provided categories. Return the selected category and probabilities. Do not execute the selected action.

If the tools do not appear, see [troubleshooting](troubleshooting.md). For how agents should use the tools, see [agent-usage.md](agent-usage.md).
