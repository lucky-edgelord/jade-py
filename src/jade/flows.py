"""Flows: small n8n-style automations, the same JSON files as the app (/Jade/Flows/<name>.flow.json).

    { "name", "description", "enabled", "trigger": {"type": "manual"|"schedule"|"note", "when", "path"},
      "vars": {…}, "timeout": seconds, "steps": [ step, … ] }

Steps run in order; each one's output is $json for the next. if / switch / each hold nested steps. Strings may
contain {{ expressions }} (JavaScript, evaluated by real Node in a sandbox) with $json, $input, $node, $vars, $item,
$index, $now, $today, $flow, $run and $date(fmt, date, days). JavaScript steps run in Node with fs rooted at the
vault and jade.* calling back into Python.

Runs: jade.flows.run_by_name(name, input), the flow tool, `jade flow run`, schedules, the API, and triggers
(schedule / note change) while `jade daemon` or `jade serve` runs. Run records: <data>/flow_runs.json."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import cron as C
from .bridge import FLOW_MAX_DEPTH, ask_once, jade_bridge, run_agent_prompt
from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade")
FLOW_DIR, FLOW_EXT, FLOW_MAX_STEPS = "/Jade/Flows", ".flow.json", 5000
CRON_LOG = "/Jade/Logs/cron.log"

# field: (key, label, kind, placeholder, required). kinds: tpl, area, code, expr, json, bool, num, sel:<a|b>, tool, flowsel, pbsel, lines
FLOW_TYPES: dict[str, dict] = {
    "tool": {"label": "Tool", "help": "Call one of Jade's tools. Output: what the tool returns (text).",
             "fields": [("tool", "Tool", "tool", "", True), ("args", "Arguments", "json", '{"query": "{{ $json.topic }}"}', False), ("parse", "Parse the result as JSON", "bool", "", False)]},
    "js": {"label": "JavaScript", "help": "Runs in Node: fs (rooted at the vault; realfs is the real one), path, fetch, and jade.* (tools, decide, ask, flows). $json, $input, $node, $vars, $item are set. Output: the returned value (or what it printed).",
           "fields": [("code", "Code", "code", "const notes = fs.readdirSync('/projects');\nreturn { count: notes.length };", True), ("timeout", "Time limit (seconds, default 60)", "num", "", False)]},
    "shell": {"label": "Shell", "help": "Runs a command line in the real shell (in the vault folder). Output: {code, stdout, stderr}. A non-zero exit fails the step unless \"allowFail\" is on.",
              "fields": [("command", "Command", "tpl", "ls projects | wc -l", True), ("cwd", "Folder in the vault (default /)", "tpl", "/", False), ("allowFail", "Allow failure", "bool", "", False), ("timeout", "Time limit (seconds, default 60)", "num", "", False)]},
    "decide": {"label": "Decide", "help": "Ollaya decision model: calibrated probabilities. Output: {result, choice|score|answer, confidence, probabilities}.",
               "fields": [("state", "Text to judge", "area", "{{ $json }}", True), ("question", "Question", "tpl", "Is this urgent?", True), ("kind", "Type", "sel:choice|score|yesno", "", False), ("options", "Options (one per line)", "lines", "urgent\nnormal\nlow", False)]},
    "llm": {"label": "Ask AI", "help": "One answer from the chat model (no tools, no chat). Output: the text, or parsed JSON when \"json\" is on.",
            "fields": [("prompt", "Prompt", "area", "Summarise in 3 bullets:\n{{ $json }}", True), ("system", "System prompt", "area", "", False), ("json", "Expect JSON back", "bool", "", False)]},
    "agent": {"label": "Jade agent", "help": "A full chat turn: Jade gets the message, uses her tools and answers in a chat. Output: {reply, chat}. Cannot run from inside her own chat turn.",
              "fields": [("prompt", "Message to Jade", "area", "File each line of {{ $json }} into its own note.", True), ("chat", "Chat", "sel:new|same", "", False)]},
    "note": {"label": "Note", "help": "Read or write a note. read → its text. write / append / prepend → {path, bytes}.",
             "fields": [("action", "Action", "sel:read|append|prepend|write", "", False), ("path", "Note (name or /path)", "tpl", "/Inbox.md", True), ("content", "Text", "area", "- {{ $now }} {{ $json }}", False)]},
    "set": {"label": "Set fields", "help": "Build an object. Output: the fields (merged into the incoming object when \"keep\" is on).",
            "fields": [("fields", "Fields", "json", '{"title": "{{ $json.name }}", "when": "{{ $today }}"}', True), ("keep", "Keep the incoming fields", "bool", "", False)]},
    "if": {"label": "If", "help": "Runs \"then\" or \"else\". Output: the last output of the branch that ran.",
           "fields": [("condition", "Condition (JavaScript)", "expr", "$json.count > 0", True)], "branches": ["then", "else"]},
    "switch": {"label": "Switch", "help": "Runs the first case whose \"match\" equals the value (or one of a list), else \"default\".",
               "fields": [("value", "Value (JavaScript)", "expr", "$json.result", True)], "cases": True},
    "each": {"label": "For each", "help": "Runs its steps once per item ($item, $index; $json starts as the item). Output: the list of results.",
             "fields": [("items", "List (JavaScript)", "expr", '$json.split("\\n").filter(Boolean)', True)], "branches": ["steps"]},
    "wait": {"label": "Wait", "help": "Pause (up to 300 s). Output: unchanged.", "fields": [("seconds", "Seconds", "num", "5", True)]},
    "notify": {"label": "Notify me", "help": "A notification (every notifier: the API's event stream, the terminal…). Output: unchanged.",
               "fields": [("title", "Title", "tpl", "{{ $flow.name }}", False), ("body", "Message", "area", "{{ $json }}", False)]},
    "playbook": {"label": "Playbook", "help": "Runs a saved, approved playbook (no model). Output: its result {ok, recap, vars, log}. A failed playbook fails the step.",
                 "fields": [("playbook", "Playbook", "pbsel", "", True), ("vars", "Variables (only ones the playbook declares)", "json", '{"city": "{{ $json.city }}"}', False)]},
    "flow": {"label": "Run flow", "help": "Runs another flow with an input. Output: that flow's output.",
             "fields": [("flow", "Flow", "flowsel", "", True), ("input", "Input (JSON or {{ }}; default: $json)", "json", "", False)]},
    "stop": {"label": "Stop", "help": "Ends the flow (successfully). Output: the value, or $json.", "fields": [("value", "Result (optional; {{ }})", "tpl", "", False)]},
}
FLOW_COMMON = ["id", "type", "name", "onError", "retries", "disabled", "then", "else", "steps", "cases", "default", "note"]


class FlowStop(Exception):
    def __init__(self, value):
        super().__init__("stop")
        self.value = value


class FlowStepError(Exception):
    pass


def safe_name(n) -> str:
    s = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]]+', "-", str(n or "").strip()))[:80]
    return s or "Flow"


def walk(steps, fn, path: str = "steps") -> None:
    for i, st in enumerate(steps if isinstance(steps, list) else []):
        p = f"{path}.{i}"
        fn(st, p)
        if not isinstance(st, dict):
            continue
        for k in ("then", "else", "steps", "default"):
            if isinstance(st.get(k), list):
                walk(st[k], fn, f"{p}.{k}")
        if isinstance(st.get("cases"), list):
            for j, c in enumerate(st["cases"]):
                walk(c.get("steps") if isinstance(c, dict) else None, fn, f"{p}.cases.{j}.steps")


def count(steps) -> int:
    n = [0]
    walk(steps, lambda *_: n.__setitem__(0, n[0] + 1))
    return n[0]


def has_agent(steps) -> bool:
    y = [False]
    walk(steps, lambda st, _p: y.__setitem__(0, y[0] or (isinstance(st, dict) and st.get("type") == "agent" and not st.get("disabled"))))
    return y[0]


def clean(f: dict) -> dict:
    """A copy without private (_) keys, with a unique id on every step and the keys in the app's order."""
    def strip(v):
        if isinstance(v, list):
            return [strip(x) for x in v]
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if not str(k).startswith("_")}
        return v
    out = strip(f)
    out["steps"] = out["steps"] if isinstance(out.get("steps"), list) else []
    used: set[str] = set()

    def dedupe(st, _p):
        if isinstance(st, dict) and st.get("id"):
            if str(st["id"]) in used:
                del st["id"]
            else:
                used.add(str(st["id"]))
    walk(out["steps"], dedupe)
    n = [0]

    def number(st, _p):
        if isinstance(st, dict) and not st.get("id"):
            while True:
                n[0] += 1
                if f"s{n[0]}" not in used:
                    break
            st["id"] = f"s{n[0]}"
            used.add(st["id"])
    walk(out["steps"], number)
    order = ["name", "description", "enabled", "trigger", "vars", "timeout", "steps"]
    return {**{k: out[k] for k in order if k in out}, **{k: v for k, v in out.items() if k not in order}}


