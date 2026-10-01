"""Settings: explicit overrides, then environment variables, then defaults."""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from decision_bridge.errors import BridgeError, ErrorCode

_PREFIX = "DECISION_BRIDGE_"
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_DURATION = re.compile(r"^(-?\d+|(\d+(\.\d+)?(ms|s|m|h))+)$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "nimble:latest"
    connect_timeout: float = 5.0
    request_timeout: float = 120.0
    keep_alive: str | None = None
    max_concurrency: int = 1
    allow_remote: bool = False
    log_level: str = "WARNING"


def _bad(name: str, why: str) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_CONFIGURATION, f"{name}: {why}")


def _positive_float(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise _bad(name, f"expected a number of seconds, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise _bad(name, "must be a finite number greater than zero")
    return value


def _bool(name: str, raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise _bad(name, "expected true or false")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_url(raw: str, allow_remote: bool) -> str:
    name = _PREFIX + "OLLAMA_URL"
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise _bad(name, "expected an http(s) origin such as http://127.0.0.1:11434")
    if parts.username or parts.password:
        raise _bad(name, "credentials in the URL are not supported")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise _bad(name, "use a bare origin: no path, query, or fragment")
    try:
        _ = parts.port
    except ValueError:
        raise _bad(name, "invalid port") from None
    if not allow_remote and not _is_loopback(parts.hostname):
        raise _bad(
            name,
            "non-loopback endpoint refused; set DECISION_BRIDGE_ALLOW_REMOTE=true to permit it",
        )
    return f"{parts.scheme}://{parts.netloc}"


def _check_model(raw: str) -> str:
    model = raw.strip()
    if not model:
        raise _bad(_PREFIX + "MODEL", "must not be empty")
    if model.endswith("-cloud") or model.endswith(":cloud"):
        raise BridgeError(
            ErrorCode.MODEL_NOT_LOCAL,
            "Cloud model selectors are not supported; choose an installed local model.",
        )
    return model


def load_settings(
    env: Mapping[str, str], overrides: Mapping[str, object] | None = None
) -> Settings:
    """Build validated settings. `overrides` (CLI options) win; None values are ignored."""
    values: dict[str, str] = {}
    env_names = {
        "ollama_url": "OLLAMA_URL",
        "model": "MODEL",
        "connect_timeout": "CONNECT_TIMEOUT_SECONDS",
        "request_timeout": "REQUEST_TIMEOUT_SECONDS",
        "keep_alive": "KEEP_ALIVE",
        "max_concurrency": "MAX_CONCURRENCY",
        "allow_remote": "ALLOW_REMOTE",
        "log_level": "LOG_LEVEL",
    }
    for key in env_names:
        override = (overrides or {}).get(key)
        if override is not None:
            values[key] = str(override)
        elif _PREFIX + env_names[key] in env:
            values[key] = env[_PREFIX + env_names[key]]

    d = Settings()
    allow_remote = (
        _bool(_PREFIX + "ALLOW_REMOTE", values["allow_remote"])
        if "allow_remote" in values
        else d.allow_remote
    )

    keep_alive = d.keep_alive
    if "keep_alive" in values:
        keep_alive = values["keep_alive"].strip()
        if not _DURATION.match(keep_alive):
            raise _bad(_PREFIX + "KEEP_ALIVE", "expected a duration such as 5m, 1h30m, 0, or -1")

    max_concurrency = d.max_concurrency
    if "max_concurrency" in values:
        try:
            max_concurrency = int(values["max_concurrency"])
        except ValueError:
            raise _bad(_PREFIX + "MAX_CONCURRENCY", "expected an integer from 1 to 32") from None
        if not 1 <= max_concurrency <= 32:
            raise _bad(_PREFIX + "MAX_CONCURRENCY", "expected an integer from 1 to 32")

    log_level = values.get("log_level", d.log_level).strip().upper()
    if log_level not in _LOG_LEVELS:
        raise _bad(_PREFIX + "LOG_LEVEL", f"expected one of {', '.join(_LOG_LEVELS)}")

    return Settings(
        ollama_url=_check_url(values.get("ollama_url", d.ollama_url), allow_remote),
        model=_check_model(values.get("model", d.model)),
        connect_timeout=(
            _positive_float(_PREFIX + "CONNECT_TIMEOUT_SECONDS", values["connect_timeout"])
            if "connect_timeout" in values
            else d.connect_timeout
        ),
        request_timeout=(
            _positive_float(_PREFIX + "REQUEST_TIMEOUT_SECONDS", values["request_timeout"])
            if "request_timeout" in values
            else d.request_timeout
        ),
        keep_alive=keep_alive,
        max_concurrency=max_concurrency,
        allow_remote=allow_remote,
        log_level=log_level,
    )
