# Security

## Reporting a vulnerability

**No security contact has been set up yet.** Before this project is published, the owner must add either an approved private contact address or an enabled private vulnerability-reporting mechanism here. Until then, please do not disclose issues publicly; contact the project owner directly.

## What the bridge does, security-wise

- Runs locally as a child process of your MCP client and talks to one operator-configured Ollama origin over HTTP.
- Accepts only loopback endpoints by default. Non-loopback endpoints require `DECISION_BRIDGE_ALLOW_REMOTE=true`. Remote mode does **not** add authentication, TLS, or trust: the evidence goes to whatever server you configure.
- Callers of the MCP tools cannot choose the URL, model, headers, timeouts, or remote policy; unknown tool arguments are rejected.
- Redirects are not followed and proxy environment variables are ignored for the Ollama connection.
- Enforces bounds on request size (64 KiB), upstream response size (1 MiB), CLI input size, concurrency, queue depth, and total time.
- Does not log evidence, questions, answers, or raw upstream error bodies; error messages never echo upstream bodies.
- Does not execute shell commands, edit files, download models, or manage Ollama. Outputs are advisory and never authorize actions.

## What it does not protect against

- A wrong or manipulated model answer. Evidence you send may contain prompt-injection text that steers the model. Treat results as untrusted advice and keep your own approvals and checks.
- A malicious or compromised Ollama server, or a network attacker on a remote endpoint.
- Anything your MCP client or calling agent does with the data before or after the bridge.

## Supported versions

Pre-release: only the current source tree is supported.
