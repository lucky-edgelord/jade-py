"""A chat: messages, tasks and the agent loop. Run a whole turn (async for ev in session.run(text)), get just the
answer (await session.ask(text)), or go one move at a time with a Stepper (a model call, then each tool call)."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Callable

from . import events as E
from . import prompt as P
from .providers import ProviderError, ToolCall
from .tools.registry import ToolContext

if TYPE_CHECKING:
    from .core import Jade


def estimate_tokens(x) -> int:
    if isinstance(x, list):
        return sum(estimate_tokens(m) for m in x)
    if isinstance(x, dict):
        n = len(str(x.get("content") or "")) + len(json.dumps(x.get("tool_calls") or [])) if "role" in x else len(json.dumps(x))
        return n // 3 + 4
    return len(str(x)) // 3


@dataclass
class Session:
    jade: "Jade"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "New chat"
    messages: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    task_seq: int = 0
    summary: str = ""
    tools_loaded: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)
    _cancel: bool = field(default=False, repr=False)
    _busy: bool = field(default=False, repr=False)
    allow_tools: set | None = field(default=None, repr=False)   # only these tools this run (curiosity wake-ups); not saved
    max_steps: int | None = field(default=None, repr=False)     # per-chat limit below settings.max_steps; not saved

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "title", "messages", "tasks", "task_seq", "summary", "tools_loaded", "created", "updated", "meta")}

    @classmethod
    def from_dict(cls, jade: "Jade", d: dict) -> "Session":
        s = cls(jade)
        for k, v in d.items():
            if hasattr(s, k) and not k.startswith("_") and k != "jade":
                setattr(s, k, v)
        return s

    def save(self) -> None:
        self.updated = time.time()
        self.jade.sessions.save(self)

    # ---------------------------------------------------------------- running
    def cancel(self) -> None:
        """Stop the running turn after the current step (a running tool is interrupted)."""
        self._cancel = True

    def add_user(self, text: str, images: list[str] | None = None) -> dict:
        m = {"role": "user", "content": f"{P.stamp(self.jade.now())} {text}", "display": text, "ts": time.time()}
        if images:
            m["images"] = images
        self.messages.append(m)
        if self.title == "New chat":
            self.title = P.title_from(text)
        return m

    async def close(self) -> None:
        """Leave the chat: Jade writes her journal entry for what is new (if the journal is on)."""
        j = getattr(self.jade, "journal", None)
        if j is not None:
            try:
                await j.write(self)
            except Exception:
                pass

    def stepper(self, text: str | None = None, images: list[str] | None = None) -> "Stepper":
        if text is not None:
            self.add_user(text, images)
        return Stepper(self)

    async def run(self, text: str | None = None, images: list[str] | None = None) -> AsyncIterator[E.Event]:
        """One full turn: model calls and tool calls until Jade answers (or max_steps)."""
        st = self.stepper(text, images)
        while not st.done:
            async for ev in st.stream():
                yield ev

    async def ask(self, text: str, images: list[str] | None = None, on_event: Callable[[E.Event], None] | None = None) -> str:
        """Run a turn and return the answer."""
        final, err = "", ""
        async for ev in self.run(text, images):
            if on_event:
                on_event(ev)
            if isinstance(ev, E.Done):
                final = ev.text
            elif isinstance(ev, E.Error):
                err = ev.message
        if err and not final:
            raise RuntimeError(err)
        return final

    @property
    def last_answer(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and m.get("content"):
                return clean_reply(m["content"])
        return ""


def clean_reply(s: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", str(s or "")).strip()


class Stepper:
    """The agent loop, one move at a time. stream() runs the next move and yields its events:
    - with no tool calls pending: a model call (Text/Thinking deltas, then ModelDone with the calls it asks for);
    - with tool calls pending: the next tool call (ToolStart, ToolProgress…, ToolEnd).
    Between moves you can inspect .pending, change a call's arguments (edit) or skip it (skip)."""

    def __init__(self, session: Session):
        self.s = session
        self.jade = session.jade
        self.pending: list[ToolCall] = []
        self.steps = 0
        self.done = False
        self.final = ""
        self._approved: set[str] = set()

    # ---- control between moves
    def skip(self, call_id: str | None = None, reason: str = "skipped by the user") -> None:
        i = self._find(call_id)
        c = self.pending.pop(i)
        self.s.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": f"Not run: {reason}."})

    def edit(self, call_id: str | None, arguments: dict) -> None:
        self.pending[self._find(call_id)].arguments = arguments

    def approve(self, call_id: str | None = None) -> None:
        self._approved.add(self.pending[self._find(call_id)].id)

    def _find(self, call_id):
        if not self.pending:
            raise IndexError("no tool call is pending")
        if call_id is None:
            return 0
        for i, c in enumerate(self.pending):
            if c.id == call_id:
                return i
        raise KeyError(f"no pending tool call {call_id}")

    async def step(self) -> list[E.Event]:
        return [ev async for ev in self.stream()]

    async def stream(self) -> AsyncIterator[E.Event]:
        if self.done:
            return
        s = self.s
        if s._cancel:
            s._cancel = False
            for c in list(self.pending):
                self.skip(c.id, "the turn was stopped")
            self._finish("(stopped)")
            yield E.Error("stopped")
            yield E.Done(self.final, self.steps)
            return
        s._busy = True
        try:
            if self.pending:
                async for ev in self._run_tool(self.pending.pop(0)):
                    yield ev
                if not self.pending:
                    s.save()
                return
            if self.steps >= min(self.jade.settings.max_steps, self.s.max_steps or 10**9):
                self._finish(f"(stopped after {self.steps} steps: max_steps)")
                yield E.Error(f"stopped after {self.steps} model calls (settings.max_steps)")
                yield E.Done(self.final, self.steps)
                return
            async for ev in self._model():
                yield ev
        finally:
            s._busy = False

    # ---- a model call
    async def _model(self) -> AsyncIterator[E.Event]:
        s, jade = self.s, self.jade
        self.steps += 1
        query = next((m.get("display") or m["content"] for m in reversed(s.messages) if m["role"] == "user"), "")
        await jade.prepare()
        tools = jade.router.select(s, query)
        system = P.build(jade, s, tools, query)
        compacted = False
        async for ev in jade.context.fit(s, system, tools):
            compacted = True
            yield ev
        if compacted:
            system = P.build(jade, s, tools, query)
        msgs = [{"role": "system", "content": system}] + [m for m in s.messages]
        text, thinking, calls, usage = [], [], [], None
        try:
            async for c in jade.provider.chat(msgs, [t.schema(jade) for t in tools] or None):
                if s._cancel:
                    break
                if c.thinking:
                    thinking.append(c.thinking)
                    yield E.Thinking(c.thinking)
                if c.text:
                    text.append(c.text)
                    yield E.Text(c.text)
                if c.tool_calls:
                    calls.extend(c.tool_calls)
                if c.usage:
                    usage = c.usage
        except ProviderError as e:
            self._finish("")
            s.save()
            yield E.Error(str(e))
            yield E.Done("", self.steps)
            return
        body = "".join(text)
        calls = [c for c in calls if c.name]
        # some models write the call as JSON text instead of a tool call
        if not calls and body.strip().startswith("{") and '"name"' in body:
            c = _text_tool_call(body, {t.name for t in jade.tools.active(jade)})
            if c:
                calls, body = [c], ""
        m = {"role": "assistant", "content": body, "ts": time.time()}
        if thinking:
            m["thinking"] = "".join(thinking)
        if calls:
            m["tool_calls"] = [c.to_dict() for c in calls]
        s.messages.append(m)
        if usage:
            s.meta["last_prompt_tokens"] = usage.prompt_tokens
        yield E.ModelDone(body, [c.to_dict() for c in calls], usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0)
        if calls and not s._cancel:
            self.pending = list(calls)
            for c in calls:
                if c.name not in [t.name for t in tools] and jade.tools.get(c.name) and c.name not in s.tools_loaded:
                    s.tools_loaded.append(c.name)   # the model named a tool that was not sent: keep it from now on
        else:
            self._finish(clean_reply(body))
            s.save()
            jade._after_turn(s)
            yield E.Done(self.final, self.steps)

    def _finish(self, text: str) -> None:
        self.done = True
        self.final = text
        self.pending = []

    # ---- a tool call
    async def _run_tool(self, c: ToolCall) -> AsyncIterator[E.Event]:
        s, jade = self.s, self.jade
        yield E.ToolStart(c.id, c.name, c.arguments)
        t = jade.tools.get(c.name)
        if s.allow_tools is not None and c.name not in s.allow_tools:
            msg = f"Error: {c.name} is not available right now."
            s.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": msg})
            yield E.ToolEnd(c.id, c.name, msg, error=True)
            return
        need = jade.settings.approve == "all" or (jade.settings.approve == "risky" and t is not None and t.risky)
        if need and c.id not in self._approved:
            ok = await jade.ask_approval(c, t) if jade.approver else False
            if not ok:
                msg = "Not run: the user did not approve this call." if jade.approver else "Not run: approval is required (settings.approve) and nobody is here to approve."
                s.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": msg})
                yield E.ToolEnd(c.id, c.name, msg, error=True, skipped=True)
                return
        q: asyncio.Queue[str] = asyncio.Queue()
        ctx = ToolContext(jade, s, c.id, emit=q.put_nowait)
        task = asyncio.create_task(jade.tools.call(c.name, c.arguments, ctx))
        while not task.done():
            try:
                line = await asyncio.wait_for(q.get(), 0.1)
                yield E.ToolProgress(c.id, c.name, line)
            except asyncio.TimeoutError:
                if s._cancel and not task.done():
                    task.cancel()
        while not q.empty():
            yield E.ToolProgress(c.id, c.name, q.get_nowait())
        try:
            r = task.result()
            out, err, ms = r.output, r.error, r.ms
        except asyncio.CancelledError:
            out, err, ms = "Interrupted: the user stopped the turn.", True, 0
        s.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": out, "ts": time.time(), **({"error": True} if err else {})})
        yield E.ToolEnd(c.id, c.name, out, err, ms)


