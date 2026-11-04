"""The trajectory log: every agent run, exactly as the model saw it, for fine-tuning. The same records and export
formats as the Jade Vault app, so exports from the app and from jade-py train together (jade-tune reads both).

Each run is one JSON file in <data>/trajectories. Rate a reply 👍/👎 (dashboard, REPL /rate, the API, or
jade.traj.rate) to filter the export, or export the ratings themselves (a "metadata" field per line) so 👍/👎 pairs
of the same prompt can train with DPO / KTO. Retry a reply (dashboard ↻, REPL /retry, session.retry()) to get a
second answer to the same prompt: 👎 the worse one, 👍 the better one.

    jade traj stats
    jade traj export --filter prefs --meta -o prefs.jsonl
    text = jade.traj.export(format="openai", filter="prefs", meta=True)

Formats: "openai" (messages + tools: Unsloth, Axolotl, TRL, OpenAI) and "sharegpt" (LLaMA-Factory). Filters:
"clean" (finished runs minus 👎), "good" (only 👍), "prefs" (finished runs, 👎 too: for DPO / KTO), "all" (errors too).
"""
from __future__ import annotations

import json
import os
import random
import string
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Jade
    from .session import Session

FORMATS = ("openai", "sharegpt")
FILTERS = ("clean", "good", "prefs", "all")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _base36(n: int) -> str:
    d = string.digits + string.ascii_lowercase
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = d[r] + s
    return s or "0"


def model_message(m: dict) -> dict | None:
    """A chat message as the trajectory keeps it (the app's shape): role and content, assistant tool calls as
    {function: {name, arguments}}, tool results as {role: tool, tool_name, content}. Pictures are left out."""
    role = m.get("role")
    if role == "tool":
        o = {"role": "tool", "tool_name": m.get("name") or m.get("tool_name") or "", "content": str(m.get("content") or "")}
        if m.get("error"):
            o["error"] = True
        return o
    if role not in ("system", "user", "assistant"):
        return None
    o: dict[str, Any] = {"role": role, "content": m.get("content") if isinstance(m.get("content"), str) else str(m.get("content") or "")}
    if role == "assistant":
        if m.get("thinking"):
            o["thinking"] = m["thinking"]
        calls = m.get("tool_calls") or []
        if calls:
            o["tool_calls"] = [{"function": {"name": (c.get("function") or c).get("name"), "arguments": (c.get("function") or c).get("arguments") or {}}} for c in calls]
    return o


class Recorder:
    """One per agent turn. A turn that gets compacted mid-way is split into segments, because each segment must be a
    conversation the model really saw from its first message."""

    def __init__(self, store: "TrajStore", session: "Session"):
        self.store, self.s = store, session
        title = session.title or ""
        meta = session.meta or {}
        self.base = {"session": session.id, "title": title,
                     "source": "heartbeat" if meta.get("heartbeat") else "schedule" if title.startswith("⏰") else "curiosity" if title.startswith("✦") else "chat"}
        self.rec: dict | None = None
        self.part = 0
        self.saved: list[str] = []
        self._t0 = 0.0

    @property
    def id(self) -> str | None:
        return (self.rec or {}).get("id") or (self.saved[-1] if self.saved else None)

    def _fresh(self, msgs, tools, provider, model, params) -> dict:
        r = {"id": uuid.uuid4().hex[:10] + "-" + _base36(_now_ms()), "v": 1, "started": _now_ms(), "ended": None, "part": self.part, **self.base,
             "provider": provider, "model": model, "params": params, "tools": [t.get("function", t) for t in (tools or [])],
             "messages": [x for x in (model_message(m) for m in msgs) if x], "steps": [], "outcome": "running", "error": None, "rating": None}
        self.part += 1
        return r

    def call(self, msgs: list[dict], tools: list[dict] | None, provider: str, model: str, params: dict) -> None:
        cur = [x for x in (model_message(m) for m in msgs) if x]
        first = lambda ms: next((m.get("content") for m in ms if m.get("role") == "user"), None)  # noqa: E731
        same = self.rec is not None and len(cur) == len(self.rec["messages"]) and first(cur) == first(self.rec["messages"])
        if not same:
            if self.rec is not None and self.rec["steps"]:
                self._flush("segment")
            self.rec = self._fresh(msgs, tools, provider, model, params)   # a request that never got a reply is dropped
        self._t0 = time.monotonic()

    def reply(self, asst: dict, provider: str, model: str, prompt_tokens: int | None, completion_tokens: int | None, est: int | None = None) -> None:
        if self.rec is None:
            return
        self.rec["messages"].append(model_message(asst))
        self.rec["steps"].append({"model": model, "provider": provider, "ms": int((time.monotonic() - self._t0) * 1000), "prompt_tokens": prompt_tokens,
                                  "completion_tokens": completion_tokens, "est_prompt_tokens": est})
        if model != self.rec["model"]:
            self.rec["models"] = list(dict.fromkeys([*(self.rec.get("models") or [self.rec["model"]]), model]))

    def tool(self, name: str, content: str, error: bool = False) -> None:
        if self.rec is not None:
            self.rec["messages"].append({"role": "tool", "tool_name": name, "content": str(content), **({"error": True} if error else {})})

    def _flush(self, outcome: str, error: str | None = None) -> None:
        r, self.rec = self.rec, None
        if r is None:
            return
        r["ended"], r["outcome"], r["error"] = _now_ms(), outcome, error or None
        try:
            self.store.put(r)
            self.saved.append(r["id"])
        except OSError:
            pass

    def end(self, outcome: str, error: str | None = None) -> str | None:
        rid = self.id
        self._flush(outcome, error)
        return rid


