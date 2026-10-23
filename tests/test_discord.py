import json
import sys
from pathlib import Path

from test_vector import FAKE, drain, outs


async def test_discord_turns(jade, fake, tmp_path):
    d = tmp_path / "fake"
    d.mkdir()
    c = jade.settings.discord
    c.on, c.command, c.args, c.owner, c.channels = True, sys.executable, [FAKE, "discord", str(d)], "111", ["#general"]
    jade.settings.vector.on = True
    D = jade.bridges["discord"]
    try:
        assert await D.connect(), D.error
        assert D.user["id"] == "999" and D.chans["500"]["label"] == "#general · Lamp Club"
        (d / "inbox.json").write_text(json.dumps([
            {"id": "d1", "chat_id": "500", "guild_id": "g1", "is_group": True, "author": {"id": "222", "name": "bob"}, "text": "anyone around?"},
            {"id": "d2", "chat_id": "500", "guild_id": "g1", "is_group": True, "author": {"id": "222", "name": "bob"}, "text": "hello", "mentions_me": True},
            {"id": "d3", "chat_id": "500", "guild_id": "g1", "is_group": True, "author": {"id": "999", "name": "jadebot"}, "text": "my own echo jade"},
        ]))
        fake.script = [{"tool_calls": [("notify_owner", {"message": "Bob says hello", "urgent": True})]}, {"content": "Hi Bob!"}]
        c.notify = "urgent"
        seen = []
        jade.notifiers.append(lambda t, b: seen.append(t))
        await D.poll_once()
        await drain(D)
        sent = [o for o in outs(d) if o["op"] == "send"]
        assert {"op": "send", "chat_id": "dm:111", "content": "⚡ bob: Bob says hello", "replied_to": ""} in sent       # urgent: DM'd to the owner at once
        assert {"op": "send", "chat_id": "500", "content": "Hi Bob!", "replied_to": "d2"} in sent and len(sent) == 2
        assert "⚡ Jade (Discord)" in seen
        assert "Bob says hello" in jade.vault.read("/Jade/Discord Inbox.md") and not jade.vault.exists("/Jade/Vector Inbox.md")
        p = fake.requests[0]["messages"][0]["content"]
        assert "You are on Discord as the bot account jadebot (999)" in p
        assert {t["function"]["name"] for t in fake.requests[0]["tools"]} <= {"notify_owner", "web_search", "discord"}
    finally:
        await jade.aclose()