def _is_num(v) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return float(v) == float(v) and abs(float(v)) != float("inf")
    except (TypeError, ValueError):
        return False


def validate(f, tool_names: list[str] | None = None, now: datetime | None = None) -> list[str]:
    if not isinstance(f, dict):
        return ["the flow must be a JSON object"]
    if f.get("_bad"):
        return [f"the file is not valid JSON: {f['_bad']}"]
    errs: list[str] = []
    if not str(f.get("name") or "").strip():
        errs.append('the flow needs a "name"')
    if not isinstance(f.get("steps"), list):
        errs.append('"steps" must be a list of steps')
    tr = {"type": "manual"} if f.get("trigger") is None else f.get("trigger")
    if not isinstance(tr, dict):
        errs.append('"trigger" must be an object like {"type": "manual"}')
    else:
        t = tr.get("type") or "manual"
        if t not in ("manual", "schedule", "note"):
            errs.append("trigger.type must be manual, schedule or note")
        if t == "schedule":
            try:
                if "once" in C.from_text(str(tr.get("when") or ""), now or datetime.now().astimezone()):
                    errs.append('trigger.when must repeat (like "every day at 9am" or a cron line), not one date')
            except Exception as e:
                errs.append(f"trigger.when: {e}")
        if t == "note" and not str(tr.get("path") or "").strip():
            errs.append("trigger.path: which note or folder to watch")
    if f.get("vars") is not None and not isinstance(f.get("vars"), dict):
        errs.append('"vars" must be an object')
    ids: set[str] = set()

    def check(st, p):
        if not isinstance(st, dict):
            errs.append(f"{p}: each step must be an object")
            return
        where = f"{p} ({st.get('name') or st.get('id') or st.get('type') or '?'})"
        T = FLOW_TYPES.get(st.get("type"))
        if not T:
            errs.append(f'{where}: unknown type "{st.get("type")}". Types: {", ".join(FLOW_TYPES)}')
            return
        if st.get("id") is not None:
            if str(st["id"]) in ids:
                errs.append(f"{where}: the id {st['id']} is used twice")
            ids.add(str(st["id"]))
        for k, _label, kind, _ph, req in T["fields"]:
            v = st.get(k)
            if req and (v is None or (isinstance(v, str) and not v.strip())):
                errs.append(f'{where}: "{k}" is required')
            if kind == "json" and isinstance(v, str) and "{{" not in v:
                errs.append(f'{where}: "{k}" must be JSON (an object or list), or a {{{{ }}}} expression')
            if kind == "num" and v not in (None, "") and not _is_num(v):
                errs.append(f'{where}: "{k}" must be a number')
            if kind.startswith("sel:") and v not in (None, "") and v not in kind[4:].split("|"):
                errs.append(f'{where}: "{k}" must be one of {", ".join(kind[4:].split("|"))}')
        if st["type"] == "tool" and st.get("tool"):
            if st["tool"] == "flow":
                errs.append(f'{where}: use a "flow" step to run another flow')
            elif tool_names is not None and st["tool"] not in tool_names and not str(st["tool"]).startswith("mcp__"):
                errs.append(f'{where}: no tool named "{st["tool"]}"')
        if st["type"] == "tool" and st.get("args") is not None and not isinstance(st["args"], dict):
            errs.append(f'{where}: "args" must be an object')
        if st["type"] == "set" and st.get("fields") is not None and not isinstance(st["fields"], dict):
            errs.append(f'{where}: "fields" must be an object')
        if st["type"] == "switch" and st.get("cases") is not None and (not isinstance(st["cases"], list) or any(not isinstance(c, dict) for c in st["cases"])):
            errs.append(f'{where}: "cases" must be a list of {{"match": value, "steps": [...]}}')
        for k in ("then", "else", "steps", "default"):
            if st.get(k) is not None and not isinstance(st[k], list):
                errs.append(f'{where}: "{k}" must be a list of steps')
        if st.get("onError") is not None and st["onError"] not in ("stop", "continue"):
            errs.append(f'{where}: onError must be "stop" or "continue"')
        if st.get("retries") is not None:
            try:
                ok = int(st["retries"]) == float(st["retries"]) and 0 <= int(st["retries"]) <= 3
            except (TypeError, ValueError):
                ok = False
            if not ok:
                errs.append(f"{where}: retries must be 0-3")
    walk(f.get("steps"), check)
    return errs