class _Off:
    id = None

    def call(self, *a, **k):
        pass

    def reply(self, *a, **k):
        pass

    def tool(self, *a, **k):
        pass

    def end(self, *a, **k):
        return None


# ---------------------------------------------------------------- export (the app's converters)
def to_openai_messages(msgs: list[dict]) -> list[dict]:
    out: list[dict] = []
    seq = 0
    pending: list[dict] = []

    def flush():
        for p in pending:
            out.append({"role": "tool", "tool_call_id": p["id"], "content": "(no result: the run was interrupted)"})
        pending.clear()

    for m in msgs:
        if m.get("role") == "tool":
            k = next((i for i, p in enumerate(pending) if p["name"] == m.get("tool_name")), 0)
            if pending:
                p = pending.pop(k)
                out.append({"role": "tool", "tool_call_id": p["id"], "content": str(m.get("content") or "")})
            else:
                out.append({"role": "user", "content": f"[Result of {m.get('tool_name') or 'a tool'}]\n{m.get('content') or ''}"})
            continue
        flush()
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for t in m["tool_calls"]:
                seq += 1
                cid = f"call_{seq}_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
                f = t.get("function") or {}
                pending.append({"id": cid, "name": f.get("name")})
                a = f.get("arguments")
                calls.append({"id": cid, "type": "function", "function": {"name": f.get("name"), "arguments": a if isinstance(a, str) else json.dumps(a or {}, ensure_ascii=False, separators=(",", ":"))}})
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        else:
            out.append({"role": m.get("role"), "content": str(m.get("content") or "")})
    flush()
    return out


def to_openai(r: dict, thinking: bool = False) -> dict:
    msgs = to_openai_messages(r["messages"])
    if thinking:
        th = iter([m for m in r["messages"] if m.get("role") == "assistant"])
        for m in msgs:
            if m["role"] == "assistant":
                src = next(th, None)
                if src and src.get("thinking"):
                    m["reasoning_content"] = src["thinking"]
    out: dict = {"messages": msgs}
    if r.get("tools"):
        out["tools"] = [{"type": "function", "function": f} for f in r["tools"]]
    return out


def to_sharegpt(r: dict, thinking: bool = False) -> dict:
    conv: list[dict] = []
    system = ""
    for m in r["messages"]:
        role = m.get("role")
        if role == "system":
            system = m.get("content") or ""
        elif role == "user":
            conv.append({"from": "human", "value": m.get("content") or ""})
        elif role == "assistant":
            think = f"<think>\n{m['thinking']}\n</think>\n\n" if thinking and m.get("thinking") else ""
            if m.get("tool_calls"):
                if m.get("content") or think:
                    conv.append({"from": "gpt", "value": think + (m.get("content") or "")})
                for t in m["tool_calls"]:
                    conv.append({"from": "function_call", "value": json.dumps({"name": t["function"]["name"], "arguments": t["function"]["arguments"]}, ensure_ascii=False, separators=(",", ":"))})
            else:
                conv.append({"from": "gpt", "value": think + (m.get("content") or "")})
        elif role == "tool":
            conv.append({"from": "observation", "value": m.get("content") or ""})
    # LLaMA-Factory wants human/observation at odd positions and gpt/function_call at even ones: merge neighbours that break that
    side = lambda x: 0 if x["from"] in ("human", "observation") else 1  # noqa: E731
    fixed: list[dict] = []
    for c in conv:
        prev = fixed[-1] if fixed else None
        if prev and side(prev) == side(c) and prev["from"] == c["from"] and c["from"] != "function_call":
            prev["value"] += "\n\n" + c["value"]
        else:
            fixed.append(c)
    return {"conversations": fixed, "system": system, "tools": json.dumps(r.get("tools") or [], ensure_ascii=False, separators=(",", ":"))}


