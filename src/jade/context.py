"""Keeping a chat inside the model's context window, and choosing which optional tools to send.

Context: when a chat grows past what fits (settings.num_ctx), old tool outputs are shortened first; if that is not
enough, the oldest part of the chat is summarised by the model (kept in session.summary, shown in the system prompt)
and dropped from the messages. A turn is never cut in the middle of a tool call and its result.

Tool router: core tools are always sent; optional ones only when the message (or a loaded skill) is about them, or
when they were used earlier in the chat. The set only grows within a chat and keeps one order, so the prompt's start
stays the same and the model's prompt cache keeps working. more_tools loads any other tool on request."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, AsyncIterator

from . import events as E
from .memory import BM25, tokens
from .tools.registry import Tool

if TYPE_CHECKING:
    from .core import Jade
    from .session import Session

from .session import estimate_tokens

KEEP_RECENT = 8          # messages whose tool outputs are never shortened
SHORT_TOOL = 1200        # chars an old tool output is cut to


class Context:
    def __init__(self, jade: "Jade"):
        self.jade = jade

    def budget(self, system: str, tools: list[Tool]) -> int:
        st = self.jade.settings
        fixed = estimate_tokens(system) + sum(estimate_tokens(json.dumps(t.schema(self.jade))) for t in tools)
        return int(st.num_ctx * 0.8) - fixed

    async def fit(self, s: "Session", system: str, tools: list[Tool]) -> AsyncIterator[E.Event]:
        budget = self.budget(system, tools)
        if estimate_tokens(s.messages) <= budget:
            return
        # 1. shorten old tool outputs
        for m in s.messages[:-KEEP_RECENT]:
            if m.get("role") == "tool" and len(str(m.get("content") or "")) > SHORT_TOOL and not m.get("shortened"):
                c = str(m["content"])
                m["content"] = c[:SHORT_TOOL] + f"\n[… {len(c) - SHORT_TOOL} more characters, shortened to save room]"
                m["shortened"] = True
        if estimate_tokens(s.messages) <= budget:
            return
        # 2. summarise the oldest part: keep about a third of the budget of the newest messages, cut at a user message
        keep_from = len(s.messages)
        used = 0
        while keep_from > 1 and used + estimate_tokens(s.messages[keep_from - 1]) < budget // 3:
            keep_from -= 1
            used += estimate_tokens(s.messages[keep_from])
        while keep_from < len(s.messages) and s.messages[keep_from].get("role") != "user":
            keep_from += 1
        if keep_from >= len(s.messages):  # the last user message alone is too big: keep it anyway
            keep_from = max(i for i, m in enumerate(s.messages) if m.get("role") == "user")
        old, s.messages = s.messages[:keep_from], s.messages[keep_from:]
        if not old:
            return
        s.summary = await self.summarise(s.summary, old)
        s.meta["compactions"] = s.meta.get("compactions", 0) + 1
        try:
            self.jade._journal(s, old)
        except Exception:
            pass
        yield E.Compacted(len(old))

    async def summarise(self, previous: str, msgs: list[dict]) -> str:
        lines = []
        for m in msgs:
            r = m.get("role")
            if r == "tool":
                lines.append(f"[{m.get('name')} result] {str(m.get('content'))[:600]}")
            elif r == "assistant" and m.get("tool_calls"):
                lines.append("assistant called " + ", ".join(f"{c['name']}({json.dumps(c.get('arguments'))[:200]})" for c in m["tool_calls"]) + (f" and said: {m.get('content')}" if m.get("content") else ""))
            else:
                lines.append(f"{r}: {str(m.get('content'))[:2000]}")
        text = "\n".join(lines)[-40000:]
        prompt = ("Summarise this part of a conversation for yourself, so you can continue it without the messages. Keep facts, decisions, "
                  "file paths, open questions and what the user wants; drop pleasantries. Bullet points, at most 250 words.\n\n"
                  + (f"Summary so far:\n{previous}\n\n" if previous else "") + f"Conversation:\n{text}")
        try:
            out = await self.jade.provider.complete([{"role": "user", "content": prompt}], temperature=0.1)
            out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip()
            if out:
                return out
        except Exception:
            pass
        return (previous + "\n" if previous else "") + f"- ({len(msgs)} earlier messages were dropped to save room; ask the user if something is missing.)"


class Router:
    MAX_MATCHED = 5
    MIN_SAVING = 6000   # chars of optional tool definitions below which everything is sent

    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.hidden: list[str] = []

    def select(self, s: "Session", query: str) -> list[Tool]:
        jade = self.jade
        active = [t for t in jade.tools.active(jade) if s.allow_tools is None or t.name in s.allow_tools or t.name == "more_tools"]
        self.hidden = []
        if jade.settings.tool_router == "off":
            return [t for t in active if t.name != "more_tools"]
        opt = [t for t in active if not t.core and t.name != "more_tools"]
        if not opt or sum(len(json.dumps(t.schema(jade))) for t in opt) < self.MIN_SAVING:
            return [t for t in active if t.name != "more_tools"]
        keep = set(s.tools_loaded)
        for m in s.messages[-80:]:
            for c in m.get("tool_calls") or []:
                keep.add(c["name"])
        q = " " + re.sub(r"[^a-z0-9_ ]+", " ", query.lower()) + " "
        skill_text = "\n".join(str(m.get("content")) for m in s.messages[-8:] if m.get("role") == "tool" and m.get("name") == "skill")
        for t in opt:
            if f" {t.name.lower()} " in q or (skill_text and re.search(r"\b" + re.escape(t.name) + r"\b", skill_text)):
                keep.add(t.name)
        if query.strip():
            docs = [tokens(f"{t.name.replace('_', ' ')} {t.hints} {t.hints} {t.describe(jade)}") for t in opt]
            sc = BM25(docs).scores(tokens(query))
            top = max(sc) if sc else 0
            for i in sorted(range(len(opt)), key=lambda i: -sc[i])[: self.MAX_MATCHED]:
                if sc[i] >= max(0.5 * top, 1.2):
                    keep.add(opt[i].name)
        names = {t.name for t in opt}
        s.tools_loaded = list(dict.fromkeys(s.tools_loaded + [n for n in sorted(keep) if n in names]))
        chosen = [t for t in active if (t.core and t.name != "more_tools") or t.name in s.tools_loaded]
        self.hidden = [t.name for t in opt if t.name not in s.tools_loaded]
        if self.hidden and jade.tools.get("more_tools"):
            chosen.append(jade.tools.get("more_tools"))
        return chosen


def more_tools_tool(jade: "Jade") -> Tool:
    def desc(j: "Jade") -> str:
        hidden = ", ".join(j.router.hidden) or "none"
        return f"More tools exist but are not loaded, to save context: {hidden}. Call this with names (or a short description of the job) to load them; they are callable from your next step."

    def run(ctx, names=None, query: str = "", **_):
        j = ctx.jade
        opt = [t for t in j.tools.active(j) if not t.core and t.name != "more_tools"]
        want = set(names if isinstance(names, list) else re.split(r"[,\s]+", names or "") if names else [])
        if query:
            sc = BM25([tokens(f"{t.name} {t.hints} {t.describe(j)}") for t in opt]).scores(tokens(query))
            want |= {opt[i].name for i in sorted(range(len(opt)), key=lambda i: -sc[i])[:4] if sc[i] > 0}
        hits = [t for t in opt if t.name in want]
        if not hits:
            return "No tool matches. Not loaded: " + ", ".join(t.name for t in opt)
        if ctx.session is not None:
            ctx.session.tools_loaded = list(dict.fromkeys(ctx.session.tools_loaded + [t.name for t in hits]))
        return f"Loaded {len(hits)} tool(s); call them from your next step:\n" + "\n".join(f"- {t.name}: {t.describe(j)[:220]}" for t in hits)

    return Tool("more_tools", desc, {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}, "description": "tool names to load"},
                                                                       "query": {"type": "string", "description": "or: what you need to do"}}}, run, core=True)
