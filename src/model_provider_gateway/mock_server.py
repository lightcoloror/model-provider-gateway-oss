"""Ephemeral loopback-only OpenAI-compatible stub used by tests and CLI smoke."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "status": "ready"})
            return
        self._json(404, {"error": {"type": "not_found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        server_status = int(getattr(self.server, "response_status", 200))
        server_delay = float(getattr(self.server, "response_delay_seconds", 0.0))
        trigger = str(body.get("mock_trigger") or "")
        if trigger == "429" or server_status == 429:
            self._json(429, {"error": {"type": "rate_limit", "message": "synthetic 429"}})
            return
        if trigger == "500" or server_status >= 500:
            self._json(
                server_status if server_status >= 500 else 500,
                {"error": {"type": "server_error", "message": "synthetic 5xx"}},
            )
            return
        if 400 <= server_status < 500:
            self._json(server_status, {"error": {"type": "client_error", "message": "synthetic 4xx"}})
            return
        if trigger == "timeout":
            server_delay = max(server_delay, 0.25)
        if server_delay:
            time.sleep(server_delay)
        if self.path == "/v1/chat/completions":
            messages = body.get("messages") or []
            user_content = messages[0].get("content") if messages else ""
            is_vision = isinstance(user_content, list) and any(
                item.get("type") == "image_url" for item in user_content if isinstance(item, dict)
            )
            self._json(
                200,
                {
                    "id": "mock-chat-1",
                    "object": "chat.completion",
                    "model": body.get("model", "mock-model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "mock-vision-ok" if is_vision else "mock-ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
            return
        if self.path == "/v1/audio/transcriptions":
            self._json(200, {"text": "mock transcript", "model": body.get("model", "mock-asr")})
            return
        self._json(404, {"error": {"type": "not_found"}})

    def _json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.wfile.write(payload)


@contextmanager
def mock_server(*, status: int = 200, delay_seconds: float = 0.0) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.response_status = status  # type: ignore[attr-defined]
    server.response_delay_seconds = delay_seconds  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def mock_smoke(*, trigger: str = "", timeout_seconds: float = 1.0) -> dict[str, Any]:
    with mock_server() as base_url:
        body = {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "ping"}],
            "mock_trigger": trigger,
        }
        request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read())
                return {"ok": True, "status": response.status, "content": payload["choices"][0]["message"]["content"]}
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            return {"ok": False, "status": exc.code, "error": payload["error"]["type"]}
        except TimeoutError:
            return {"ok": False, "status": "timeout", "error": "timeout"}
