"""Plans: Gantt charts for long-term projects. The same files and the same gantt tool as the Jade Vault app (2.26):

    /Jade/Projects/<id>.json  {id, name, description, tasks: [{id, name, start, end, deps, owner, progress, milestone, notes}]}

Dates are YYYY-MM-DD; end is the last day of a task (a milestone has start == end). The user's tasks are theirs: Jade
changes one only with confirm=true, after asking. With autonomous mode on, Jade's ready tasks (every dependency done,
start date reached) become tier 3 goals of the scheduler (see autonomy.py)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

GANTT_DIR = "/Jade/Projects"
ACTIONS = ["projects", "create", "show", "add", "update", "link", "unlink", "remove", "plan", "report", "ready"]


def d_of(s) -> date:
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise ToolError(f"not a date: {s} (use YYYY-MM-DD)") from None


def iso(d) -> str:
    return d_of(d).isoformat()


def add(d, n: int) -> str:
    return (d_of(d) + timedelta(days=n)).isoformat()


def diff(a, b) -> int:
    return (d_of(b) - d_of(a)).days


def dur(t) -> int:
    return diff(t["start"], t["end"]) + 1


def done(t) -> bool:
    return (t.get("progress") or 0) >= 100


def slug(s) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()))[:48] or "project"


class Plans:
    def __init__(self, jade: "Jade"):
        self.jade = jade

    @property
    def v(self):
        return self.jade.vault

    def today(self) -> str:
        return self.jade.now().date().isoformat()

    def path(self, pid) -> str:
        return f"{GANTT_DIR}/{slug(pid)}.json"

    def load(self, pid) -> dict:
        p = json.loads(self.v.read(self.path(pid)))
        p["id"] = p.get("id") or slug(pid)
        p["name"] = p.get("name") or p["id"]
        p["tasks"] = p.get("tasks") if isinstance(p.get("tasks"), list) else []
        for t in p["tasks"]:
            t["deps"] = [str(x) for x in (t.get("deps") or [])]
            t["owner"] = "user" if t.get("owner") == "user" else "jade"
            t["progress"] = max(0, min(100, int(t.get("progress") or 0)))
            t["end"] = t.get("end") or t["start"]
        return p

    def save(self, p) -> dict:
        p["updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.v.write(self.path(p["id"]), json.dumps(p, indent=2, ensure_ascii=False))
        return p

    def list(self) -> list[dict]:
        if not self.v.is_dir(GANTT_DIR):
            return []
        out = []
        for name in self.v.list(GANTT_DIR):
            n = name.rstrip("/").rsplit("/", 1)[-1]
            if n.endswith(".json"):
                try:
                    out.append(self.load(n[:-5]))
                except (ValueError, OSError):
                    pass
        return sorted(out, key=lambda p: p["name"].lower())

    def find(self, ref) -> dict:
        r = str(ref or "").strip()
        names = ", ".join(p["id"] for p in self.list()) or "none yet"
        if not r:
            raise ToolError(f"which project? Give project (its id or name). Projects: {names}")
        if self.v.is_file(self.path(r)):
            return self.load(r)
        for p in self.list():
            if p["name"].lower() == r.lower() or p["id"] == slug(r):
                return p
        raise ToolError(f'no project "{r}". Projects: {names}')

    @staticmethod
    def task(p, ref) -> dict:
        r = str(ref if ref is not None else "").strip()
        for t in p["tasks"]:
            if t["id"] == r:
                return t
        for t in p["tasks"]:
            if t["name"].lower() == r.lower():
                return t
        raise ToolError(f'no task "{r}" in {p["name"]}. Tasks: ' + ("; ".join(f"{x['id']} {x['name']}" for x in p["tasks"]) or "none"))

    @staticmethod
    def new_id(p) -> str:
        i = len(p["tasks"]) + 1
        while any(t["id"] == f"t{i}" for t in p["tasks"]):
            i += 1
        return f"t{i}"

    @staticmethod
    def cycle(p, frm, to) -> bool:
        seen, st = set(), [to]
        while st:
            x = st.pop()
            if x == frm:
                return True
            if x in seen:
                continue
            seen.add(x)
            t = next((y for y in p["tasks"] if y["id"] == x), None)
            if t:
                st.extend(t["deps"])
        return False

    @staticmethod
    def topo(p) -> list[dict]:
        out, mark = [], {}

        def visit(t):
            if mark.get(t["id"]) == 2:
                return
            if mark.get(t["id"]) == 1:
                raise ToolError(f"dependency cycle at {t['id']}")
            mark[t["id"]] = 1
            for dd in t["deps"]:
                x = next((y for y in p["tasks"] if y["id"] == dd), None)
                if x:
                    visit(x)
            mark[t["id"]] = 2
            out.append(t)
        for t in p["tasks"]:
            visit(t)
        return out

    def plan(self, p) -> list[str]:
        moved = []
        for t in self.topo(p):
            ends = sorted(x["end"] for x in p["tasks"] if x["id"] in t["deps"])
            if not ends:
                continue
            earliest = add(ends[-1], 0 if t.get("milestone") else 1)
            if t["start"] < earliest:
                n, was = dur(t), t["start"]
                t["start"], t["end"] = earliest, add(earliest, n - 1)
                moved.append(f"{t['id']} {t['name']}: {was} -> {t['start']}")
        return moved

    def critical(self, p) -> dict:
        if not p["tasks"]:
            return {"ids": [], "end": None}
        order = self.topo(p)
        finish = max(t["end"] for t in order)
        late: dict[str, str] = {}
        for t in reversed(order):
            le = finish
            for s in (x for x in p["tasks"] if t["id"] in x["deps"]):
                ls_ = add(late[s["id"]], -(dur(s) - 1))
                cand = add(ls_, 0 if s.get("milestone") else -1)
                if cand < le:
                    le = cand
            late[t["id"]] = le
        return {"ids": [t["id"] for t in order if late[t["id"]] <= t["end"]], "end": finish}

    def state(self, p, t, today=None) -> str:
        today = today or self.today()
        if done(t):
            return "done"
        open_ = [x for x in p["tasks"] if x["id"] in t["deps"] and not done(x)]
        if t["end"] < today:
            return "late"
        if open_:
            return "blocked" if t["start"] <= today else "waiting"
        return "ready" if t["start"] <= today else "planned"

    def report(self, p, today=None) -> str:
        today = today or self.today()
        by: dict[str, list] = {}
        for t in p["tasks"]:
            by.setdefault(self.state(p, t, today), []).append(t)
        crit = self.critical(p)
        tot = sum(dur(t) for t in p["tasks"])
        pct = round(sum(dur(t) * t["progress"] for t in p["tasks"]) / tot) if tot else 0

        def line(t):
            return f"  {t['id']} {t['name']} ({t['owner']}, {t['start']}{' → ' + t['end'] if t['end'] != t['start'] else ''}, {t['progress']}%)"
        soon = [t for t in p["tasks"] if not done(t) and t["end"] >= today and diff(today, t["end"]) <= 3]
        parts = [f"{p['name']}: {len(p['tasks'])} tasks, {pct}% done, finishes {crit['end'] or '(no tasks)'}."]
        for k in ("late", "blocked", "ready", "waiting"):
            if by.get(k):
                parts.append(f"{k[0].upper() + k[1:]}:\n" + "\n".join(line(t) for t in by[k]))
        if soon:
            parts.append("Due within 3 days:\n" + "\n".join(line(t) for t in soon))
        if crit["ids"]:
            parts.append("Critical path: " + " → ".join(crit["ids"]))
        return "\n".join(parts)

    def text(self, p, today=None) -> str:
        today = today or self.today()
        if not p["tasks"]:
            return f"{p['name']}: no tasks yet. Add some with action add."
        start = min(t["start"] for t in p["tasks"])
        end = max(t["end"] for t in p["tasks"])
        span = diff(start, end) + 1
        unit = 7 if span > 120 else 2 if span > 60 else 1
        cols = -(-span // unit)
        W = min(28, max(len(t["id"] + " " + t["name"]) for t in p["tasks"]))
        crit = set(self.critical(p)["ids"])
        rows = []
        for t in self.topo(p):
            a, b = diff(start, t["start"]) // unit, diff(start, t["end"]) // unit
            bar = ""
            for i in range(cols):
                if i < a or i > b:
                    bar += "·"
                elif t.get("milestone"):
                    bar += "◆"
                else:
                    bar += "█" if (i - a) / max(1, b - a + 1) < t["progress"] / 100 else "░"
            label = (t["id"] + " " + t["name"])[:W].ljust(W)
            rows.append(f"{label} |{bar}| {'you ' if t['owner'] == 'user' else 'jade'} {str(t['progress']).rjust(3)}% {self.state(p, t, today)}{' *' if t['id'] in crit else ''}"
                        f"{'  after ' + ','.join(t['deps']) if t['deps'] else ''}")
        ti = diff(start, today)
        mark = (" " * (W + 2 + ti // unit) + "^ today") if 0 <= ti < span else ""
        head = f"{p['name']}  {start} → {end}  (one column = {unit} day{'s' if unit > 1 else ''}; █ done ░ to do ◆ milestone; * critical)"
        return head + "\n" + "\n".join(rows) + ("\n" + mark if mark else "")

    def ready(self, today=None) -> list[dict]:
        today = today or self.today()
        return [{"project": p["id"], "projectName": p["name"], "task": t} for p in self.list() for t in p["tasks"]
                if t["owner"] == "jade" and not t.get("milestone") and self.state(p, t, today) == "ready"]

    def fields(self, t, a, p) -> None:
        if a.get("name") is not None:
            t["name"] = str(a["name"])[:120]
        if a.get("start") is not None:
            t["start"] = iso(a["start"])
        if a.get("end") is not None:
            t["end"] = iso(a["end"])
        elif a.get("days") is not None:
            t["end"] = add(t["start"], max(1, int(a["days"])) - 1)
        if a.get("milestone") is not None:
            t["milestone"] = a["milestone"] in (True, "true")
        if t.get("milestone"):
            t["end"] = t["start"]
        if t["end"] < t["start"]:
            raise ToolError(f"{t['name']}: end {t['end']} is before start {t['start']}")
        if a.get("owner") is not None:
            t["owner"] = "user" if a["owner"] in ("user", "you") else "jade"
        if a.get("progress") is not None:
            t["progress"] = max(0, min(100, round(float(a["progress"]))))
        if a.get("notes") is not None:
            t["notes"] = str(a["notes"])[:2000]
        if a.get("deps") is not None:
            raw = a["deps"] if isinstance(a["deps"], list) else [a["deps"]]
            ds = [self.task(p, x.strip())["id"] for y in raw for x in str(y).split(",") if x.strip()]
            for dd in ds:
                if dd == t["id"] or self.cycle(p, t["id"], dd):
                    raise ToolError(f"{t['id']} cannot depend on {dd}: that makes a cycle")
            t["deps"] = list(dict.fromkeys(ds))

    def tool(self, a: dict, by: str = "jade") -> str:
        action = str(a.get("action") or "projects").lower()
        if action not in ACTIONS:
            raise ToolError(f"unknown action {action}: use {', '.join(ACTIONS)}")
        if action == "projects":
            ps = self.list()
            if not ps:
                return "No projects yet. Make one with action create (name, description)."
            return "\n".join(f"{p['id']}: {p['name']} — {len(p['tasks'])} tasks, {sum(1 for t in p['tasks'] if done(t))} done" + (f"\n  {p['description']}" if p.get("description") else "") for p in ps)
        if action == "ready":
            r = self.ready()
            return "\n".join(f"{x['projectName']} / {x['task']['id']} {x['task']['name']} ({x['task']['progress']}%, due {x['task']['end']})" for x in r) or "Nothing of yours is ready right now."
        if action == "create":
            name = str(a.get("name") or a.get("project") or "").strip()
            if not name:
                raise ToolError("create needs a name")
            pid = slug(name)
            if self.v.is_file(self.path(pid)):
                raise ToolError(f"a project {pid} already exists")
            self.save({"id": pid, "name": name, "description": str(a.get("description") or ""), "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "by": by, "tasks": []})
            return f"Made project {pid}. Add tasks with action add (project={pid}, name, start, days or end, deps, owner)."
        if action == "report" and not a.get("project"):
            ps = self.list()
            return "\n\n".join(self.report(p) for p in ps) if ps else "No projects yet."
        p = self.find(a.get("project"))
        confirmed = a.get("confirm") in (True, "true")

        def mine(t):
            if by == "jade" and t["owner"] == "user" and not confirmed:
                raise ToolError(f"{t['id']} {t['name']} is the user's task. Ask them first; pass confirm=true only after they agreed (or they change it on the Plans page).")
        if action == "show":
            notes = "\n".join(f"{t['id']} notes: {t['notes']}" for t in p["tasks"] if t.get("notes"))
            return self.text(p) + (f"\n\n{p['description']}" if p.get("description") else "") + ("\n\n" + notes if notes else "")
        if action == "report":
            return self.report(p)
        if action == "add":
            name = str(a.get("name") or "").strip()
            if not name:
                raise ToolError("add needs a name")
            raw = a.get("deps")
            deps = [x.strip() for y in (raw if isinstance(raw, list) else [raw] if raw else []) for x in str(y).split(",") if x.strip()]
            start = iso(a["start"]) if a.get("start") else (add(max(self.task(p, dd)["end"] for dd in deps), 1) if deps else self.today())
            t = {"id": self.new_id(p), "name": name, "start": start, "end": start, "deps": [], "owner": "user" if a.get("owner") in ("user", "you") else "jade", "progress": 0}
            p["tasks"].append(t)
            try:
                b = dict(a)
                b.pop("name", None)
                b.pop("start", None)
                b.pop("owner", None)
                if b.get("days") is None and not b.get("end"):
                    b["days"] = 1
                self.fields(t, b, p)
            except Exception:
                p["tasks"].pop()
                raise
            moved = self.plan(p)
            self.save(p)
            return (f"Added {t['id']} {t['name']}: {t['start']}{' → ' + t['end'] if t['end'] != t['start'] else ''} ({t['owner']})"
                    f"{', after ' + ', '.join(t['deps']) if t['deps'] else ''}." + ("\nMoved to keep dependencies: " + "; ".join(moved) if moved else ""))
        if action == "update":
            t = self.task(p, a.get("task") or a.get("id"))
            before = json.dumps(t, sort_keys=True)
            if any(a.get(k) is not None for k in ("name", "start", "end", "days", "owner", "progress", "milestone", "deps")):
                mine(t)
            self.fields(t, a, p)
            moved = self.plan(p)
            self.save(p)
            if json.dumps(t, sort_keys=True) == before:
                return "Nothing changed."
            return f"Updated {t['id']} {t['name']}: {t['start']}{' → ' + t['end'] if t['end'] != t['start'] else ''}, {t['progress']}%, {t['owner']}." + ("\nMoved to keep dependencies: " + "; ".join(moved) if moved else "")
        if action in ("link", "unlink"):
            t = self.task(p, a.get("task") or a.get("id"))
            dd = self.task(p, a.get("dep") or a.get("after"))
            mine(t)
            if action == "link":
                if dd["id"] == t["id"] or self.cycle(p, t["id"], dd["id"]):
                    raise ToolError(f"{t['id']} cannot depend on {dd['id']}: that makes a cycle")
                if dd["id"] not in t["deps"]:
                    t["deps"].append(dd["id"])
            else:
                t["deps"] = [x for x in t["deps"] if x != dd["id"]]
            moved = self.plan(p)
            self.save(p)
            return f"{t['id']} {'now waits for' if action == 'link' else 'no longer waits for'} {dd['id']}." + ("\nMoved: " + "; ".join(moved) if moved else "")
        if action == "remove":
            t = self.task(p, a.get("task") or a.get("id"))
            mine(t)
            p["tasks"] = [x for x in p["tasks"] if x is not t]
            for x in p["tasks"]:
                x["deps"] = [y for y in x["deps"] if y != t["id"]]
            self.save(p)
            return f"Removed {t['id']} {t['name']}."
        moved = self.plan(p)
        self.save(p)
        return ("Rescheduled:\n" + "\n".join(moved) if moved else "Every task already starts after its dependencies.") + "\n\n" + self.text(p)


DESCRIPTION = ("Plans: Gantt charts for long-term projects in /Jade/Projects (the user sees them on the dashboard's Plans page and in the Jade Vault app). "
               "Actions: projects; create (name, description); show (project: text chart); add (project, name, start YYYY-MM-DD, days or end, deps: task ids, owner jade|user, milestone, notes); "
               "update (project, task, any field, progress 0-100); link / unlink (project, task, dep); remove (project, task); plan (push tasks after their dependencies); "
               "report (late, blocked, ready, due soon, critical path; all projects without project); ready (your tasks that can start now). "
               "The user's tasks are theirs: changing one needs confirm=true after they agreed. With autonomous mode on, your ready tasks are worked on by the scheduler.")
PARAMS = {"type": "object", "properties": {"action": {"type": "string", "enum": ACTIONS}, "project": {"type": "string"}, "task": {"type": "string"}, "name": {"type": "string"},
                                           "description": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "days": {"type": "number"},
                                           "deps": {"type": "array", "items": {"type": "string"}}, "dep": {"type": "string"}, "owner": {"type": "string", "enum": ["jade", "user"]},
                                           "progress": {"type": "number"}, "milestone": {"type": "boolean"}, "notes": {"type": "string"}, "confirm": {"type": "boolean"}},
          "required": ["action"]}


async def gantt_tool(ctx, **a: Any) -> str:
    return ctx.jade.plans.tool(a, "jade")


def setup(jade: "Jade") -> None:
    jade.plans = Plans(jade)
    jade.tools.add(Tool("gantt", DESCRIPTION, PARAMS, gantt_tool, core=False,
                        hints="gantt plan plans project projects milestone milestones timeline roadmap deadline dependencies critical path weeks months long term planning",
                        summary="gantt: long-term project plans (Gantt charts)"))