def clip(v, n: int = 2000):
    if v is None:
        return None
    try:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(v)
    if len(s) <= n:
        return v if isinstance(v, str) else json.loads(s)
    return s[:n] + f"… [{len(s) - n} more characters]"


def lines(v) -> list[str]:
    arr = v if isinstance(v, list) else v.split("\n") if isinstance(v, str) else [] if v is None else [v]
    return [x for x in ((s.strip() if isinstance(s, str) else json.dumps(s)) for s in arr) if x]


def match(m, v) -> bool:
    if isinstance(m, list):
        return any(match(x, v) for x in m)
    if m == v and type(m) is type(v):
        return True
    if _js_str(m) == _js_str(v):
        return True
    return isinstance(m, (dict, list)) and json.dumps(m, sort_keys=False) == json.dumps(v, sort_keys=False)


def _js_str(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return "[object Object]" if isinstance(v, dict) else ",".join(_js_str(x) for x in v)
    return str(v)


def _dumps(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


class Run(dict):
    """One run: id, flow, path, via, started, ended, status, input, output, error, steps (records)."""


class Flows:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self._eval = None
        self.active: dict[str, Run] = {}
        self.busy: set[str] = set()
        self.next: dict[str, float | None] = {}
        self.sig: dict[str, str] = {}
        self.runs_path = jade.settings.data_path / "flow_runs.json"

    # ------------------------------------------------------------ storage
    def files(self) -> list[str]:
        v = self.jade.vault
        if not v.is_dir(FLOW_DIR):
            return []
        return sorted(f"{FLOW_DIR}/{n}" for n in os.listdir(v.real(FLOW_DIR)) if n.endswith(FLOW_EXT) and v.is_file(f"{FLOW_DIR}/{n}"))

    def read(self, p: str) -> dict:
        base = p.rsplit("/", 1)[-1][: -len(FLOW_EXT)]
        try:
            f = json.loads(self.jade.vault.read(p))
            if not isinstance(f, dict):
                raise ValueError("not a JSON object")
            return {**f, "name": str(f.get("name") or base), "_path": p}
        except ValueError as e:
            return {"name": base, "_path": p, "_bad": str(e), "steps": []}

    def list(self) -> list[dict]:
        return [self.read(p) for p in self.files()]

    def find(self, name) -> dict | None:
        k = str(name if name is not None else "").strip().lower()
        if not k:
            return None
        allf = self.list()
        for f in allf:
            if f["name"].lower() == k:
                return f
        for f in allf:
            b = f["_path"].rsplit("/", 1)[-1].lower()
            if k in (f["_path"].lower(), b, b[: -len(FLOW_EXT)]) or b == k + FLOW_EXT:
                return f
        return None

    def get(self, name) -> dict:
        f = self.find(name)
        if not f:
            raise ToolError(f'no flow named "{name or ""}". Use action list.')
        return f

    def save(self, f: dict, old_path: str | None = None) -> dict:
        c = clean(f)
        c["name"] = str(c.get("name") or "").strip() or "Flow"
        p = f"{FLOW_DIR}/{safe_name(c['name'])}{FLOW_EXT}"
        self.jade.vault.write(p, json.dumps(c, indent=2, ensure_ascii=False) + "\n")
        if old_path and old_path != p and self.jade.vault.is_file(old_path):
            self.jade.vault.remove(old_path)
        self.changed()
        return {**c, "_path": p}

    def delete(self, f: dict) -> None:
        if self.jade.vault.is_file(f["_path"]):
            self.jade.vault.remove(f["_path"])
        self.changed()

    def changed(self) -> None:
        self.next.clear()

    def tool_names(self) -> list[str]:
        return [n for n in self.jade.tools.names() if n != "flow"]

    def validate(self, f) -> list[str]:
        return validate(f, self.tool_names(), self.jade.now())

    # ------------------------------------------------------------ run records
    def recent(self) -> list[dict]:
        try:
            return json.loads(self.runs_path.read_text())
        except (OSError, ValueError):
            return []

    def _record(self, run: Run) -> None:
        copy = {k: v for k, v in run.items() if k not in ("stop",)}
        copy["input"], copy["output"], copy["steps"] = clip(run["input"], 4000), clip(run["output"], 8000), run["steps"][:300]
        rows = [copy] + [r for r in self.recent() if r.get("id") != run["id"]]
        try:
            self.runs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.runs_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows[:100], ensure_ascii=False, default=str))
            os.replace(tmp, self.runs_path)
        except OSError as e:
            log.warning("could not save the flow run: %s", e)

    def stop(self, run_id: str) -> bool:
        r = self.active.get(run_id)
        if r:
            r["stop"] = True
        return bool(r)

    # ------------------------------------------------------------ expressions
    @property
    def evaluator(self):
        if self._eval is None:
            from .node import Evaluator
            self._eval = Evaluator()
        return self._eval

    async def aclose(self):
        if self._eval:
            await self._eval.close()
            self._eval = None

    def view(self, ctx: dict) -> dict:
        d = self.jade.now()
        return {**ctx, "$now": d.isoformat(timespec="milliseconds"), "$today": d.strftime("%Y-%m-%d")}

    async def resolve(self, st: dict, ctx: dict) -> dict:
        T = FLOW_TYPES[st["type"]]
        jobs: list[dict] = []
        sets: list = []
        out: dict = {}

        def add(src, mode, setter):
            jobs.append({"src": src, "mode": mode})
            sets.append(setter)

        def walkv(v, setter):
            if isinstance(v, str):
                if "{{" in v:
                    add(v, "tpl", setter)
                else:
                    setter(v)
            elif isinstance(v, list):
                a = list(v)
                setter(a)
                for i, x in enumerate(a):
                    walkv(x, lambda y, i=i: a.__setitem__(i, y))
            elif isinstance(v, dict):
                o = dict(v)
                setter(o)
                for k in list(o):
                    walkv(o[k], lambda y, k=k: o.__setitem__(k, y))
            else:
                setter(v)

        for k, _l, kind, _ph, _r in T["fields"]:
            v = st.get(k)
            if v is None:
                continue
            if kind == "expr":
                src = re.sub(r"^\{\{([\s\S]*)\}\}$", r"\1", str(v).strip()).strip()
                if src:
                    add(src, "expr", lambda x, k=k: out.__setitem__(k, x))
            elif kind in ("code", "bool", "num", "tool", "flowsel", "pbsel") or kind.startswith("sel:"):
                out[k] = v
            else:
                walkv(v, lambda x, k=k: out.__setitem__(k, x))
        if jobs:
            vals = await self.evaluator.run(jobs, self.view(ctx))
            for s, x in zip(sets, vals):
                s(x)
        return out

    # ------------------------------------------------------------ running
    async def run_by_name(self, name, input=None, *, via: str = "manual", depth: int = 0) -> Run:   # noqa: A002
        f = self.find(name)
        if not f:
            raise ToolError(f'no flow named "{name}"')
        return await self.run(f, input, via=via, depth=depth)

    async def run(self, flow: dict, input=None, *, via: str = "manual", depth: int = 0, on_step=None) -> Run:   # noqa: A002
        """Run a flow. Returns the run record (status ok | error | stopped; output; steps)."""
        run = Run(id=uuid.uuid4().hex[:12], flow=flow.get("name"), path=flow.get("_path"), via=via, started=time.time(), ended=0.0,
                  status="running", input=input, output=None, error=None, steps=[], stop=False)
        errs = self.validate(flow)
        if errs or depth > FLOW_MAX_DEPTH:
            run.update(status="error", error="the flow is not valid: " + "; ".join(errs) if errs else "flows call each other too deeply", ended=time.time())
            self._record(run)
            return run
        self.active[run["id"]] = run
        ctx = {"$input": input, "$json": input, "$node": {}, "$vars": flow["vars"] if isinstance(flow.get("vars"), dict) else {},
               "$flow": {"name": flow["name"]}, "$run": {"id": run["id"], "via": via}, "$item": None, "$index": None}
        try:
            limit = min(3600, max(5, float(flow.get("timeout") or 600)))
        except (TypeError, ValueError):
            limit = 600
        st = {"run": run, "flow": flow, "depth": depth, "deadline": time.time() + limit, "count": 0, "level": 0, "on_step": on_step, "in_chat": via == "jade"}
        try:
            run["output"] = await self.seq(flow.get("steps") or [], ctx, st)
            run["status"] = "ok"
        except FlowStop as e:
            run.update(output=e.value, status="ok", stopped=True)
        except asyncio.CancelledError:
            run.update(status="stopped", error="cancelled")
            raise
        except Exception as e:
            run.update(status="stopped" if run["stop"] else "error", error=str(e))
        finally:
            run["ended"] = time.time()
            self.active.pop(run["id"], None)
            run.pop("stop", None)
            self._record(run)
        return run

    async def seq_in(self, steps, ctx, st):
        st["level"] += 1
        try:
            return await self.seq(steps, ctx, st)
        finally:
            st["level"] -= 1

    async def seq(self, steps, ctx, st):
        last = ctx["$json"]
        for step in steps or []:
            if st["run"].get("stop"):
                raise RuntimeError("stopped")
            if time.time() > st["deadline"]:
                raise RuntimeError(f"the flow ran past its time limit ({round(st['deadline'] - st['run']['started'])} s)")
            st["count"] += 1
            if st["count"] > FLOW_MAX_STEPS:
                raise RuntimeError("too many steps ran (an endless loop?)")
            last = await self.step(step, ctx, st)
            ctx["$json"] = last
        return last

    def _ui(self, st):
        if st.get("on_step"):
            try:
                st["on_step"](st["run"])
            except Exception:
                pass

    async def step(self, step: dict, ctx: dict, st: dict):
        label = str(step.get("name") or step.get("id") or step.get("type"))
        rec = {"id": step.get("id"), "name": label, "type": step.get("type"), "depth": st["level"], "status": "running", "started": time.time(), "ms": 0, "input": clip(ctx["$json"], 1500)}
        st["run"]["steps"].append(rec)
        self._ui(st)

        def keep(out):
            if step.get("name"):
                ctx["$node"][step["name"]] = out
            if step.get("id"):
                ctx["$node"][step["id"]] = out

        def ms():
            return int((time.time() - rec["started"]) * 1000)

        if step.get("disabled"):
            rec.update(status="skipped", output=rec["input"])
            keep(ctx["$json"])
            return ctx["$json"]
        container = step.get("type") in ("if", "switch", "each")
        try:
            retries = int(step.get("retries") or 0)
        except (TypeError, ValueError):
            retries = 0
        tries = 1 if container else min(3, max(0, retries)) + 1
        err: BaseException | None = None
        for a in range(tries):
            try:
                out = await self.exec(step, ctx, st, rec)
                rec.update(status="ok", ms=ms(), output=clip(out, 1500))
                keep(out)
                self._ui(st)
                return out
            except FlowStop as e:
                rec.update(status="ok", ms=ms(), output=clip(e.value, 1500), note="stopped the flow")
                self._ui(st)
                raise
            except Exception as e:
                err = e
                if isinstance(e, FlowStepError) or st["run"].get("stop"):
                    break
                if a < tries - 1:
                    rec["note"] = f"retry {a + 1}"
                    self._ui(st)
                    await asyncio.sleep(0.8 * (a + 1))
        rec.update(ms=ms(), status="error")
        if isinstance(err, FlowStepError):
            rec["error"] = "a step inside failed"
            self._ui(st)
            raise err
        rec["error"] = str(err)
        if step.get("onError") == "continue" and not st["run"].get("stop"):
            rec["note"] = "continued"
            out = {"error": rec["error"]}
            keep(out)
            self._ui(st)
            return out
        self._ui(st)
        raise FlowStepError(f'step "{label}" failed: {rec["error"]}')

    def _redact(self, s: str) -> str:
        for v in sorted((str(x) for x in self.jade.settings.secrets.values() if x and len(str(x)) >= 4), key=len, reverse=True):
            s = s.replace(v, "••••")
        return s

    async def exec(self, step: dict, ctx: dict, st: dict, rec: dict):
        j = self.jade
        p = await self.resolve(step, ctx)
        t = step["type"]
        flow = st["flow"]

        def secs(v, default=60, hi=600):
            try:
                return min(hi, max(1, float(v or default)))
            except (TypeError, ValueError):
                return default

        if t == "tool":
            name = str(p.get("tool") or "")
            if name == "flow":
                raise RuntimeError('use a "flow" step to run another flow')
            if not j.tools.get(name):
                raise RuntimeError(f'the tool "{name}" is not available right now')
            r: Any = await j.run_tool(name, p["args"] if isinstance(p.get("args"), dict) else {})
            if p.get("parse") and isinstance(r, str):
                try:
                    r = json.loads(r)
                except ValueError:
                    m = re.search(r"[\[{][\s\S]*[\]}]", r)
                    if not m:
                        raise RuntimeError("the result is not JSON") from None
                    r = json.loads(m.group(0))
            return r
        if t == "js":
            from .node import run_script

            async def rpc(op, a):
                return await jade_bridge(j, op, a, depth=st["depth"], via=f"flow:{flow['name']}")
            r = await run_script(j, str(p.get("code") or ""), globals_=self.view(ctx), timeout=secs(p.get("timeout")), rpc=rpc)
            if r["out"].strip() or r["err"].strip():
                rec["log"] = self._redact(r["out"] + r["err"])[-3000:]
            if r["code"] != 0:
                err = "\n".join(ln for ln in r["err"].split("\n") if not re.match(r"^\s+at\s", ln)).strip()
                raise RuntimeError((err or f"exit code {r['code']}")[-800:])
            if r["ret"] is not None:
                return r["ret"]
            return r["out"].removesuffix("\n") if r["out"] else ctx["$json"]
        if t == "shell":
            from .tools.builtin import run_shell
            cwd = j.vault.real(str(p.get("cwd") or "/"))
            out, code = await run_shell(j, str(p.get("command") or ""), timeout=secs(p.get("timeout")), cwd=str(cwd))
            rec["log"] = self._redact(out)[-3000:]
            if code != 0 and not p.get("allowFail"):
                raise RuntimeError(f"exit {code}" + (f": {out.strip()[-500:]}" if out.strip() else ""))
            return {"code": code, "stdout": out, "stderr": ""}
        if t == "decide":
            from .reasoning import decide_data
            return await decide_data(j, p.get("state"), p.get("question") or "", p.get("kind") or "choice", lines(p.get("options")))
        if t == "llm":
            return await ask_once(j, _dumps(p.get("prompt")), str(p.get("system") or ""), bool(p.get("json")))
        if t == "agent":
            if st.get("in_chat"):
                raise RuntimeError("Jade is busy")
            async with j.background:
                return await run_agent_prompt(j, _dumps(p.get("prompt")), "same" if p.get("chat") == "same" else "new",
                                              key=f"flow:{flow['name']}:{step.get('id') or ''}", title=f"⚙ {flow['name']}")
        if t == "note":
            return self.note_op(p)
        if t == "set":
            f = p["fields"] if isinstance(p.get("fields"), dict) else {}
            return {**ctx["$json"], **f} if p.get("keep") and isinstance(ctx["$json"], dict) else f
        if t == "if":
            ok = _truthy(p.get("condition"))
            rec["note"] = "then" if ok else "else"
            self._ui(st)
            br = step.get("then") if ok else step.get("else")
            return await self.seq_in(br, ctx, st) if isinstance(br, list) and br else ctx["$json"]
        if t == "switch":
            cases = step["cases"] if isinstance(step.get("cases"), list) else []
            i = next((n for n, c in enumerate(cases) if match(c.get("match"), p.get("value"))), -1)
            rec["note"] = f"case {json.dumps(cases[i].get('match'))}" if i >= 0 else "default"
            self._ui(st)
            br = cases[i].get("steps") if i >= 0 else step.get("default")
            return await self.seq_in(br, ctx, st) if isinstance(br, list) and br else ctx["$json"]
        if t == "each":
            items = p.get("items")
            if isinstance(items, str):
                items = [s for s in items.split("\n") if s.strip()]
            elif isinstance(items, dict):
                items = [{"key": k, "value": v} for k, v in items.items()]
            if not isinstance(items, list):
                raise RuntimeError('"items" must give a list')
            if len(items) > 1000:
                raise RuntimeError(f"too many items ({len(items)}; at most 1000)")
            save = (ctx["$item"], ctx["$index"])
            out = []
            rec["note"] = f"{len(items)} item{'' if len(items) == 1 else 's'}"
            try:
                for i, it in enumerate(items):
                    ctx["$item"], ctx["$index"], ctx["$json"] = it, i, it
                    out.append(await self.seq_in(step.get("steps") or [], ctx, st))
            finally:
                ctx["$item"], ctx["$index"] = save
            return out
        if t == "wait":
            try:
                end = time.time() + min(300, max(0, float(p.get("seconds") or 0)))
            except (TypeError, ValueError):
                end = time.time()
            while time.time() < end:
                if st["run"].get("stop"):
                    raise RuntimeError("stopped")
                await asyncio.sleep(min(0.25, end - time.time()))
            return ctx["$json"]
        if t == "notify":
            title = str(p.get("title") if p.get("title") is not None else "") or flow["name"]
            body = "" if p.get("body") is None else _dumps(p["body"])
            j.notify(title, body)
            return ctx["$json"]
        if t == "flow":
            sub = self.find(p.get("flow"))
            if not sub:
                raise RuntimeError(f'no flow named "{p.get("flow")}"')
            if st["depth"] + 1 > FLOW_MAX_DEPTH:
                raise RuntimeError("flows call each other too deeply")
            r = await self.run(sub, ctx["$json"] if "input" not in p else p["input"], via=f"flow:{flow['name']}", depth=st["depth"] + 1)
            if r["status"] != "ok":
                raise RuntimeError(f'flow "{sub["name"]}" {r["status"]}: {r["error"]}')
            return r["output"]
        if t == "playbook":
            if not hasattr(j, "playbooks"):
                raise RuntimeError("playbooks are not available")
            r = await j.playbooks.run_unattended(p.get("playbook"), p.get("vars") or {}, via=f"flow:{flow['name']}")
            if not r.get("ok"):
                fl = r.get("failed") or {}
                raise RuntimeError(f'playbook "{p.get("playbook")}" failed at {fl.get("name") or "task " + str(fl.get("task", "?"))}: {fl.get("error") or "error"}')
            return r
        if t == "stop":
            v = p.get("value")
            raise FlowStop(ctx["$json"] if v is None or v == "" else v)
        raise RuntimeError(f"unknown step type {t}")

    def note_op(self, p: dict):
        v = self.jade.vault
        act = p.get("action") or "read"
        raw = str(p.get("path") if p.get("path") is not None else "").strip()
        if not raw:
            raise RuntimeError('"path" is empty')
        if act == "read":
            r = v.norm(raw) if v.is_file(v.norm(raw)) else v.resolve(raw)
            if not r:
                raise RuntimeError(f'no note "{raw}"')
            return v.read(r)
        full = v.norm(raw) if v.is_file(v.norm(raw)) else (v.resolve(raw) or v.norm(raw if raw.startswith("/") else "/" + raw))
        if not re.search(r"\.[A-Za-z0-9]+$", full):
            full += ".md"
        c = p.get("content")
        text = "" if c is None else c if isinstance(c, str) else json.dumps(c, indent=2, ensure_ascii=False)
        prev = v.read(full) if v.is_file(full) else ""
        nl = "" if text.endswith("\n") else "\n"
        if act == "write":
            v.write(full, text)
        elif act == "append":
            v.write(full, prev + ("\n" if prev and not prev.endswith("\n") else "") + text + nl)
        elif act == "prepend":
            m = re.match(r"^---\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)", prev)
            head = m.group(0) if m else ""
            v.write(full, head + text + nl + prev[len(head):])
        else:
            raise RuntimeError("action must be read, write, append or prepend")
        return {"path": full, "bytes": len(v.read_bytes(full))}

    # ------------------------------------------------------------ triggers
    def trigger_text(self, f: dict) -> str:
        tr = f.get("trigger") or {}
        off = " · off" if f.get("enabled") is False else ""
        if tr.get("type") == "schedule":
            d = str(tr.get("when") or "")
            try:
                sp = C.from_text(d, self.jade.now())
                if sp.get("cron"):
                    d = C.describe(sp["cron"])
            except Exception:
                pass
            n = self.next.get(f["_path"])
            return f"⏰ {d}{' · next ' + datetime.fromtimestamp(n).astimezone().strftime('%a %b %-d %H:%M') if n and f.get('enabled') is not False else ''}{off}"
        if tr.get("type") == "note":
            return f"✎ when {tr.get('path') or '?'} changes{off}"
        return "Manual"

    def watch_path(self, spec) -> str:
        raw = str(spec or "").strip()
        if not raw:
            return ""
        v = self.jade.vault
        n = v.norm(raw if raw.startswith("/") else "/" + raw)
        return n if v.exists(n) else (v.resolve(raw) or n)

    def watch_sig(self, spec) -> str:
        p = self.watch_path(spec)
        if not p:
            return ""
        v = self.jade.vault
        if v.is_dir(p):
            out = []
            root = v.real(p)
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if not d.startswith(".")]
                for fn in fns:
                    full = os.path.join(dp, fn)
                    vp = v.vpath(full)
                    if vp.startswith(FLOW_DIR + "/") or vp == CRON_LOG or vp.startswith("/Jade/Logs/") or fn.endswith(".jvtmp"):
                        continue
                    try:
                        out.append(f"{vp}\u0001{os.stat(full).st_mtime_ns}")
                    except OSError:
                        pass
            return "\u0002".join(sorted(out))
        try:
            return f"{p}\u0001{v.stat(p).st_mtime_ns}" if v.is_file(p) else ""
        except OSError:
            return ""

    @staticmethod
    def sig_diff(a: str, b: str) -> dict:
        def m(s):
            return dict(x.split("\u0001", 1) for x in str(s or "").split("\u0002") if x)
        A, B = m(a), m(b)
        return {"changed": [k for k, v in B.items() if A.get(k) != v], "removed": [k for k in A if k not in B]}

    def _plan(self, f: dict, after: float) -> float | None:
        try:
            sp = C.from_text(str((f.get("trigger") or {}).get("when") or ""), self.jade.now())
            if not sp.get("cron"):
                return None
            n = C.next_after(sp["cron"], datetime.fromtimestamp(after).astimezone(self.jade.now().tzinfo))
            return n.timestamp() if n else None
        except Exception:
            return None

    def fire(self, f: dict, input, via: str):   # noqa: A002
        if f["_path"] in self.busy:
            return None
        self.busy.add(f["_path"])

        async def go():
            try:
                run = await self.run(f, input, via=via)
                if run["status"] != "ok":
                    self.jade.notify(f'Flow "{f["name"]}" failed', str(run.get("error") or "")[:180])
                return run
            except Exception as e:
                log.warning("flow %s: %s", f["name"], e)
            finally:
                self.busy.discard(f["_path"])
                if (f.get("trigger") or {}).get("type") == "note":
                    self.sig[f["_path"]] = self.watch_sig(f["trigger"].get("path"))
        return asyncio.get_running_loop().create_task(go())

    def tick(self, now: float | None = None) -> list:
        """Start schedule-triggered flows that are due. Returns the tasks started."""
        now = time.time() if now is None else now
        started = []
        for f in self.list():
            if f.get("_bad") or f.get("enabled") is False or (f.get("trigger") or {}).get("type") != "schedule":
                continue
            if f["_path"] not in self.next:
                self.next[f["_path"]] = self._plan(f, now)
                continue
            due = self.next[f["_path"]]
            if not due or now < due:
                continue
            self.next[f["_path"]] = self._plan(f, now)
            t = self.fire(f, {"trigger": "schedule", "at": datetime.fromtimestamp(due).astimezone().isoformat()}, "schedule")
            if t:
                started.append(t)
        return started

    def note_check(self) -> list:
        """Start note-triggered flows whose note or folder changed since the last check."""
        started = []
        for f in self.list():
            tr = f.get("trigger") or {}
            if f.get("_bad") or f.get("enabled") is False or tr.get("type") != "note" or f["_path"] in self.busy:
                continue
            now = self.watch_sig(tr.get("path"))
            old = self.sig.get(f["_path"])
            if old is None:
                self.sig[f["_path"]] = now
                continue
            if now == old:
                continue
            self.sig[f["_path"]] = now
            d = self.sig_diff(old, now)
            if not d["changed"] and not d["removed"]:
                continue
            path = self.watch_path(tr.get("path"))
            inp = {"trigger": "note", "path": path, **d}
            if self.jade.vault.is_file(path) and path.endswith(".md"):
                try:
                    inp["content"] = self.jade.vault.read(path)[:50000]
                except OSError:
                    pass
            t = self.fire(f, inp, "note")
            if t:
                started.append(t)
        return started

    async def loop(self, daemon) -> None:
        """The daemon loop: schedule triggers every 15 s, note triggers every 3 s (quiet when there are none)."""
        n = 0
        while not daemon.stopping.is_set():
            try:
                if n % 5 == 0:
                    self.tick()
                if any((f.get("trigger") or {}).get("type") == "note" and f.get("enabled") is not False for f in self.list()):
                    self.note_check()
            except Exception as e:
                log.warning("flow triggers: %s", e)
            n += 1
            if await daemon._sleep(3):
                return

    # ------------------------------------------------------------ text
    @staticmethod
    def run_text(run: dict) -> str:
        rows = []
        for s in run["steps"]:
            mark = {"ok": "✓", "skipped": "–", "error": "✗"}.get(s["status"], "…")
            rows.append(f"{mark} {s['name']} ({s['type']}{', ' + s['note'] if s.get('note') else ''}, {s['ms']} ms){': ' + s['error'] if s.get('error') else ''}")
        out = run.get("output")
        out = out if isinstance(out, str) else json.dumps(out, indent=1, ensure_ascii=False, default=str)
        if len(out) > 4000:
            out = out[:4000] + f"… [{len(out) - 4000} more characters]"
        return f"Flow \"{run['flow']}\" {run['status']}{': ' + run['error'] if run.get('error') else ''} in {run['ended'] - run['started']:.1f} s\n" + "\n".join(rows) + f"\nOutput: {out}"


