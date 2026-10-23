"""jade.* for scripts and flows: what JavaScript (and the shell) can ask Jade to do. The same calls as the app:
tool, list, decide, ask, agent, flow, flows, shell, notify, kv."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Jade

FLOW_MAX_DEPTH = 4


async def ask_once(jade: "Jade", prompt: str, system: str = "", as_json: bool = False) -> Any:
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": str(prompt)}]
    out = await jade.provider.complete(msgs, temperature=0.2, **({"format": "json"} if as_json else {}))
    out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip()
    if not as_json:
        return out
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", out)
    raw = m.group(1) if m else out
    i = min([x for x in (raw.find("{"), raw.find("[")) if x >= 0] or [0])
    try:
        return json.loads(raw[i:])
    except ValueError as e:
        raise RuntimeError(f"the model did not answer with JSON ({e})") from None


async def run_agent_prompt(jade: "Jade", message: str, chat: str = "new", key: str = "", title: str = "") -> dict:
    sid = jade.state.get("agent_chats", {}).get(key) if chat == "same" and key else None
    try:
        s = jade.session(sid) if sid else None
    except (KeyError, ValueError):
        s = None
    if s is None:
        s = jade.session()
        if title:
            s.title = title
        if chat == "same" and key:
            jade.state.setdefault("agent_chats", {})[key] = s.id
            jade.save_state()
    reply = await s.ask(message)
    return {"reply": reply, "chat": s.id}


def _kv_path(jade):
    return jade.settings.data_path / "kv.json"


def kv_op(jade: "Jade", a: dict) -> Any:
    p = _kv_path(jade)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    op = a.get("op") or "get"
    if op == "get":
        return data.get(str(a.get("key")))
    if op == "set":
        data[str(a.get("key"))] = a.get("value")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=1))
        return True
    if op == "list":
        return list(data)
    if op in ("del", "delete"):
        data.pop(str(a.get("key")), None)
        p.write_text(json.dumps(data, indent=1))
        return True
    raise RuntimeError(f"unknown kv op {op}")


async def jade_bridge(jade: "Jade", op: str, a: dict, *, depth: int = 0, via: str = "script", session=None) -> Any:
    if op == "tool":
        name = str(a.get("name") or "")
        if not name:
            raise RuntimeError("jade.tool needs a tool name")
        if name == "flow" and depth >= FLOW_MAX_DEPTH:
            raise RuntimeError("flows call each other too deeply")
        if not jade.tools.get(name):
            raise RuntimeError(f"no tool named {name}. jade.list() shows them")
        return await jade.run_tool(name, a.get("args") if isinstance(a.get("args"), dict) else {}, session)
    if op == "list":
        return [{"name": t.name, "description": t.describe(jade)[:240]} for t in jade.tools.active(jade)]
    if op == "decide":
        from .reasoning import decide_data
        return await decide_data(jade, a.get("state"), a.get("question") or "", a.get("type") or "choice", a.get("options"))
    if op == "ask":
        return await ask_once(jade, a.get("prompt") or "", a.get("system") or "", bool(a.get("json")))
    if op == "agent":
        return await run_agent_prompt(jade, a.get("message") or a.get("prompt") or "", a.get("chat") or "new")
    if op == "flow":
        if depth >= FLOW_MAX_DEPTH:
            raise RuntimeError("flows call each other too deeply")
        run = await jade.flows.run_by_name(a.get("name"), a.get("input"), via=via, depth=depth + 1)
        if run["status"] != "ok":
            raise RuntimeError(f'flow "{run["flow"]}" {run["status"]}: {run.get("error") or ""}')
        return run["output"]
    if op == "flows":
        return [{"name": f["name"], "description": f.get("description", ""), "enabled": f.get("enabled", True) is not False} for f in jade.flows.list() if not f.get("_bad")]
    if op == "shell":
        from .tools.builtin import run_shell
        out, code = await run_shell(jade, str(a.get("command") or ""), timeout=120)
        return {"code": code, "stdout": out, "stderr": ""}
    if op == "notify":
        jade.notify(str(a.get("title") or "Jade"), str(a.get("body") or ""))
        return True
    if op == "kv":
        return kv_op(jade, a)
    raise RuntimeError(f"unknown jade call: {op}")
