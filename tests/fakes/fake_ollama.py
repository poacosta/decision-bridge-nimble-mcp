"""A small fake Ollama HTTP server for tests: records requests, serves programmable responses."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class FakeResponse:
    status: int = 200
    body: Any = None  # dict/list -> JSON, bytes -> raw, str -> utf-8 text
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0  # seconds to wait before answering (released early by FakeOllama.stop)


def valid_answers(questions: dict[str, Any]) -> dict[str, Any]:
    """Mocked but coherent answers; shapes follow tests/fixtures/live_mixed_capture.json."""
    answers: dict[str, Any] = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            labels = list(q["criteria"])
            rest = 0.3 / (len(labels) - 1)
            probs = {label: (0.7 if i == 0 else rest) for i, label in enumerate(labels)}
            answers[qid] = {
                "type": "choice",
                "choice": labels[0],
                "probabilities": probs,
                "confidence": 0.5,
            }
        elif q["type"] == "noul":
            answers[qid] = {"type": "noul", "noul": 0.75}
        else:
            levels = q["criteria"]
            share = 1 / len(levels)
            answers[qid] = {
                "type": "score",
                "score": 0.6,
                "legend": {str(i): text for i, text in enumerate(levels)},
                "probabilities": {str(i): share for i in range(len(levels))},
                "confidence": 0.25,
            }
    return answers


def default_decision(rec: Recorded) -> FakeResponse:
    req = rec.json()
    return FakeResponse(
        body={
            "model": req["model"],
            "answers": valid_answers(req["questions"]),
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
    )


def default_tags(_: Recorded) -> FakeResponse:
    return FakeResponse(
        body={
            "models": [
                {
                    "name": "nimble:latest",
                    "model": "nimble:latest",
                    "digest": "deadbeef",
                    "capabilities": ["decision", "completion"],
                },
                {
                    "name": "some-cloud:cloud",
                    "model": "some-cloud:cloud",
                    "remote_model": "some-cloud",
                    "remote_host": "https://ollama.com:443",
                },
            ]
        }
    )


Handler = Callable[[Recorded], FakeResponse]


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind calls socket.getfqdn(), a multi-second reverse-DNS lookup on some
        # hosts. The fake only ever binds loopback, so skip it.
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class FakeOllama:
    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.routes: dict[tuple[str, str], Handler] = {
            ("POST", "/v1/systemone"): default_decision,
            ("GET", "/api/version"): lambda _: FakeResponse(body={"version": "0.35.0"}),
            ("GET", "/api/tags"): default_tags,
        }
        self._release = threading.Event()
        self._lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _serve(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                rec = Recorded(
                    self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body
                )
                with outer._lock:
                    outer.requests.append(rec)
                handler = outer.routes.get((self.command, self.path.split("?")[0]))
                if handler is None:
                    resp = FakeResponse(404, "404 page not found\n", "text/plain")
                else:
                    resp = handler(rec)
                if resp.delay:
                    outer._release.wait(resp.delay)
                payload, ctype = _encode(resp)
                self.send_response(resp.status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(payload)))
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(payload)

            do_GET = do_POST = _serve

        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> FakeOllama:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()

    def calls(self, path: str) -> list[Recorded]:
        with self._lock:
            return [r for r in self.requests if r.path.split("?")[0] == path]

    def set(self, method: str, path: str, handler: Handler | FakeResponse) -> None:
        self.routes[(method, path)] = handler if callable(handler) else (lambda _: handler)

    def __enter__(self) -> FakeOllama:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _encode(resp: FakeResponse) -> tuple[bytes, str]:
    if isinstance(resp.body, bytes):
        return resp.body, resp.content_type or "application/octet-stream"
    if isinstance(resp.body, str):
        return resp.body.encode(), resp.content_type or "text/plain"
    if resp.body is None:
        return b"", resp.content_type or "text/plain"
    return json.dumps(resp.body).encode(), resp.content_type or "application/json; charset=utf-8"