def _truthy(v) -> bool:
    return not (v is None or v is False or v == 0 or v == "" or (isinstance(v, float) and v != v))


def help_text() -> str:
    def fdesc(f):
        k, _l, kind, _ph, req = f
        extra = (" = " + kind[4:]) if kind.startswith("sel:") else {"bool": " (true/false)", "num": " (number)", "json": " (JSON)", "expr": " (JavaScript expression)"}.get(kind, "")
        return f"{k}{'*' if req else ''}{extra}"
    types = "\n".join(
        f"- {k} ({T['label']}): {T['help']}\n  fields: {', '.join(fdesc(f) for f in T['fields'])}"
        + (f"; nested step lists: {', '.join(T['branches'])}" if T.get("branches") else "")
        + ('; cases: [{"match": value or [values], "steps": [...]}], default: [steps]' if T.get("cases") else "")
        for k, T in FLOW_TYPES.items())
    return f"""A flow is JSON (saved in /Jade/Flows/<name>.flow.json):
{{"name": "Inbox triage", "description": "…", "enabled": true,
 "trigger": {{"type": "manual"}} | {{"type": "schedule", "when": "every weekday at 9am" or "0 9 * * 1-5"}} | {{"type": "note", "path": "/Inbox.md" (a note or a folder)}},
 "vars": {{"key": "value"}}, "timeout": 600,
 "steps": [ {{"id": "s1", "type": "…", "name": "Short name", …fields…, "onError": "stop"|"continue", "retries": 0-3, "disabled": false}}, … ]}}
Steps run top to bottom; each step's output becomes $json for the next. The flow's output is the last step's output.
Expressions: any text field may contain {{{{ JavaScript }}}}. A field that is only one {{{{ }}}} keeps the value's type (object, number…); otherwise it is text.
Available: $json (previous output), $input (what the flow was started with), $node["Step name"] or $node.s1 (any earlier step's output), $vars, $item and $index (inside for-each), $now (ISO time), $today (YYYY-MM-DD), $flow.name, $run.via, and $date(format, date, days) e.g. $date("dddd D MMM", $now, 1).
"condition", "value" and "items" are plain JavaScript expressions (no braces). JavaScript steps get the same names as variables, plus fs (rooted at the vault), path, fetch and jade (jade.tool(name, args), jade.decide({{...}}), jade.ask(prompt), jade.flow(name, input), jade.shell(cmd), jade.kv.get/set) and return their output.
A note trigger starts the flow with {{trigger, path, changed: [paths], removed: [paths], content}} a few seconds after the note changes. A schedule trigger starts it with {{trigger, at}}. Triggers run while `jade daemon` or `jade serve` runs.
Step types (* = required):
{types}
Errors: a failing step stops the flow, unless "onError": "continue" (its output is then {{"error": "…"}}). "agent" steps cannot run inside your own chat turn, so do not run such flows with this tool; they run from schedules, triggers, the API or `jade flow run`.
Example:
{{"name": "Daily digest", "trigger": {{"type": "schedule", "when": "every day at 18:00"}}, "steps": [
 {{"type": "js", "name": "Changed today", "code": "const out = []; for (const f of fs.readdirSync('/', {{recursive: true}})) {{ const p = '/' + f; if (p.endsWith('.md') && fs.statSync(p).mtimeMs > Date.now() - 864e5) out.push(p); }} return out;"}},
 {{"type": "if", "condition": "$json.length > 0", "else": [{{"type": "stop", "value": "nothing changed"}}]}},
 {{"type": "llm", "name": "Summary", "prompt": "Summarise what changed today in these notes, one line each:\\n{{{{ $json.join('\\\\n') }}}}"}},
 {{"type": "note", "action": "append", "path": "/Journal/Digest.md", "content": "## {{{{ $today }}}}\\n{{{{ $json }}}}"}},
 {{"type": "notify", "title": "Digest ready", "body": "{{{{ $node['Summary'].slice(0, 120) }}}}"}}]}}"""


