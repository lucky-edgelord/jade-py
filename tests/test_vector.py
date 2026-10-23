import asyncio
import json
import sys
from pathlib import Path

FAKE = str(Path(__file__).parent / "fixtures" / "fake_bridge.py")


def outs(d):
    p = d / "out.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


async def drain(br):
    for _ in range(200):
        if not br.q and (not br.worker or br.worker.done()):
            return
        await asyncio.sleep(0.05)


async def test_vector_turns_scopes_and_inbox(jade, fake, tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_DIR", str(d))
    v = jade.settings.vector
    v.on, v.command, v.args, v.owner, v.owner_name, v.channels, v.dm = True, sys.executable, [FAKE, "vector", str(d)], "npub1owner", "Boss", ["#general"], "anyone"
    jade.settings.approve = "auto"
    V = jade.bridges["vector"]
    try:
        assert await V.connect(), V.error
        assert V.npub == "npub1jadebot" and V.chans["ch1"]["label"] == "#general · Lamps"
        (d / "inbox.json").write_text(json.dumps([
            {"id": "a1", "chat_id": "npub1alice", "npub": "npub1alice", "is_group": False, "content": "Hi! Please tell your owner I called."},
            {"id": "g1", "chat_id": "ch1", "npub": "npub1bob", "is_group": True, "content": "random chatter"},
            {"id": "g2", "chat_id": "ch1", "npub": "npub1bob", "is_group": True, "content": "hey Jade, what's up?"},
        ]))
        (d / "events.json").write_text(json.dumps([{"type": "invite", "community_id": "c2"}]))
        fake.script = [
            {"tool_calls": [("notify_owner", {"message": "Alice called and wants a callback"}), ("read", {"path": "/Inbox.md"})]},
            {"content": "Done, I passed it on!"},
            {"content": "Not much, just shining."},
        ]
        await V.poll_once()
        await drain(V)
        sent = [o for o in outs(d) if o["op"] == "send"]
        assert sent[0] == {"op": "send", "chat_id": "npub1alice", "content": "Done, I passed it on!", "replied_to": "a1"}
        assert sent[1]["chat_id"] == "ch1" and sent[1]["replied_to"] == "g2" and len(sent) == 2      # "random chatter" was not addressed to her
        assert any(o["op"] == "typing" for o in outs(d))
        # a public chat: the scoped prompt (no notes, no memory) and only the scoped tools
        r0 = fake.requests[0]
        assert "You are on Vector messenger" in r0["messages"][0]["content"] and "Long-term memory" not in r0["messages"][0]["content"]
        assert {t["function"]["name"] for t in r0["tools"]} <= {"notify_owner", "web_search", "vector"}
        assert "Alice (npub1alice) · not your owner" in r0["messages"][-1]["content"]
        s = jade.session(jade.state["bridge_sessions"]["vector:npub1alice"])
        assert any("not available right now" in str(m.get("content")) for m in s.messages if m.get("role") == "tool")   # read was refused
        inbox = jade.vault.read("/Jade/Vector Inbox.md")
        assert "Alice (npub1alice)" in inbox and "Alice called and wants a callback" in inbox
        assert "Invite to Moths" in inbox and not any(o["op"] == "accept" for o in outs(d))   # a stranger's invite waits for the owner
        # the owner gets everything, with the normal prompt
        (d / "inbox.json").write_text(json.dumps([{"id": "o1", "chat_id": "npub1owner", "npub": "npub1owner", "is_group": False, "content": "what's in my inbox note?"}]))
        fake.script = [{"tool_calls": [("vector", {"action": "react", "message_id": "o1", "emoji": "👀"})]}, {"content": "NO_REPLY"}]
        n_before = len([o for o in outs(d) if o["op"] == "send"])
        await V.poll_once()
        await drain(V)
        last = fake.requests[-2]
        assert "Long-term memory" in last["messages"][0]["content"] and "write" in {t["function"]["name"] for t in last["tools"]}
        assert any(o["op"] == "react" and o["emoji"] == "👀" for o in outs(d))
        assert len([o for o in outs(d) if o["op"] == "send"]) == n_before          # NO_REPLY stays silent
        # DM policy: owner only
        v.dm = "owner"
        (d / "inbox.json").write_text(json.dumps([{"id": "x1", "chat_id": "npub1eve", "npub": "npub1eve", "is_group": False, "content": "let me in"}]))
        await V.poll_once()
        await drain(V)
        assert "not answered: DM policy" in jade.vault.read("/Jade/Vector Inbox.md")
    finally:
        await jade.aclose()
