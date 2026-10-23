"""A fake model server (Ollama /api/chat and OpenAI /v1/chat/completions, both streaming) that tests script."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jade import Jade


class FakeModel:
    def __init__(self):
        self.script: list = []      # each item: dict(content=, tool_calls=[(name, args)], thinking=) or callable(body) -> dict
        self.requests: list[dict] = []
        self.default = lambda body: {"content": "ok: " + str(body["messages"][-1].get("content"))[-60:]}

    def reply(self, body: dict) -> dict:
        self.requests.append(body)
        item = self.script.pop(0) if self.script else self.default
        return item(body) if callable(item) else item


def make_handler(fake: FakeModel):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == "/api/tags":
                return self._json(200, {"models": [{"name": "fake:1b"}]})
            if self.path == "/v1/models":
                return self._json(200, {"data": [{"id": "fake-oai"}]})
            self._json(404, {"error": "no"})

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/embed":
                return self._json(200, {"embeddings": [[float(len(t) % 7), 1.0, float(t.count("a"))] for t in body.get("input", [])]})
            r = fake.reply(body)
            if r.get("status"):
                return self._json(r["status"], {"error": r.get("error", "boom")})
            if self.path == "/api/chat":
                msg = {"role": "assistant", "content": r.get("content", "")}
                if r.get("thinking"):
                    msg["thinking"] = r["thinking"]
                if r.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": n_, "arguments": a}} for n_, a in r["tool_calls"]]
                if not body.get("stream"):
                    return self._json(200, {"message": msg, "done": True})
                self.send_response(200)
                self.send_header("content-type", "application/x-ndjson")
                self.end_headers()
                text = msg.pop("content")
                half = len(text) // 2
                for part in (text[:half], text[half:]):
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": part}, "done": False}) + "\n").encode())
                self.wfile.write((json.dumps({"message": {**msg, "content": ""}, "done": True, "prompt_eval_count": 100, "eval_count": 10}) + "\n").encode())
                return
            if self.path == "/v1/chat/completions":
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                def ev(o):
                    self.wfile.write(f"data: {json.dumps(o)}\n\n".encode())
                text = r.get("content", "")
                for part in (text[: len(text) // 2], text[len(text) // 2:]):
                    if part:
                        ev({"choices": [{"index": 0, "delta": {"content": part}}]})
                for i, (n_, a) in enumerate(r.get("tool_calls") or []):
                    s = json.dumps(a)
                    ev({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": f"c{i}", "function": {"name": n_, "arguments": s[:5]}}]}}]})
                    ev({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": s[5:]}}]}}]})
                ev({"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5}})
                self.wfile.write(b"data: [DONE]\n\n")
                return
            self._json(404, {"error": "no"})
    return H


@pytest.fixture
def fake():
    f = FakeModel()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(f))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    f.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield f
    srv.shutdown()


@pytest.fixture
def jade(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "none.toml"))
    j = Jade(tmp_path / "vault", data_dir=tmp_path / "data", host=fake.url, model="fake:1b", journal=False, embed_on=False)
    yield j
