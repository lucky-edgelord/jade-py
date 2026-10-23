"""Tools: a name, a description, a JSON schema and a Python function. The agent sends the definitions to the
model and runs the calls it makes. Register your own with @jade.tool or Registry.add."""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..core import Jade
    from ..session import Session


class ToolError(Exception):
    """A tool failed in a way the model should see (it gets the message and can try again)."""


@dataclass
class ToolContext:
    jade: "Jade"
    session: "Session | None" = None
    call_id: str = ""
    emit: Callable[[str], None] | None = None   # progress lines (shown live in the REPL)
    images: list = field(default_factory=list)  # pictures for the model (base64 JPEG/PNG, label), shown after the tool calls

    def show_image(self, b64: str, label: str = "") -> bool:
        """Hand the model a picture: it arrives in a message right after this round of tool calls (if settings.look_images)."""
        if not self.jade.settings.look_images or self.session is None:
            return False
        self.images.append((b64, label))
        return True

    @property
    def vault(self):
        return self.jade.vault

    @property
    def settings(self):
        return self.jade.settings

    def say(self, line: str) -> None:
        if self.emit:
            try:
                self.emit(line)
            except Exception:
                pass


Fn = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str | Callable[["Jade"], str]
    parameters: dict
    fn: Fn
    risky: bool = False            # asks first when settings.approve == "risky"
    core: bool = True              # always sent; optional tools go through the tool router
    hints: str = ""                # extra words the tool router matches on
    enabled: Callable[["Jade"], bool] | None = None
    summary: str = ""              # one line for the system prompt's tool list
    hidden: bool = False           # callable (scripts, flows, codemode) but never offered to the model

    def describe(self, jade: "Jade") -> str:
        return self.description(jade) if callable(self.description) else self.description

    def schema(self, jade: "Jade") -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.describe(jade), "parameters": self.parameters}}


@dataclass
class ToolResult:
    name: str
    output: str
    error: bool = False
    ms: int = 0


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def add(self, t: Tool) -> Tool:
        self.tools[t.name] = t
        return t

    def remove(self, name: str) -> None:
        self.tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self.tools

    def names(self) -> list[str]:
        return list(self.tools)

    def active(self, jade: "Jade") -> list[Tool]:
        return [t for t in self.tools.values() if not t.hidden and (not t.enabled or t.enabled(jade))]

    def copy(self) -> "Registry":
        r = Registry()
        r.tools = dict(self.tools)
        return r

    def tool(self, name: str | None = None, *, description: str = "", parameters: dict | None = None, risky: bool = False, core: bool = False, hints: str = "", summary: str = ""):
        """Decorator: register a function as a tool. Parameters default to a schema made from the signature."""
        def deco(fn: Fn) -> Fn:
            self.add(Tool(name or fn.__name__, description or (inspect.getdoc(fn) or fn.__name__), parameters or schema_from_signature(fn), _wrap_plain(fn),
                          risky=risky, core=core, hints=hints, summary=summary))
            return fn
        return deco

    async def call(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        t0 = time.monotonic()
        t = self.tools.get(name)
        if not t:
            return ToolResult(name, f"Error: there is no tool named {name}. Tools: {', '.join(x.name for x in self.active(ctx.jade))}", True)
        if t.enabled and not t.enabled(ctx.jade):
            return ToolResult(name, f"Error: the {name} tool is switched off.", True)
        try:
            out = t.fn(ctx, **(args or {})) if _takes_kwargs(t.fn) else t.fn(ctx, args or {})
            if inspect.isawaitable(out):
                out = await out
            text = out if isinstance(out, str) else _to_text(out)
            return ToolResult(name, text, False, int((time.monotonic() - t0) * 1000))
        except asyncio.CancelledError:
            raise
        except TypeError as e:
            return ToolResult(name, f"Error: bad arguments for {name}: {e}", True, int((time.monotonic() - t0) * 1000))
        except Exception as e:  # the model sees the error and can recover
            return ToolResult(name, f"Error: {e}", True, int((time.monotonic() - t0) * 1000))


def _to_text(o: Any) -> str:
    import json
    if o is None:
        return "(done)"
    try:
        return json.dumps(o, indent=1, ensure_ascii=False, default=str)
    except TypeError:
        return str(o)


def _takes_kwargs(fn: Fn) -> bool:
    return getattr(fn, "_jade_kwargs", True)


def _wrap_plain(fn: Fn) -> Fn:
    """User tools may be written without a ctx parameter: f(query: str) -> str."""
    params = list(inspect.signature(fn).parameters.values())
    wants_ctx = bool(params) and params[0].name in ("ctx", "context")

    def call(ctx: ToolContext, **kw):
        return fn(ctx, **kw) if wants_ctx else fn(**kw)
    call._jade_kwargs = True  # type: ignore[attr-defined]
    call.__name__ = fn.__name__
    return call


_PY2JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def schema_from_signature(fn: Fn) -> dict:
    props, req = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.name in ("ctx", "context", "self") or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
        origin = getattr(ann, "__origin__", ann)
        props[p.name] = {"type": _PY2JSON.get(origin, "string")}
        if p.default is inspect.Parameter.empty:
            req.append(p.name)
    return {"type": "object", "properties": props, **({"required": req} if req else {})}


def truncate_head(s: str, max_chars: int = 50_000, max_lines: int = 2000) -> str:
    lines = s.split("\n")
    cut = False
    if len(lines) > max_lines:
        lines, cut = lines[:max_lines], True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out, cut = out[:max_chars], True
    return out + ("\n\n[output truncated]" if cut else "")


def truncate_tail(s: str, max_chars: int = 50_000, max_lines: int = 2000) -> str:
    lines = s.split("\n")
    cut = False
    if len(lines) > max_lines:
        lines, cut = lines[-max_lines:], True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out, cut = out[-max_chars:], True
    return ("[earlier output truncated]\n\n" if cut else "") + out