def _text_tool_call(body: str, names: set[str]) -> ToolCall | None:
    try:
        j = json.loads(body.strip().strip("`").removeprefix("json").strip())
    except ValueError:
        return None
    if isinstance(j, dict) and j.get("name") in names:
        args = j.get("arguments") or j.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        return ToolCall(j["name"], args if isinstance(args, dict) else {})
    return None


class SessionStore:
    """Chats as JSON files in <data>/chats/<id>.json, with an index for listing."""

    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, sid: str) -> Path:
        if not re.fullmatch(r"[\w-]{1,64}", sid):
            raise ValueError(f"bad chat id {sid!r}")
        return self.dir / f"{sid}.json"

    def save(self, s: Session) -> None:
        p = self.path(s.id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False))
        tmp.replace(p)

    def load(self, jade: "Jade", sid: str) -> Session:
        p = self.path(sid)
        if not p.is_file():
            raise KeyError(f"no chat {sid}")
        return Session.from_dict(jade, json.loads(p.read_text()))

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                out.append({"id": d["id"], "title": d.get("title", ""), "updated": d.get("updated", 0), "messages": len(d.get("messages", []))})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(out, key=lambda x: -x["updated"])

    def delete(self, sid: str) -> None:
        self.path(sid).unlink(missing_ok=True)