FLOW_SPEC = {
    "description": "Automation flows (like n8n): a list of steps that run in order — Jade's tools, JavaScript, shell commands, Ollaya decisions, AI answers, a full Jade chat turn, notes, playbooks, if / switch / for-each, wait, notify, other flows. A flow runs by hand, on a schedule, when a note changes, from scripts (jade.flow), from `jade flow run` or over the API. Actions: help (the JSON format and every step type: read it before you create or change a flow), list, get, create, update, delete, run, runs, enable, disable.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["help", "list", "get", "create", "update", "delete", "run", "runs", "enable", "disable"]},
        "name": {"type": "string", "description": "The flow's name (get, update, delete, run, runs, enable, disable)"},
        "flow": {"type": "object", "description": "create / update: the whole flow as JSON (see help). update replaces the flow; to rename, give the new name here and the old one in \"name\""},
        "input": {"type": "object", "description": "run: input data for the flow (it is $input and the first $json)"},
        "limit": {"type": "number", "description": "runs: how many (default 10)"},
        "unattended": {"type": "boolean", "description": "run: started by a schedule, not from a chat (allows agent steps)"}}, "required": ["action"]},
}


def _ago(ts: float) -> str:
    d = time.time() - ts
    return "just now" if d < 60 else f"{int(d // 60)} min ago" if d < 3600 else f"{int(d // 3600)} h ago" if d < 86400 else f"{int(d // 86400)} d ago"


