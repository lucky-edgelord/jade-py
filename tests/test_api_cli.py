import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from jade.api import create_app


def test_api_app_compatible_and_more(jade, fake):
    c = TestClient(create_app(jade))
    assert c.get("/v1/status").json()["ok"]
    fake.script = [{"content": "", "tool_calls": [("notes_search", {"query": "Inbox"})]}, {"content": "Found [[Inbox]]."}]
    r = c.get("/v1/chat", params={"message": "find my inbox"}).json()
    assert r["ok"] and r["reply"] == "Found [[Inbox]]." and r["tool_calls"][0]["name"] == "notes_search" and r["tool_calls"][0]["ok"]
    sid = r["session"]["id"]
    fake.script = [{"content": "again"}]
    r2 = c.get("/v1/chat", params={"message": "more", "session": sid}).json()
    assert r2["session"]["id"] == sid and r2["session"]["messages"] == 5
    h = c.get("/v1/history", params={"session": sid}).json()
    assert [m["role"] for m in h["messages"]] == ["user", "assistant", "user", "assistant"] and h["messages"][0]["content"] == "find my inbox"
    assert c.get("/v1/chats").json()["chats"][0]["id"] == sid
    assert c.get("/v1/chat").status_code == 400
    assert c.get("/v1/history", params={"session": "nope"}).status_code == 404
    # POST + streaming
    fake.script = [{"content": "streamed answer"}]
    with c.stream("POST", "/v1/chat", json={"message": "stream it", "stream": True, "new": True}) as s:
        body = "".join(s.iter_text())
    assert "event: text" in body and "event: done" in body and "streamed answer" in body
    # tools
    assert any(t["name"] == "bash" for t in c.get("/v1/tools").json()["tools"])
    r = c.post("/v1/tools/bash", json={"command": "echo hi-from-api"}).json()
    assert r["ok"] and "hi-from-api" in r["result"]


def test_api_step_mode(jade, fake):
    c = TestClient(create_app(jade))
    fake.script = [{"content": "", "tool_calls": [("bash", {"command": "echo first"}), ("bash", {"command": "echo second"})]}, {"content": "all done"}]
    r = c.post("/v1/chats/new/step", json={"message": "two things"}).json()
    sid = r["session"]["id"]
    assert len(r["pending"]) == 2 and not r["done"]
    c.post(f"/v1/chats/{sid}/edit", json={"arguments": {"command": "echo changed"}})
    r = c.post(f"/v1/chats/{sid}/step").json()
    assert "changed" in [e for e in r["events"] if e["type"] == "tool_end"][0]["output"]
    r = c.post(f"/v1/chats/{sid}/skip", json={"reason": "no"}).json()
    assert r["pending"] == []
    r = c.post(f"/v1/chats/{sid}/step").json()
    assert r["done"] and r["reply"] == "all done"


def test_api_token(jade):
    jade.settings.api_token = "secret-token-123"
    c = TestClient(create_app(jade))
    assert c.get("/v1/status").status_code == 401
    assert c.get("/v1/status", headers={"Authorization": "Bearer secret-token-123"}).status_code == 200
    assert c.get("/health").status_code == 200


def test_cli(tmp_path, fake):
    env = {**os.environ, "JADE_CONFIG": str(tmp_path / "c.toml"), "JADE_HOST": fake.url, "JADE_MODEL": "fake:1b", "JADE_EMBED_ON": "false"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "jade", "--vault", str(tmp_path / "v"), *a], env=env, capture_output=True, text=True, timeout=60)
    r = run("tools")
    assert r.returncode == 0 and "notes_search" in r.stdout, r.stderr
    r = run("tool", "notes_search", "query=spaced")
    assert "/Inbox.md" in r.stdout, r.stderr
    fake.script = [{"content": "cli answer"}]
    r = run("ask", "-q", "hello", "there")
    assert r.stdout.strip() == "cli answer", r.stderr
    r = run("chats")
    assert "hello there" in r.stdout
    fake.script = [{"content": "json events"}]
    r = run("ask", "--json", "hi")
    assert any(json.loads(l)["type"] == "done" for l in r.stdout.splitlines())
