"""The trajectory log: runs as the model saw them, 👍/👎 ratings, retry, and the app's export formats (with ratings
in a metadata field, and a filter that keeps 👎 runs, for DPO / KTO)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from jade import traj as T
from jade.api import create_app


def lines(text):
    return [json.loads(x) for x in text.splitlines() if x.strip()]


async def test_a_run_is_logged_as_the_model_saw_it(jade, fake):
    fake.script = [{"content": "", "tool_calls": [("notes_search", {"query": "inbox"})]}, {"content": "Your inbox has 2 items."}]
    s = jade.session()
    assert await s.ask("what is in my inbox") == "Your inbox has 2 items."
    runs = jade.traj.all()
    assert len(runs) == 1
    r = runs[0]
    assert r["outcome"] == "ok" and r["source"] == "chat" and r["session"] == s.id and r["model"] == "fake:1b" and r["rating"] is None
    assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant", "tool", "assistant"]
    assert r["messages"][2]["tool_calls"] == [{"function": {"name": "notes_search", "arguments": {"query": "inbox"}}}]
    assert r["messages"][3]["tool_name"] == "notes_search" and r["messages"][-1]["content"] == "Your inbox has 2 items."
    assert len(r["steps"]) == 2 and r["steps"][0]["prompt_tokens"] == 100 and r["steps"][0]["completion_tokens"] == 10
    assert any(t["name"] == "notes_search" for t in r["tools"]) and "parameters" in r["tools"][0]
    # the system message is exactly what the model got
    assert r["messages"][0]["content"] == fake.requests[0]["messages"][0]["content"]
    assert s.messages[-1]["traj"] == r["id"]


async def test_ratings_retry_and_preference_export(jade, fake):
    s = jade.session()
    fake.script = [{"content": "a weak answer"}]
    await s.ask("rate me")
    T.rate_message(s, None, -1)
    fake.script = [{"content": "a much better answer"}]
    evs = [ev async for ev in s.retry()]
    assert evs[-1].type == "done" and evs[-1].text == "a much better answer"
    assert [m.get("display") or m["content"] for m in s.messages if m["role"] in ("user", "assistant")] == ["rate me", "a much better answer"]
    T.rate_message(s, None, 1)
    assert s.messages[-1]["rating"] == 1 and jade.session(s.id).messages[-1]["rating"] == 1   # saved with the chat
    runs = jade.traj.all()
    assert [r["rating"] for r in runs] == [-1, 1]

    pref = lines(jade.traj.export("openai", "prefs", meta=True))
    assert len(pref) == 2 and sorted(o["metadata"]["rating"] for o in pref) == [-1, 1]
    prompt = lambda o: [(m["role"], m["content"]) for m in o["messages"][:-1]]  # noqa: E731
    assert prompt(pref[0]) == prompt(pref[1])           # the same prompt: a DPO pair
    md = pref[0]["metadata"]
    assert set(md) == {"id", "started", "model", "provider", "source", "outcome", "rating", "steps"} and md["started"].endswith("Z") and md["outcome"] == "ok"
    # the other filters
    assert [o["messages"][-1]["content"] for o in lines(jade.traj.export(filter="clean"))] == ["a much better answer"]
    assert len(lines(jade.traj.export(filter="good"))) == 1
    assert "metadata" not in lines(jade.traj.export(filter="all"))[0]   # ratings only when asked for
    # settings give the defaults
    jade.settings.traj_meta, jade.settings.traj_filter = True, "prefs"
    assert len(lines(jade.traj.export())) == 2 and "metadata" in lines(jade.traj.export())[0]
    # clearing a rating
    T.rate_message(s, None, None)
    assert "rating" not in s.messages[-1] and jade.traj.all()[-1]["rating"] is None


async def test_formats_match_the_app(jade, fake):
    fake.script = [{"content": "", "tool_calls": [("notes_search", {"query": "a"}), ("note_links", {"path": "/Inbox.md"})], "thinking": "hmm"},
                   {"content": "done", "thinking": "ok then"}]
    await jade.session().ask("two tools")
    o = lines(jade.traj.export("openai", "all", thinking=True))[0]
    m = o["messages"]
    assert [x["role"] for x in m] == ["system", "user", "assistant", "tool", "tool", "assistant"]
    ids = [c["id"] for c in m[2]["tool_calls"]]
    assert [x["tool_call_id"] for x in m[3:5]] == ids and all(c["type"] == "function" and isinstance(c["function"]["arguments"], str) for c in m[2]["tool_calls"])
    assert m[2]["content"] is None and m[2]["reasoning_content"] == "hmm" and m[-1]["reasoning_content"] == "ok then"
    assert o["tools"][0]["type"] == "function"
    g = lines(jade.traj.export("sharegpt", "all", thinking=True))[0]
    assert [c["from"] for c in g["conversations"]] == ["human", "gpt", "function_call", "function_call", "observation", "gpt"]
    assert g["conversations"][1]["value"] == "<think>\nhmm\n</think>\n\n" and g["system"] and json.loads(g["tools"])
    assert json.loads(g["conversations"][2]["value"]) == {"name": "notes_search", "arguments": {"query": "a"}}
    assert g["conversations"][4]["value"].count("\n\n") >= 1   # two observations merged, as LLaMA-Factory wants
    assert "reasoning_content" not in json.dumps(lines(jade.traj.export("openai", "all"))[0])


async def test_errors_stopped_and_off(jade, fake):
    fake.script = [{"status": 500, "error": "model exploded"}]
    [ev async for ev in jade.session().run("boom")]
    r = jade.traj.all()[-1]
    assert r["outcome"] == "error" and "model exploded" in r["error"] and not r["steps"]
    assert jade.traj.export(filter="prefs") == "" and len(lines(jade.traj.export(filter="all"))) == 1
    jade.settings.traj_on = False
    await jade.session().ask("not logged")
    assert jade.traj.count() == 1
    st = jade.traj.stats()
    assert st["runs"] == 1 and st["errors"] == 1
    assert jade.traj.clear() == 1 and jade.traj.count() == 0


async def test_background_runs_are_labelled(jade, fake):
    s = jade.session()
    s.title, s.meta["heartbeat"] = "♥ Heartbeat", True
    await s.ask("check")
    s2 = jade.session()
    s2.title = "⏰ morning"
    await s2.ask("go")
    assert [r["source"] for r in jade.traj.all()] == ["heartbeat", "schedule"]


def test_api_rate_retry_export(jade, fake):
    c = TestClient(create_app(jade))
    fake.script = [{"content": "first try"}]
    sid = c.post("/v1/chat", json={"message": "pair me", "session": "new"}).json()["session"]["id"]
    assert c.post(f"/v1/chats/{sid}/rate", json={"rating": "down"}).json()["rating"] == -1
    fake.script = [{"content": "second try"}]
    r = c.post(f"/v1/chats/{sid}/retry", json={}).json()
    assert r["ok"] and r["reply"] == "second try"
    msgs = c.get(f"/v1/chats/{sid}").json()["chat"]["messages"]
    i = max(k for k, m in enumerate(msgs) if m["role"] == "assistant")
    assert c.post(f"/v1/chats/{sid}/rate", json={"index": i, "rating": 1}).json()["trajectory"]
    assert c.post(f"/v1/chats/{sid}/rate", json={"index": 0, "rating": 1}).status_code == 400   # not a reply
    st = c.get("/v1/trajectories").json()
    assert st["runs"] == 2 and st["good"] == 1 and st["bad"] == 1
    e = c.get("/v1/trajectories/export", params={"filter": "prefs", "meta": "1"})
    assert e.status_code == 200 and "attachment" in e.headers["content-disposition"] and sorted(o["metadata"]["rating"] for o in lines(e.text)) == [-1, 1]
    assert c.get("/v1/trajectories/export", params={"format": "nope"}).status_code == 400
    # streaming retry
    fake.script = [{"content": "third try"}]
    with c.stream("POST", f"/v1/chats/{sid}/retry", json={"stream": True}) as s:
        body = "".join(s.iter_text())
    assert "event: done" in body and "third try" in body
    assert c.delete("/v1/trajectories").json()["deleted"] == 3


def test_cli_traj(tmp_path, fake):
    env = {**os.environ, "JADE_CONFIG": str(tmp_path / "c.toml"), "JADE_HOST": fake.url, "JADE_MODEL": "fake:1b", "JADE_EMBED_ON": "false"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "jade", "--vault", str(tmp_path / "v"), *a], env=env, capture_output=True, text=True, timeout=60)  # noqa: E731
    fake.script = [{"content": "logged"}]
    assert run("ask", "-q", "hi").returncode == 0
    r = run("traj", "stats")
    assert r.returncode == 0 and r.stdout.startswith("1 runs"), r.stdout + r.stderr
    out = tmp_path / "t.jsonl"
    r = run("traj", "export", "--filter", "prefs", "--meta", "--format", "sharegpt", "-o", str(out))
    assert r.returncode == 0 and "Exported 1 of 1" in r.stderr
    o = lines(out.read_text())[0]
    assert o["conversations"][-1] == {"from": "gpt", "value": "logged"} and o["metadata"]["rating"] is None
    assert run("traj", "export", "--filter", "bogus").returncode == 2
    assert run("traj", "clear", "--yes").stdout.strip() == "Deleted 1 runs."