async def flow_tool(ctx, action: str = "list", name: str = "", flow=None, input=None, limit=10, unattended: bool = False, **kw):   # noqa: A002
    F: Flows = ctx.jade.flows
    act = str(action or "list")

    def parsed():
        f = flow
        if isinstance(f, str):
            try:
                f = json.loads(f)
            except ValueError as e:
                raise ToolError(f'"flow" is not valid JSON: {e}') from None
        if not isinstance(f, dict):
            raise ToolError('give the whole flow in "flow" (see action help)')
        return f

    def target():
        return F.get(name or (flow.get("name") if isinstance(flow, dict) else ""))

    if act == "help":
        return help_text()
    if act == "list":
        allf = F.list()
        if not allf:
            return "No flows yet. Use action help, then create."
        recent = F.recent()
        rows = []
        for f in allf:
            last = next((r for r in recent if r.get("path") == f["_path"]), None)
            rows.append(f"- {f['name']}{' (off)' if f.get('enabled') is False else ''}: {'INVALID: ' + f['_bad'] if f.get('_bad') else F.trigger_text(f)} · {len(f.get('steps') or [])} steps"
                        f"{' · ' + f['description'] if f.get('description') else ''}{' · last run ' + last['status'] + ' ' + _ago(last['started']) if last else ''}")
        return "\n".join(rows)
    if act == "get":
        return json.dumps(clean(target()), indent=2, ensure_ascii=False)
    if act == "create":
        f = parsed()
        errs = F.validate(f)
        if errs:
            raise ToolError("not saved, fix these:\n- " + "\n- ".join(errs))
        if F.find(f["name"]):
            raise ToolError(f'a flow named "{f["name"]}" already exists: use update')
        s = F.save(f)
        return f'Created flow "{s["name"]}" ({s["_path"]}) with {count(s["steps"])} steps. Trigger: {F.trigger_text(s)}.'
    if act == "update":
        old = F.get(name or (flow.get("name") if isinstance(flow, dict) else ""))
        f = parsed()
        if not f.get("name"):
            f["name"] = old["name"]
        errs = F.validate(f)
        if errs:
            raise ToolError("not saved, fix these:\n- " + "\n- ".join(errs))
        clash = F.find(f["name"])
        if clash and clash["_path"] != old["_path"]:
            raise ToolError(f'another flow is already named "{f["name"]}"')
        s = F.save(f, old["_path"])
        return f'Updated flow "{s["name"]}" ({count(s["steps"])} steps). Trigger: {F.trigger_text(s)}.'
    if act == "delete":
        f = target()
        F.delete(f)
        return f'Deleted flow "{f["name"]}".'
    if act in ("enable", "disable"):
        f = target()
        f["enabled"] = act == "enable"
        F.save(f, f["_path"])
        return f"{f['name']} is {act}d."
    if act == "run":
        f = target()
        in_chat = ctx.session is not None and not unattended
        if in_chat and has_agent(f.get("steps")):
            raise ToolError("this flow has a Jade agent step, which cannot run inside your chat turn. It can run from a schedule, a trigger, the API or `jade flow run`.")
        run = await F.run(f, input, via="schedule" if unattended else "jade" if in_chat else "manual")
        text = F.run_text(run)
        if run["status"] != "ok" and unattended:
            raise ToolError(text)
        return text
    if act == "runs":
        f = target() if name else None
        try:
            n = min(50, int(limit or 10))
        except (TypeError, ValueError):
            n = 10
        rows = [r for r in F.recent() if not f or r.get("path") == f["_path"]][:n]
        if not rows:
            return "No runs yet."
        return "\n".join(f"{datetime.fromtimestamp(r['started']).strftime('%Y-%m-%d %H:%M:%S')} {r['flow']}: {r['status']}{' (' + str(r['error'])[:160] + ')' if r.get('error') else ''}"
                         f" via {r['via']}, {r['ended'] - r['started']:.1f} s" for r in rows)
    raise ToolError(f"unknown action {act}")


def setup(jade: "Jade") -> None:
    jade.flows = Flows(jade)
    jade.tools.add(Tool("flow", FLOW_SPEC["description"], FLOW_SPEC["parameters"], flow_tool, core=False,
                        hints="flow flows automation automate workflow n8n zapier pipeline trigger steps when note changes",
                        summary="flow: n8n-style automations (tools, JavaScript, shell, AI, if/switch/each, notes) run by hand, on a schedule or when a note changes; help first"))
    jade.daemon_loops.append(lambda daemon: jade.flows.loop(daemon))
