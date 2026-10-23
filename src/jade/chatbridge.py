"""What the Vector and Discord bridges share, as in the app: the bridge is an MCP server (HTTP with a bearer token,
or a local command) that the messenger's bot runs behind. Jade polls it for new messages, decides who gets an
answer (owner / admins / everyone, a channel whitelist, trigger words), runs one turn per chat in that chat's own
session with tools by role, and sends her answer back as a reply with a typing indicator.

  owner  = everything Jade can do;
  admin  = messenger powers (message anyone, moderate, join/leave), no vault, shell or web beyond search;
  public = talk in that chat, react, read it, pass messages to the owner (notify_owner).

Only one bridge turn runs at a time (they share jade.background with schedules and the heartbeat).
Settings: [vector] and [discord] in the config (see settings.BridgeSettings)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade
    from .session import Session

log = logging.getLogger("jade")
SCOPE_TOOLS = {"public": {"notify_owner", "web_search"}, "admin": {"notify_owner", "web_search"}}


def jload(t, d=None):
    try:
        return json.loads(t)
    except (TypeError, ValueError):
        return d


def word_hit(words: list[str], text: str) -> bool:
    t = str(text or "").lower()
    return any(re.search(r"(^|[^\w])" + re.escape(w.lower()) + r"($|[^\w])", t) for w in words if w)


class Bridge:
    kind = "bridge"          # "vector" / "discord"
    label = "Bridge"
    icon = "💬"
    inbox_path = "/Jade/Bridge Inbox.md"
    actions: dict[str, set] = {}

    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.server = None        # an mcpx.Server
        self.connected = False
        self.error = ""
        self.q: list[dict] = []
        self.names: dict[str, str] = {}
        self.chans: dict[str, dict] = {}
        self.sent_to: set | None = None
        self.worker: asyncio.Task | None = None
        self.polling = False
        self.log: list[dict] = []
        self.loop = None

    # ---- settings and identity
    @property
    def cfg(self):
        return getattr(self.jade.settings, self.kind)

    def role(self, who) -> str:
        if not who:
            return "public"
        if self.cfg.owner and str(who) == self.cfg.owner.strip():
            return "owner"
        if str(who) in self.cfg.admins:
            return "admin"
        return "public"

    def name(self, who) -> str:
        if not who:
            return "?"
        if who in self.names:
            return self.names[who]
        if who in self.chans:
            return self.chans[who]["label"]
        if str(who) == self.cfg.owner.strip():
            return self.cfg.owner_name or "Owner"
        return self.short(str(who))

    @staticmethod
    def short(x: str) -> str:
        return x

    # ---- the connection
    async def connect(self) -> bool:
        from .mcpx import Server
        loop = asyncio.get_running_loop()
        if self.server and self.server.state == "connected" and loop is self.loop:
            return True
        await self.stop()
        c = self.cfg
        if not c.url and not c.command:
            self.error = "no bridge url"
            return False
        scfg: dict[str, Any] = {"timeout": 90}
        if c.command:
            scfg.update(command=c.command, args=list(c.args))
        else:
            scfg["url"] = c.url.strip()
            if c.token:
                scfg["headers"] = {"Authorization": "Bearer " + c.token.strip()}
        if not getattr(self.jade, "mcp", None):
            self.error = "the MCP part is not available"
            return False
        self.server = Server(self.jade.mcp, self.kind, scfg)
        await self.server.start(30)
        self.loop = loop
        if self.server.state != "connected":
            self.error = self.server.error or "could not connect"
            self.server = None
            return False
        try:
            await self.on_connect()
        except Exception as e:
            self.error = str(e)
            await self.stop()
            return False
        self.connected, self.error = True, ""
        return True

    async def stop(self):
        if self.server:
            await self.server.stop()
        self.server, self.connected = None, False

    async def call(self, name: str, args: dict | None = None, timeout: float = 90) -> str:
        if not self.server or self.server.state != "connected":
            if not await self.connect():
                raise ToolError(f"{self.label} is not connected: {self.error}")
        r = await asyncio.wait_for(self.server.client.call_tool(name, args or {}), timeout)
        d = r.model_dump(by_alias=True, exclude_none=True, mode="json")
        text = "\n".join(c.get("text", "") for c in d.get("content") or [] if c.get("type") == "text")
        if d.get("isError"):
            raise ToolError(text or f"{name} failed")
        return text

    async def on_connect(self):
        pass

    # ---- who gets an answer
    def channel_allowed(self, chat_id: str, group_id: str = "") -> bool:
        allow = self.cfg.channels
        if not allow:
            return False
        ch = self.chans.get(chat_id) or {}
        low = [a.lower() for a in allow]
        return bool(chat_id in allow or (group_id and group_id in allow) or (ch.get("group_id") in allow) or \
            ("#" + str(ch.get("name", "")).lower() in low) or (str(ch.get("group", "")).lower() in low))

    def triggered(self, m: dict) -> bool:
        return self.cfg.trigger == "all" or word_hit(self.cfg.words or ["jade"], self.text_of(m))

    def text_of(self, m: dict) -> str:
        return str(m.get("content") or m.get("text") or "")

    def incoming(self, chat_id: str, sender: str, is_group: bool, m: dict, group_id: str = "", mentioned: bool = False) -> None:
        role = self.role(sender)
        self.log.append({"at": time.time(), "chat": chat_id, "from": sender, "text": self.text_of(m)[:200]})
        del self.log[:-200]
        if not is_group:
            pol = self.cfg.dm
            answer = pol == "anyone" or role == "owner" or (pol == "known" and role == "admin")
            if not answer:
                if self.cfg.inbox_unanswered:
                    self.inbox_add(sender, chat_id, self.text_of(m) or ("[file]" if m.get("attachments") else ""), "not answered: DM policy")
                return
        elif not ((role == "owner" or self.channel_allowed(chat_id, group_id)) and (mentioned or self.triggered(m))):
            return
        self.enqueue({"kind": "msg", "chat_id": chat_id, "group_id": group_id, "is_group": is_group, "sender": sender, "role": role, "msgs": [m]})

    def enqueue(self, item: dict) -> None:
        last = self.q[-1] if self.q else None
        if last and item["kind"] == "msg" and last["kind"] == "msg" and last["chat_id"] == item["chat_id"] and last["sender"] == item["sender"]:
            last["msgs"] += item["msgs"]
        elif last and item["kind"] == "xdc" and last["kind"] == "xdc" and last.get("topic") == item.get("topic"):
            last["frames"] += item["frames"]
        else:
            self.q.append(item)
        if not self.worker or self.worker.done():
            self.worker = asyncio.get_running_loop().create_task(self.pump())

    async def pump(self):
        while self.q:
            item = self.q.pop(0)
            try:
                async with self.jade.background:
                    await self.wake(item)
            except Exception as e:
                log.warning("%s turn failed: %s", self.kind, e)

    # ---- secretary
    def inbox_add(self, sender: str, chat: str, text: str, note: str = "", urgent: bool = False) -> str:
        v = self.jade.vault
        if not v.is_file(self.inbox_path):
            v.write(self.inbox_path, f"# {self.label} Inbox\n\nMessages people asked Jade to pass on, and messages she did not answer. Tick them off when handled. #{self.kind}\n\n")
        when = self.jade.now().strftime("%a %b %-d, %-I:%M %p")
        where = self.where_label(chat, sender)
        who = f"{self.name(sender)} ({sender})" if sender else self.label
        body = re.sub(r"\s+", " ", str(text or "")).strip()[:600]
        line = f"- [ ] **{when}** · {who} in {where}{' · ⚡ urgent' if urgent else ''}{f' · _{note}_' if note else ''}: {body}"
        v.write(self.inbox_path, v.read(self.inbox_path).rstrip("\n") + "\n" + line + "\n")
        return line

    def where_label(self, chat: str, sender: str) -> str:
        return self.chans[chat]["label"] if chat in self.chans else "DM"

    async def tell_owner(self, text: str, urgent: bool, here: dict | None) -> bool:
        self.jade.notify(f"{'⚡ ' if urgent else ''}Jade ({self.label})", text[:200])
        return False

    async def notify_owner(self, session, message: str, urgent: bool) -> str:
        msg = str(message or "").strip()
        if not msg:
            raise ToolError("notify_owner needs message")
        here = (session.meta.get("bridge") if session else None) or {}
        sender, chat = here.get("sender", ""), here.get("chat_id", "")
        mode = self.cfg.notify
        now = mode == "now" or (mode == "urgent" and urgent)
        self.inbox_add(sender, chat, msg, "also sent at once" if now else "", urgent)
        if now:
            dm = await self.tell_owner(f"{self.name(sender) + ': ' if sender else ''}{msg}", urgent, here)
            return f"Told the owner now{f' ({self.label} DM + notification)' if dm else ' (notification)'} and filed it in the inbox."
        return f"Filed in the owner's inbox ({self.inbox_path}); they will see it next time they check."

    # ---- a turn
    async def describe(self, item: dict) -> tuple[str, list[str]]:
        raise NotImplementedError

    async def send_reply(self, item: dict, text: str) -> None:
        raise NotImplementedError

    async def typing(self, chat_id: str) -> None:
        pass

    def scoped_prompt(self, jade, session) -> str:
        raise NotImplementedError

    def session_for(self, item: dict) -> "Session":
        j = self.jade
        key = f"{self.kind}:{item['chat_id']}"
        sid = j.state.get("bridge_sessions", {}).get(key)
        s = None
        if sid:
            try:
                s = j.session(sid)
            except (KeyError, ValueError, OSError):
                s = None
        if s is None:
            s = j.session()
            s.title = f"{self.icon} {self.chans[item['chat_id']]['label'] if item['is_group'] and item['chat_id'] in self.chans else self.name(item['chat_id'] if not item['is_group'] else item['sender'])}"
            j.state.setdefault("bridge_sessions", {})[key] = s.id
            j.save_state()
        return s

    async def wake(self, item: dict) -> None:
        s = self.session_for(item)
        s.meta["bridge"] = {"kind": self.kind, "chat_id": item["chat_id"], "is_group": item["is_group"], "sender": item["sender"], "group_id": item.get("group_id", ""), "role": item["role"]}
        scoped = item["role"] != "owner"
        s.allow_tools = (SCOPE_TOOLS[item["role"]] | {self.kind}) if scoped else None
        s.prompt_override = self.scoped_prompt if scoped else None
        self.sent_to = set()
        tick = None
        if item["kind"] == "msg":
            await self.typing(item["chat_id"])

            async def keep_typing():
                while True:
                    await asyncio.sleep(7)
                    await self.typing(item["chat_id"])
            tick = asyncio.get_running_loop().create_task(keep_typing())
        try:
            content, images = await self.describe(item)
            before = len(s.messages)
            text, err = "", ""
            try:
                text = await s.ask(content, images or None)
            except Exception as e:
                err = str(e)
            for m in s.messages[before:]:
                if m.get("role") == "user" and m.get("content", "").endswith(content):
                    m["display"] = content.split("\n(Your plain-text")[0]
            s.save()
            text = re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()
            if item["kind"] == "msg":
                if text and not re.match(r"^NO_REPLY\b", text, re.I) and item["chat_id"] not in self.sent_to:
                    text = re.sub(r"\bNO_REPLY\b", "", text).strip()
                    if text:
                        try:
                            await self.send_reply(item, text)
                        except Exception as e:
                            log.warning("%s: could not send the reply: %s", self.kind, e)
                elif err and item["role"] == "owner":
                    await self.send_reply(item, f"⚠ {err[:300]}")
        finally:
            if tick:
                tick.cancel()
            self.sent_to = None
            s.allow_tools = None
            s.prompt_override = None

    def mark_sent(self, chat_id) -> None:
        if self.sent_to is not None and chat_id:
            self.sent_to.add(str(chat_id))

    # ---- polling
    async def poll_once(self) -> None:
        raise NotImplementedError

    async def loop_(self, daemon) -> None:
        while not daemon.stopping.is_set():
            if self.cfg.on:
                try:
                    if (self.server and self.server.state == "connected") or await self.connect():
                        await self.poll_once()
                except Exception as e:
                    self.error = str(e)
                    log.warning("%s: %s", self.kind, e)
                    if re.search(r"401|token", str(e), re.I):
                        await self.stop()
            if await daemon._sleep(max(1, float(self.cfg.poll or 5))):
                break
        await self.stop()

    # ---- the tool
    def scope_of(self, ctx) -> str | None:
        b = (ctx.session.meta.get("bridge") if ctx.session else None) or {}
        return b.get("role") if b.get("kind") == self.kind and b.get("role") != "owner" else None

    def here(self, ctx) -> str | None:
        b = (ctx.session.meta.get("bridge") if ctx.session else None) or {}
        return b.get("chat_id") if b.get("kind") == self.kind else None


def notify_owner_spec() -> dict:
    return {"description": "Secretary: pass a message on to your owner. It goes into their inbox note for the messenger the person wrote from, and, depending on their setting, "
                           "also straight to them. Use whenever someone asks you to tell the owner something, or something needs their attention.",
            "parameters": {"type": "object", "properties": {"message": {"type": "string", "description": "Clear summary: who, what they want, any deadline or contact detail"},
                                                            "urgent": {"type": "boolean", "description": "Only for things that cannot wait"}}, "required": ["message"]}}


async def notify_owner_tool(ctx, message: str = "", urgent: bool = False, text: str = "", **kw):
    j = ctx.jade
    b = (ctx.session.meta.get("bridge") if ctx.session else None) or {}
    br = getattr(j, "bridges", {}).get(b.get("kind")) or next(iter(getattr(j, "bridges", {}).values()), None)
    if not br:
        raise ToolError("no messenger bridge is set up")
    return await br.notify_owner(ctx.session, message or text, bool(urgent))


def register_bridge(jade: "Jade", br: Bridge) -> None:
    if not hasattr(jade, "bridges"):
        jade.bridges = {}
        spec = notify_owner_spec()
        jade.tools.add(Tool("notify_owner", spec["description"], spec["parameters"], notify_owner_tool, core=False,
                            enabled=lambda j: any(b.cfg.on for b in j.bridges.values()), hints="tell owner pass on message notify secretary"))
    jade.bridges[br.kind] = br
    jade.daemon_loops.append(lambda daemon: br.loop_(daemon))
    jade.close_hooks.append(br.stop)
