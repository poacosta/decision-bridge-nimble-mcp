"""Cheap, bounded health checks shared by `doctor` and the `bridge_status` MCP tool.

Nothing here runs inference except the explicit smoke test in `run_doctor`. A version string is a
diagnostic hint only; it never proves that the decision endpoint works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from decision_bridge import __version__
from decision_bridge.config import Settings
from decision_bridge.errors import BridgeError, ErrorCode
from decision_bridge.ollama import InstalledModel, OllamaClient

if TYPE_CHECKING:
    from decision_bridge.service import DecisionService

MIN_OLLAMA = (0, 35, 0)

SMOKE_INPUT: dict[str, Any] = {
    "state": "The sky is blue.",
    "questions": {
        "color": {
            "type": "choice",
            "instructions": "Which color does the text mention?",
            "criteria": {"blue": "Blue is mentioned.", "red": "Red is mentioned."},
        }
    },
}

_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(text.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


@dataclass
class Check:
    name: str
    state: str  # available | unavailable | unknown | not_checked
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "state": self.state, "detail": self.detail}
        if self.hint:
            data["hint"] = self.hint
        return data


@dataclass
class ProbeResult:
    checks: list[Check]
    version: str | None
    model: InstalledModel | None  # set only when the model is installed, local and usable

    def get(self, name: str) -> Check:
        return next(c for c in self.checks if c.name == name)


def check_model(
    models: list[InstalledModel], name: str
) -> tuple[InstalledModel | None, BridgeError | None]:
    """Return the installed model, or the error that explains why it cannot be used."""
    wanted = {name} | ({f"{name}:latest"} if ":" not in name else set())
    model = next((m for m in models if m.name in wanted), None)
    if model is None:
        return None, BridgeError(
            ErrorCode.MODEL_NOT_FOUND,
            f"The configured model is not installed. Install it explicitly: ollama pull {name}",
        )
    if model.is_remote:
        return model, BridgeError(
            ErrorCode.MODEL_NOT_LOCAL,
            "The configured model is served remotely. Select an installed local model.",
        )
    if model.capabilities and "decision" not in model.capabilities:
        return model, BridgeError(
            ErrorCode.DECISION_UNSUPPORTED,
            "The configured model does not report decision support. Select a Nimble model.",
        )
    return model, None


def _skipped(name: str, why: str) -> Check:
    return Check(name, "unknown", why)


async def probe(client: OllamaClient, settings: Settings) -> ProbeResult:
    checks = [Check("configuration", "available", "Settings are valid.")]
    version: str | None = None
    reachable = True
    try:
        version = await client.get_version()
        checks.append(
            Check("ollama_reachable", "available", f"Ollama answered at {settings.ollama_url}.")
        )
    except BridgeError as exc:
        if exc.code is ErrorCode.OLLAMA_UNAVAILABLE or exc.code is ErrorCode.TIMEOUT:
            reachable = False
            checks.append(
                Check(
                    "ollama_reachable",
                    "unavailable",
                    f"Cannot reach Ollama at {settings.ollama_url}.",
                    "Start Ollama (the desktop app, or `ollama serve`) or fix "
                    "DECISION_BRIDGE_OLLAMA_URL.",
                )
            )
        else:
            checks.append(
                Check(
                    "ollama_reachable",
                    "available",
                    f"A server answered at {settings.ollama_url}, "
                    f"but /api/version failed ({exc.code}).",
                )
            )

    parsed = parse_version(version) if version else None
    if not reachable:
        checks.append(_skipped("ollama_version", "Not checked: Ollama is unreachable."))
    elif parsed is None:
        checks.append(_skipped("ollama_version", "Ollama did not report a usable version."))
    elif parsed < MIN_OLLAMA:
        checks.append(
            Check(
                "ollama_version",
                "unavailable",
                f"Ollama {version} is older than 0.35, which Nimble decisions require.",
                "Upgrade Ollama from https://ollama.com/download.",
            )
        )
    else:
        checks.append(
            Check(
                "ollama_version",
                "available",
                f"Ollama {version} (version checks are diagnostic only; "
                "they do not prove the decision endpoint works).",
            )
        )

    model: InstalledModel | None = None
    names = ("model_installed", "model_local", "model_decision_capable")
    models: list[InstalledModel] | None = None
    if reachable:
        try:
            models = await client.list_models()
        except BridgeError:
            models = None
    if models is None:
        why = (
            "Not checked: Ollama is unreachable."
            if not reachable
            else "Could not list installed models."
        )
        checks.extend(_skipped(n, why) for n in names)
        return ProbeResult(checks, version, None)

    found, problem = check_model(models, settings.model)
    if found is None:
        checks.append(
            Check(
                "model_installed",
                "unavailable",
                f"Model {settings.model!r} is not installed.",
                f"Install it explicitly: ollama pull {settings.model}",
            )
        )
        checks.append(_skipped("model_local", "Not checked: model is not installed."))
        checks.append(_skipped("model_decision_capable", "Not checked: model is not installed."))
        return ProbeResult(checks, version, None)

    digest = f" (digest {found.digest[:12]})" if found.digest else ""
    checks.append(
        Check("model_installed", "available", f"Model {found.name!r} is installed{digest}.")
    )
    if found.is_remote:
        checks.append(
            Check(
                "model_local",
                "unavailable",
                "The model is served remotely, not from this machine.",
                "Select an installed local model with DECISION_BRIDGE_MODEL.",
            )
        )
    else:
        checks.append(Check("model_local", "available", "The model is stored locally."))
    if not found.capabilities:
        checks.append(
            _skipped("model_decision_capable", "The server did not report model capabilities.")
        )
    elif "decision" in found.capabilities:
        checks.append(
            Check("model_decision_capable", "available", "The model reports decision support.")
        )
    else:
        checks.append(
            Check(
                "model_decision_capable",
                "unavailable",
                "The model does not report decision support.",
                "Select a Nimble model with DECISION_BRIDGE_MODEL.",
            )
        )
    if problem is None:
        model = found
    return ProbeResult(checks, version, model)


@dataclass
class DoctorReport:
    ok: bool
    checks: list[Check]
    inference_verified: bool
    bridge_version: str = __version__
    model: str = ""
    endpoint: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bridge_version": self.bridge_version,
            "model": self.model,
            "endpoint": self.endpoint,
            "inference_verified": self.inference_verified,
            "checks": [c.to_dict() for c in self.checks],
        }

    def render_text(self) -> str:
        marks = {
            "available": "OK  ",
            "unavailable": "FAIL",
            "unknown": "??  ",
            "not_checked": "--  ",
        }
        lines = [
            f"decision-bridge {self.bridge_version}  model={self.model}  endpoint={self.endpoint}"
        ]
        for check in self.checks:
            lines.append(f"[{marks[check.state]}] {check.name}: {check.detail}")
            if check.hint:
                lines.append(f"       next step: {check.hint}")
        lines.append("")
        if self.inference_verified:
            lines.append("Inference readiness: verified by a real decision request.")
        elif any(c.name == "decision_smoke_test" and c.state == "unavailable" for c in self.checks):
            lines.append("Inference readiness: NOT verified; the smoke test failed (see above).")
        else:
            lines.append(
                "Inference readiness is unverified: no decision request was sent. "
                "Run `decision-bridge doctor --smoke-test` to verify."
            )
        lines.append("Result: " + ("ready" if self.ok else "problems found"))
        return "\n".join(lines)


async def run_doctor(service: DecisionService, *, smoke_test: bool) -> DoctorReport:
    result = await service.inspect()
    checks = list(result.checks)
    verified = False
    if smoke_test:
        try:
            await service.decide(SMOKE_INPUT)
        except BridgeError as exc:
            checks.append(Check("decision_smoke_test", "unavailable", f"{exc.code}: {exc.message}"))
        else:
            verified = True
            checks.append(
                Check("decision_smoke_test", "available", "A synthetic decision request succeeded.")
            )
    else:
        checks.append(
            Check(
                "decision_smoke_test",
                "not_checked",
                "Not run; add --smoke-test to send one request.",
            )
        )
    return DoctorReport(
        ok=all(c.state != "unavailable" for c in checks),
        checks=checks,
        inference_verified=verified,
        model=service.settings.model,
        endpoint=service.settings.ollama_url,
    )
