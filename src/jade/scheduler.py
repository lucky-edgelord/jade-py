"""Things that run on their own: schedules (a prompt, a shell command, a playbook or a flow at set times) and the
heartbeat (every N minutes inside active hours Jade reads /Jade/HEARTBEAT.md and acts on it; HEARTBEAT_OK stays
silent). `jade daemon` (and `jade serve`) run them; Daemon(jade).run() does the same from your own code.

Schedules live in <data>/schedules.json. Output of commands goes to /Jade/Logs/cron.log. Notifications go through
jade.notify(title, body) (see Jade.notifiers)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from . import cron as C
from .prompt import strip_comments
from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade")
CRON_LOG = "/Jade/Logs/cron.log"
HB_PATH = "/Jade/HEARTBEAT.md"
HB_DEFAULT_PROMPT = "Read HEARTBEAT.md (below). Follow it strictly. Do not infer or repeat old tasks from prior chats. Use your tools to check what it asks. If nothing needs attention, reply HEARTBEAT_OK."
MAX_SCHEDULES = 100


class Scheduler:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.path = jade.settings.data_path / "schedules.json"
        self.list: list[dict] = self._load()
        self.busy: set[str] = set()

    # ---------------------------------------------------------------- storage
    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.list, indent=1))
        tmp.replace(self.path)

    # ---------------------------------------------------------------- timing
    def now(self) -> datetime:
        return self.jade.now()

    def spec(self, when: str) -> dict:
        s = C.from_text(when, self.now())
        if "once" in s:
            if s["once"].timestamp() < time.time() - 60:
                raise ToolError("that time has already passed")
            return {"cron": "", "once": True, "run_at": s["once"].timestamp(), "done": False, "when_text": str(when).strip()}
        if not C.upcoming(s["cron"], 1, self.now()):
            raise ToolError("that schedule never matches a real date")
        return {"cron": s["cron"], "once": False, "run_at": 0, "done": False, "when_text": str(when).strip()}

    def plan(self, s: dict, after: datetime | None = None) -> None:
        if s.get("once"):
            s["next_run"] = s["run_at"] if s.get("enabled") and not s.get("done") else None
            return
        n = C.next_after(s["cron"], after or self.now())
        s["next_run"] = n.timestamp() if n else None

    def when(self, s: dict) -> str:
        return f"once · {self._clock(s['run_at'])}" if s.get("once") else C.describe(s["cron"])

    def _clock(self, ts: float | None) -> str:
        if not ts:
            return "never"
        return datetime.fromtimestamp(ts, self.now().tzinfo).strftime("%a %-d %b %H:%M")

    # ---------------------------------------------------------------- editing
    def add(self, *, when: str, prompt: str = "", name: str = "", chat: str = "new", command: str = "", playbook: str = "", flow: str = "",
            input=None, catch_up: bool = True, enabled: bool = True, src: str = "") -> dict:   # noqa: A002
        kind = "shell" if command else "playbook" if playbook else "flow" if flow else "prompt"
        if kind == "prompt" and not str(prompt).strip():
            raise ToolError("the prompt is empty: say what should happen at that time")
        if len(self.list) >= MAX_SCHEDULES:
            raise ToolError(f"there are already {MAX_SCHEDULES} schedules; delete one first")
        label = prompt if kind == "prompt" else f"$ {command}" if kind == "shell" else f"playbook {playbook}" if kind == "playbook" else f"flow {flow}"
        s = {"id": uuid.uuid4().hex[:8], "name": (name or label).strip()[:60], **self.spec(when), "kind": kind, "prompt": label if kind != "prompt" else prompt,
             "chat": "same" if chat == "same" else "new", "catch_up": bool(catch_up), "enabled": bool(enabled), "created": time.time(), "last_run": 0, "last_status": "", "src": src}
        if kind == "shell":
            s["command"] = command
        if kind == "playbook":
            s["playbook"], s["input"] = playbook, input
        if kind == "flow":
            s["flow"], s["input"] = flow, input
        self.plan(s)
        self.list.append(s)
        self.save()
        return s

    def find(self, sid: str) -> dict:
        sid = str(sid or "").lstrip("#")
        for s in self.list:
            if s["id"] == sid:
                return s
        raise ToolError(f'no schedule with id {sid}. Use action "list" to see ids.')

    def show(self, s: dict) -> str:
        return (f"#{s['id']} {'on ' if s['enabled'] else 'off'} \"{s['name']}\" {'one-time' if s.get('once') else s['cron']} ({self.when(s)}) next {self._clock(s.get('next_run'))}"
                + (f" | last {self._clock(s['last_run'])} {s['last_status']}" if s.get("last_run") else "") + f"\n   {s['kind']}: {s['prompt'][:160]}")

    # ---------------------------------------------------------------- running
    def catch_up(self) -> list[dict]:
        """Schedules that were due while nothing was running: run them now (catch_up) or mark them missed."""
        now, due = time.time(), []
        for s in self.list:
            if not s.get("enabled"):
                continue
            if s.get("next_run") and s["next_run"] <= now:
                if s.get("catch_up"):
                    due.append(s)
                    s["last_status"] = "missed, catching up"
                else:
                    s["last_status"] = f"missed {self._clock(s['next_run'])}"
                if s.get("once"):
                    s["done"], s["enabled"] = True, False
            self.plan(s)
        self.save()
        return due

    def due(self) -> list[dict]:
        now, out = time.time(), []
        for s in self.list:
            if s.get("enabled") and s.get("next_run") and now >= s["next_run"] and s["id"] not in self.busy:
                out.append(s)
                if s.get("once"):
                    s["done"], s["enabled"] = True, False
                self.plan(s)
        if out:
            self.save()
        return out

    async def run(self, s: dict, why: str = "scheduled") -> str:
        j = self.jade
        self.busy.add(s["id"])
        s["last_run"], s["last_status"] = time.time(), "running"
        self.save()
        try:
            if s["kind"] == "shell":
                from .tools.builtin import run_shell
                out, code = await run_shell(j, s["command"], timeout=300)
                s["last_status"] = "ok" if code == 0 else f"error: exit {code}"
                self.log(s, why, f"$ {s['command']}\n{out}\nexit {code}")
                if code:
                    j.notify(f"{s['name']}: failed (exit {code})", out.strip()[-180:])
            elif s["kind"] in ("playbook", "flow"):
                tool = s["kind"]
                args = {"action": "run", "name": s[tool]}
                if s.get("input") is not None:
                    args["vars" if tool == "playbook" else "input"] = s["input"]
                args["unattended"] = True
                out = await j.run_tool(tool, args)
                s["last_status"] = "ok"
                self.log(s, why, f"{tool} {s[tool]}: ok\n{out[:4000]}")
            else:
                sess = None
                if s.get("chat") == "same" and s.get("session_id"):
                    try:
                        sess = j.session(s["session_id"])
                    except (KeyError, ValueError):
                        sess = None
                if sess is None:
                    sess = j.session()
                    sess.title = f"⏰ {s['name']}" + ("" if s.get("chat") == "same" else f" · {self.now().strftime('%b %-d %H:%M')}")
                    if s.get("chat") == "same":
                        s["session_id"] = sess.id
                async with j.background:
                    reply = await sess.ask(s["prompt"])
                s["last_status"] = "ok"
                j.notify(f"{s['name']}: done", re.sub(r"[`*#_>]", "", reply)[:180])
            return s["last_status"]
        except Exception as e:
            s["last_status"] = f"error: {e}"[:160]
            self.log(s, why, f"error: {e}")
            j.notify(f"{s['name']}: failed", str(e)[:180])
            return s["last_status"]
        finally:
            self.busy.discard(s["id"])
            self.save()

    def log(self, s: dict, why: str, text: str) -> None:
        v = self.jade.vault
        head = f"[{self.now().strftime('%Y-%m-%d %H:%M:%S')}] #{s['id']} {s['name']} ({why})"
        prev = v.read(CRON_LOG) if v.is_file(CRON_LOG) else ""
        nxt = prev + head + "\n" + text.rstrip()[:8000] + "\n\n"
        if len(nxt) > 200000:
            nxt = nxt[-150000:].split("\n", 1)[-1]
        v.write(CRON_LOG, nxt)


async def schedule_tool(ctx, action: str = "list", when: str = "", prompt: str = "", name: str = "", id: str = "", chat: str = "new", command: str = "",  # noqa: A002
                        flow: str = "", playbook: str = "", input=None, vars=None, **kw):   # noqa: A002
    sch: Scheduler = ctx.jade.scheduler
    a = str(action or "list").lower()
    if a == "add":
        w = when or kw.get("cron") or kw.get("time") or kw.get("schedule")
        if not str(w or "").strip():
            raise ToolError('add needs "when". ' + C.HELP)
        try:
            s = sch.add(when=w, prompt=prompt or kw.get("message") or kw.get("task") or name, name=name, chat=chat, command=command, playbook=playbook, flow=flow,
                        input=vars if playbook else input)
        except C.CronError as e:
            raise ToolError(str(e)) from None
        nxt = (f"It runs once at {sch._clock(s['run_at'])}" if s.get("once") else "Next runs: " + ", ".join(sch._clock(t.timestamp()) for t in C.upcoming(s["cron"], 3, sch.now())))
        tz = sch.now().tzname()
        running = "" if ctx.jade.daemon_running else " Schedules run while `jade daemon` (or `jade serve`) is running."
        return f"Created schedule:\n{sch.show(s)}\n{nxt} ({tz}).{running} Tell the user in one short sentence."
    if a == "remove":
        s = sch.find(id)
        sch.list.remove(s)
        sch.save()
        return f'Deleted "{s["name"]}".'
    if a in ("enable", "disable"):
        s = sch.find(id)
        if a == "enable" and s.get("once") and s["run_at"] <= time.time():
            raise ToolError("that one-time schedule has already passed; add a new one")
        s["enabled"], s["done"] = a == "enable", False
        sch.plan(s)
        sch.save()
        return f"{'Enabled' if s['enabled'] else 'Paused'}:\n{sch.show(s)}"
    if a == "run":
        s = sch.find(id)
        return f"Ran \"{s['name']}\": {await sch.run(s, 'by hand')}"
    if a == "list":
        return "\n".join(sch.show(s) for s in sch.list) or "No schedules yet."
    raise ToolError(f"unknown action {a}")


# ---------------------------------------------------------------- heartbeat
def heartbeat_body(jade: "Jade") -> str:
    v = jade.vault
    if not v.is_file(HB_PATH):
        return ""
    text = strip_comments(v.read(HB_PATH))
    real = [l for l in text.split("\n") if l.strip() and not re.match(r"^#{1,6}(\s|$)", l.strip()) and not re.match(r"^(```|~~~)", l.strip())
            and not re.match(r"^[-*+]\s*(\[[ xX]?\])?\s*$", l.strip()) and not re.match(r"^---+$", l.strip())]
    return text if real else ""


def is_ack(text: str) -> bool:
    t = re.sub(r"<think>[\s\S]*?</think>", "", str(text or "")).strip()
    if not t:
        return True
    m = re.search(r"^\**(HEARTBEAT_OK|NO_REPLY)\**[\s.!:]*|[\s.!:]*\**(HEARTBEAT_OK|NO_REPLY)\**[.!]?$", t, re.I)
    return bool(m) and len(t.replace(m.group(0), "").strip()) <= 300


def in_active_hours(start: str, end: str, d: datetime) -> bool:
    def mins(s):
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(s or "").strip())
        return int(m.group(1)) * 60 + int(m.group(2)) if m else None
    a, b = mins(start), mins(end)
    if a is None or b is None:
        return True
    if a == b:
        return False
    n = d.hour * 60 + d.minute
    return a <= n < b if a < b else n >= a or n < b


async def run_heartbeat(jade: "Jade", manual: bool = False) -> str:
    st = jade.settings
    state = jade.state
    body = heartbeat_body(jade)
    state["hb_last"] = time.time()
    if not body:
        state["hb_status"] = "skipped (HEARTBEAT.md is empty)"
        jade.save_state()
        return state["hb_status"]
    sid = state.get("hb_session")
    try:
        s = jade.session(sid) if sid else None
    except (KeyError, ValueError):
        s = None
    if s is None:
        s = jade.session()
        s.title = "♥ Heartbeat"
        s.meta["heartbeat"] = True
        state["hb_session"] = s.id
    before = len(s.messages)
    prompt = f"[Heartbeat · {jade.now().strftime('%a %b %-d %H:%M')}]\n{st.hb_prompt.strip() or HB_DEFAULT_PROMPT}\n\nHEARTBEAT.md:\n{body}"
    try:
        async with jade.background:
            reply = await s.ask(prompt)
    except Exception as e:
        state["hb_status"] = f"error: {e}"[:120]
        jade.save_state()
        return state["hb_status"]
    tools = sum(1 for m in s.messages[before:] if m.get("role") == "tool")
    if is_ack(reply):
        del s.messages[before:]   # silent: prune the OK run
        s.save()
        state["hb_status"] = "OK" + (f" ({tools} check{'s' if tools > 1 else ''})" if tools else "")
    else:
        clean = re.sub(r"\s+", " ", re.sub(r"[`*#_>$]", "", re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), reply))).strip()
        state["hb_status"] = "alert: " + clean[:80]
        jade.notify("♥ Jade", clean[:200])
    jade.save_state()
    return state["hb_status"]


# ---------------------------------------------------------------- the daemon
class Daemon:
    """Runs schedules, the heartbeat and curiosity (and any extra loops parts add) until stopped."""

    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.stopping = asyncio.Event()

    async def run(self) -> None:
        j = self.jade
        j.daemon_running = True
        sch = j.scheduler
        for s in sch.catch_up():
            asyncio.create_task(sch.run(s, "catch-up"))
        loops = [self._schedules(), self._heartbeat()] + [f(self) for f in j.daemon_loops]
        try:
            await asyncio.gather(*loops)
        finally:
            j.daemon_running = False

    def stop(self):
        self.stopping.set()

    async def _sleep(self, s: float) -> bool:
        try:
            await asyncio.wait_for(self.stopping.wait(), s)
            return True
        except asyncio.TimeoutError:
            return False

    async def _schedules(self):
        sch = self.jade.scheduler
        while not self.stopping.is_set():
            sch.list = sch._load() if sch.path.exists() and sch.path.stat().st_mtime > getattr(sch, "_seen", 0) else sch.list
            sch._seen = sch.path.stat().st_mtime if sch.path.exists() else 0
            for s in sch.due():
                asyncio.create_task(sch.run(s))
            if await self._sleep(10):
                return

    async def _heartbeat(self):
        j = self.jade
        while not self.stopping.is_set():
            st = j.settings
            if st.hb_on and time.time() - j.state.get("hb_last", 0) >= max(1, st.hb_every) * 60 and in_active_hours(st.hb_start, st.hb_end, j.now()):
                try:
                    await run_heartbeat(j)
                except Exception as e:
                    log.warning("heartbeat failed: %s", e)
            if await self._sleep(30):
                return


def setup(jade: "Jade") -> None:
    jade.scheduler = Scheduler(jade)
    spec = jade.specs["schedule"]
    desc = spec["description"].replace("They run only while the app is open", "They run while `jade daemon` or `jade serve` runs")
    jade.tools.add(Tool("schedule", desc, spec["parameters"], schedule_tool, core=False,
                        hints="schedule remind reminder daily weekly cron every later alarm recurring tomorrow morning evening at",
                        summary='schedule: create/list/pause/delete scheduled prompts and reminders, shell commands, playbooks or flows ("when" in plain English)'))
