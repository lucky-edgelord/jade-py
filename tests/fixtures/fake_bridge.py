"""A fake Vector / Discord bridge (stdio MCP) for the tests. State lives in files in $FAKE_DIR:
inbox.json (messages to deliver), events.json, out.jsonl (everything Jade sent or did)."""
import json
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

D = Path(sys.argv[2] if len(sys.argv) > 2 else os.environ["FAKE_DIR"])
KIND = sys.argv[1] if len(sys.argv) > 1 else "vector"
s = MCPServer(f"fake-{KIND}")


def take(name):
    p = D / name
    if not p.exists():
        return []
    v = json.loads(p.read_text() or "[]")
    p.write_text("[]")
    return v


def out(op, **kw):
    with open(D / "out.jsonl", "a") as f:
        f.write(json.dumps({"op": op, **kw}) + "\n")


@s.tool()
def get_new_messages() -> str:
    return json.dumps(take("inbox.json"))


@s.tool()
def get_new_events() -> str:
    return json.dumps(take("events.json"))


@s.tool()
def send_message(content: str, chat_id: str = "", user_id: str = "", replied_to: str = "") -> str:
    out("send", chat_id=chat_id or ("dm:" + user_id), content=content, replied_to=replied_to)
    return json.dumps({"message_id": "m-out", "chat_id": chat_id or ("dm" + user_id)})


@s.tool()
def send_typing(chat_id: str = "", user_id: str = "") -> str:
    out("typing", chat_id=chat_id)
    return "ok"


@s.tool()
def react(message_id: str, emoji: str, chat_id: str = "", user_id: str = "") -> str:
    out("react", chat_id=chat_id, message_id=message_id, emoji=emoji)
    return "ok"


@s.tool()
def get_messages(chat_id: str = "", user_id: str = "", limit: int = 20) -> str:
    return json.dumps([{"id": "m1", "at": 1, "npub": "npub1alice", "author": {"name": "alice"}, "content": "earlier hello", "text": "earlier hello"}])


# vector
@s.tool()
def my_npub() -> str:
    return "npub1jadebot"


@s.tool()
def list_communities() -> str:
    return json.dumps([{"community_id": "c1", "name": "Lamps", "channels": [{"channel_id": "ch1", "name": "general"}]}])


@s.tool()
def get_profile(npub: str) -> str:
    return json.dumps({"display_name": {"npub1alice": "Alice", "npub1owner": "Boss"}.get(npub, "")})


@s.tool()
def load_profile(npub: str) -> str:
    return get_profile(npub)


@s.tool()
def list_pending_invites() -> str:
    return json.dumps([{"community_id": "c2", "name": "Moths", "inviter_npub": "npub1stranger"}])


@s.tool()
def accept_pending_invite(community_id: str) -> str:
    out("accept", community_id=community_id)
    return "joined"


# discord
@s.tool()
def status() -> str:
    return json.dumps({"ready": True, "user": {"id": "999", "username": "jadebot"}})


@s.tool()
def list_guilds() -> str:
    return json.dumps([{"id": "g1", "name": "Lamp Club"}])


@s.tool()
def list_channels(guild_id: str) -> str:
    return json.dumps([{"id": "500", "name": "general"}])


if __name__ == "__main__":
    s.run()