def keep(r: dict, filter: str) -> bool:   # noqa: A002
    if filter == "all":
        return True
    if not r.get("steps") or not any(m.get("role") == "assistant" for m in r.get("messages", [])):
        return False
    if r.get("outcome") not in ("ok", "segment"):
        return False
    if filter == "prefs":
        return True   # finished runs, 👎 too: for preference training (DPO / KTO) with ratings exported
    if r.get("rating") == -1:
        return False
    if filter == "good":
        return r.get("rating") == 1
    return True   # "clean": finished runs, not thumbed down


def metadata(r: dict) -> dict:
    return {"id": r["id"], "started": datetime.fromtimestamp(r["started"] / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "model": ",".join(r["models"]) if r.get("models") else r.get("model"), "provider": r.get("provider"), "source": r.get("source"),
            "outcome": r.get("outcome"), "rating": r.get("rating"), "steps": r.get("steps")}


class TrajStore:
    def __init__(self, jade: "Jade"):
        self.jade = jade

    @property
    def folder(self) -> Path:
        return self.jade.settings.data_path / "trajectories"

    def recorder(self, session: "Session"):
        return Recorder(self, session) if self.jade.settings.traj_on else _Off()

    def _path(self, rid: str) -> Path:
        if not rid or "/" in rid or "\\" in rid or rid.startswith("."):
            raise ValueError(f"bad trajectory id {rid!r}")
        return self.folder / f"{rid}.json"

    def put(self, r: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self._path(r["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(r, ensure_ascii=False))
        os.replace(tmp, p)

    def get(self, rid: str) -> dict | None:
        try:
            return json.loads(self._path(rid).read_text())
        except (OSError, ValueError):
            return None

    def all(self) -> list[dict]:
        out = []
        if self.folder.is_dir():
            for p in self.folder.glob("*.json"):
                try:
                    out.append(json.loads(p.read_text()))
                except (OSError, ValueError):
                    continue
        return sorted(out, key=lambda r: r.get("started") or 0)

    def count(self) -> int:
        return len(list(self.folder.glob("*.json"))) if self.folder.is_dir() else 0

    def clear(self) -> int:
        n = 0
        if self.folder.is_dir():
            for p in self.folder.glob("*.json"):
                p.unlink(missing_ok=True)
                n += 1
        return n

    def rate(self, rid: str | None, rating: int | None) -> bool:
        if rating not in (1, -1, None):
            raise ValueError("rating is 1 (👍), -1 (👎) or None")
        r = self.get(rid) if rid else None
        if r is None:
            return False
        r["rating"] = rating
        self.put(r)
        return True

    def stats(self) -> dict:
        a = self.all()
        return {"runs": len(a), "steps": sum(len(r.get("steps") or []) for r in a), "good": sum(r.get("rating") == 1 for r in a),
                "bad": sum(r.get("rating") == -1 for r in a), "errors": sum(r.get("outcome") == "error" for r in a),
                "bytes": sum(len(json.dumps(r, ensure_ascii=False)) for r in a)}

    def export(self, format: str | None = None, filter: str | None = None, thinking: bool | None = None, meta: bool | None = None) -> str:   # noqa: A002
        """The JSONL text: one training example per run that passes the filter (defaults from the settings)."""
        st = self.jade.settings
        format, filter = format or st.traj_format, filter or st.traj_filter
        thinking = st.traj_thinking if thinking is None else thinking
        meta = st.traj_meta if meta is None else meta
        if format not in FORMATS:
            raise ValueError(f"format is one of {', '.join(FORMATS)}")
        if filter not in FILTERS:
            raise ValueError(f"filter is one of {', '.join(FILTERS)}")
        lines = []
        for r in self.all():
            if not keep(r, filter):
                continue
            o = to_sharegpt(r, thinking) if format == "sharegpt" else to_openai(r, thinking)
            if meta:
                o["metadata"] = metadata(r)
            lines.append(json.dumps(o, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")


def rate_message(session: "Session", index: int | None, rating: int | None) -> dict:
    """Rate a reply in a chat (index of the assistant message; None: the last reply). Saves the chat and the run."""
    if rating not in (1, -1, None):
        raise ValueError("rating is 1 (👍), -1 (👎) or null")
    msgs = session.messages
    if index is None:
        index = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "assistant" and msgs[i].get("content") and not msgs[i].get("tool_calls")), None)
    if index is None or not (0 <= index < len(msgs)) or msgs[index].get("role") != "assistant":
        raise ValueError("no reply to rate there")
    m = msgs[index]
    if rating is None:
        m.pop("rating", None)
    else:
        m["rating"] = rating
    session.save()
    ok = session.jade.traj.rate(m.get("traj"), rating)
    return {"index": index, "rating": rating, "trajectory": m.get("traj") if ok else None}
