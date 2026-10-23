"""The Jade object: a vault, a model, tools, memory, skills and chats, in one place.

    from jade import Jade
    jade = Jade("~/JadeVault")                      # settings from ~/.config/jade/config.toml and JADE_*
    print(jade.chat("What's in my inbox?"))         # one turn in a new chat, synchronous
    s = jade.session()                              # a chat you keep using
    answer = await s.ask("Summarise [[Project Neon]]")
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .context import Context, Router, more_tools_tool
from .memory import Memory
from .providers import Provider, ToolCall, make_provider
from .session import Session, SessionStore
from .settings import Settings
from .skills import Skills
from .tools import builtin
from .tools.registry import Registry, Tool
from .vault import Vault

Approver = Callable[[ToolCall, Tool | None], Awaitable[bool] | bool]


def tool_specs() -> dict[str, dict]:
    return json.loads((resources.files("jade") / "tools" / "specs.json").read_text())


class Jade:
    def __init__(self, vault: str | os.PathLike | None = None, settings: Settings | None = None, *, provider: Provider | None = None,
                 seed: bool = True, config: str | os.PathLike | None = None, **overrides: Any):
        from .settings import CONFIG_PATH
        self.config_path = Path(config) if config else CONFIG_PATH
        if settings is None:
            settings = Settings.load(config, **overrides)
        elif overrides:
            settings = settings.model_copy(update=overrides)
        if vault is not None:
            settings.vault = Path(os.path.expanduser(str(vault))).resolve()
        self.settings = settings
        self.vault = Vault(settings.vault, seed=seed)
        self.memory = Memory(self.vault)
        self.memory.ensure()
        self.skills = Skills(self.vault)
        self.provider = provider or make_provider(settings)
        self.tools = Registry()
        self.specs = tool_specs()
        builtin.register(self.tools, self.specs)
        self.tools.add(more_tools_tool(self))
        self.router = Router(self)
        self.context = Context(self)
        self.sessions = SessionStore(settings.data_path / "chats")
        self.approver: Approver | None = None
        self.prompt_extras: list = []
        self.notifiers: list[Callable[[str, str], Any]] = []   # notify(title, body) goes to each (the API streams them; the CLI prints)
        self.daemon_loops: list = []                           # extra background loops parts add (curiosity, backups…)
        self.daemon_running = False
        self.prepare_hooks: list = []                          # async callables run before each model call and tool run (MCP connects here)
        self.close_hooks: list = []                            # async callables run by aclose()
        self.background = asyncio.Lock()                       # background turns (schedules, heartbeat) run one at a time
        self.state = self._load_state()
        self._extensions_loaded = False
        self._load_extensions()

    def _load_extensions(self) -> None:
        """Optional parts register their tools here (each one checks what it needs)."""
        from . import extensions
        extensions.load(self)
        self._extensions_loaded = True

    # ---------------------------------------------------------------- chats
    def session(self, id: str | None = None) -> Session:   # noqa: A002
        """A chat: a new one, or the saved one with this id."""
        return self.sessions.load(self, id) if id else Session(self)

    def chats(self) -> list[dict]:
        return self.sessions.list()

    async def achat(self, text: str, session: Session | str | None = None, **kw) -> str:
        s = session if isinstance(session, Session) else self.session(session)
        return await s.ask(text, **kw)

    def chat(self, text: str, session: Session | str | None = None, **kw) -> str:
        """One turn, synchronously (from scripts and the REPL; inside async code use achat)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.achat(text, session, **kw))
        raise RuntimeError("Jade.chat() was called inside a running event loop: use await jade.achat(...)")

    # ---------------------------------------------------------------- tools
    def tool(self, name: str | None = None, **kw):
        """Decorator: give Jade a tool.

            @jade.tool()
            def weather(city: str) -> str:
                "Current weather for a city."
                ...
        """
        return self.tools.tool(name, **kw)

    async def run_tool(self, name: str, args: dict | None = None, session: Session | None = None) -> str:
        from .tools.registry import ToolContext
        if name.startswith("mcp__") or name not in self.tools:
            await self.prepare()
        r = await self.tools.call(name, args or {}, ToolContext(self, session))
        if r.error:
            raise RuntimeError(r.output.removeprefix("Error: "))
        return r.output

    async def ask_approval(self, call: ToolCall, tool: Tool | None) -> bool:
        if not self.approver:
            return False
        r = self.approver(call, tool)
        return bool(await r) if asyncio.iscoroutine(r) else bool(r)

    # ---------------------------------------------------------------- notifications and state
    def notify(self, title: str, body: str = "") -> None:
        """Tell the user something (schedules, the heartbeat, tools). Goes to every notifier; logged in /Jade/Logs/notifications.log."""
        try:
            self.vault.append("/Jade/Logs/notifications.log", f"[{self.now().strftime('%Y-%m-%d %H:%M')}] {title}{': ' + body if body else ''}\n")
        except Exception:
            pass
        for f in list(self.notifiers):
            try:
                r = f(title, body)
                if asyncio.iscoroutine(r):
                    asyncio.get_running_loop().create_task(r)
            except Exception:
                pass

    def _load_state(self) -> dict:
        try:
            return json.loads((self.settings.data_path / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self) -> None:
        p = self.settings.data_path / "state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(p)

    # ---------------------------------------------------------------- helpers used by the loop and tools
    def now(self) -> datetime:
        tz = None
        if self.settings.timezone:
            try:
                tz = ZoneInfo(self.settings.timezone)
            except Exception:
                tz = None
        return datetime.now(tz).astimezone(tz) if tz else datetime.now().astimezone()

    async def memory_search(self, query: str) -> str:
        emb = getattr(self, "embeddings", None)
        if emb is not None and self.settings.embed_on:
            try:
                r = await emb.memory_search(query)
                if r:
                    return r
            except Exception:
                pass
        return self.memory.search_text(query)

    def _memory_changed(self) -> None:
        emb = getattr(self, "embeddings", None)
        if emb is not None:
            emb.mark_dirty()

    def _after_turn(self, s: Session) -> None:
        for f in getattr(self, "_turn_hooks", []):
            try:
                f(s)
            except Exception:
                pass

    def _journal(self, s: Session, msgs: list[dict]) -> None:
        j = getattr(self, "journal", None)
        if j is not None:
            j.later(s, msgs)

    async def prepare(self) -> None:
        for h in list(self.prepare_hooks):
            try:
                await h()
            except Exception as e:
                import logging
                logging.getLogger("jade").warning("prepare: %s", e)

    async def aclose(self) -> None:
        for h in list(self.close_hooks):
            try:
                await h()
            except Exception:
                pass
        if getattr(self, "flows", None):
            await self.flows.aclose()
        await self.provider.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.aclose()

    def __repr__(self) -> str:
        return f"<Jade vault={self.vault.root} model={self.settings.provider}:{self.settings.model}>"
