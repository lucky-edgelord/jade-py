"""2.26 parity: Plans (Gantt) and autonomous mode (the 3-tier scheduler), the same files and behaviour as the app."""
import json
import time

import pytest

from jade.tools.registry import ToolError


def g(jade, **a):
    try:
        return jade.plans.tool(a, a.pop("_by", "jade"))
    except ToolError as e:
        return "ERR " + str(e)


def test_plans(jade):
    P = jade.plans
    today = P.today()
    assert "Made project garden-shed" in g(jade, action="create", name="Garden Shed", description="Build a shed")
    assert "Added t1 Design" in g(jade, action="add", project="garden-shed", name="Design", start=today, days=3)
    r = g(jade, action="add", project="garden-shed", name="Buy wood", days=2, deps=["t1"], owner="user")
    from jade.gantt import add
    assert f"Added t2 Buy wood: {add(today, 3)}" in r and "(user), after t1" in r
    g(jade, action="add", project="garden-shed", name="Build", days=4, deps="t2")
    assert "Added t4 Done" in g(jade, action="add", project="garden-shed", name="Done", milestone=True, deps="t3")
    assert "cycle" in g(jade, action="link", project="garden-shed", task="t1", dep="t3")
    assert "Moved to keep dependencies: t2 Buy wood" in g(jade, action="update", project="garden-shed", task="t1", days=5)
    assert "is the user's task" in g(jade, action="update", project="garden-shed", task="t2", progress=50)
    assert "Updated t2" in g(jade, action="update", project="garden-shed", task="t2", progress=50, confirm=True)
    assert "Updated t2" in g(jade, action="update", project="garden-shed", task="t2", progress=0, _by="user")
    show = g(jade, action="show", project="garden-shed")
    assert "Garden Shed" in show and "t1 Design" in show and "◆" in show and "^ today" in show
    rep = g(jade, action="report", project="garden-shed")
    assert "Ready:\n  t1 Design" in rep and "Critical path: t1 → t2 → t3 → t4" in rep
    rd = g(jade, action="ready")
    assert "Garden Shed / t1 Design" in rd and "t2" not in rd
    assert jade.vault.is_file("/Jade/Projects/garden-shed.json")
    # the app's file format
    p = json.loads(jade.vault.read("/Jade/Projects/garden-shed.json"))
    assert p["id"] == "garden-shed" and [t["id"] for t in p["tasks"]] == ["t1", "t2", "t3", "t4"] and p["tasks"][3]["milestone"] is True
    assert "Removed t3" in g(jade, action="remove", project="garden-shed", task="t3") and P.load("garden-shed")["tasks"][-1]["deps"] == []


def A(jade):
    a = jade.autonomy
    a.tasks, a.hist, a.picks = [], [], 0
    return a


def test_picking(jade):
    a = A(jade)
    for o in [{"goal": "long A", "title": "A"}, {"goal": "long B", "title": "B"}, {"kind": "prompt", "goal": "p30", "title": "P30", "priority": 30},
              {"kind": "prompt", "goal": "p80", "title": "P80", "priority": 80}]:
        assert jade.autonomy.tool({"action": "add", **o}).startswith("Queued q")
    assert a.pick()["title"] == "P80"
    a.add({"title": "URGENT", "goal": "now", "kind": "prompt", "tier": 1}, "user")
    assert a.pick()["title"] == "URGENT"
    next(t for t in a.tasks if t["title"] == "URGENT")["state"] = "done"
    a.picks = 0
    assert "".join(str(a.pick()["tier"]) for _ in range(8)) == "22232223"
    now = time.time()
    p30 = next(t for t in a.tasks if t["title"] == "P30")
    p30["enq"] = now - 120 * 60
    assert a.eff_prio(p30, now) == 70 and a.eff_prio({"prio": 60, "enq": now}, now) < 70
    for t in a.tasks:
        if t["tier"] == 2:
            t["state"] = "paused"
    ta, tb = (next(t for t in a.tasks if t["title"] == x) for x in "AB")
    ta["level"], tb["level"] = 1, 0
    assert a.pick()["title"] == "B"
    ta["enq"] = time.time() - 31 * 60
    a.pick()
    assert ta["level"] == 0
    for _ in range(3):
        jade.autonomy.tool({"action": "add", "goal": "x", "tier": 1})
    with pytest.raises(ToolError, match="already have 3 interrupts"):
        jade.autonomy.tool({"action": "add", "goal": "w", "tier": 1})


def test_limits(jade):
    a = A(jade)
    st = jade.settings
    st.auto_on, st.auto_quiet_from, st.auto_quiet_to, st.auto_steps_hour = True, 0, 0, 10
    assert a.blocked() == ""
    h = time.localtime().tm_hour
    st.auto_quiet_from, st.auto_quiet_to = h, (h + 1) % 24
    assert "quiet hours" in a.blocked()
    st.auto_quiet_from = st.auto_quiet_to = 0
    a.hist.append({"t": time.time(), "steps": 10})
    assert "hourly budget" in a.blocked()
    a.hist = []
    st.auto_on = False
    assert a.blocked() == "off"


async def test_slices(jade, fake):
    a = A(jade)
    st = jade.settings
    st.auto_on, st.auto_quiet_from, st.auto_quiet_to, st.auto_steps_hour, st.auto_gantt = True, 0, 0, 1000, False
    jade.vault.write("/Notes/in.md", "hello")

    def model(body):
        msgs = body["messages"]
        task = next((m["content"] for m in msgs if m["role"] == "user" and "Autonomous mode" in str(m["content"])), "")
        tools = sum(1 for m in msgs[[i for i, m in enumerate(msgs) if m["role"] == "user"][-1]:] if m["role"] == "tool")
        if "Write the poem" in task:
            if "Your notes from the last slice" in task:
                return {"content": "Finished the poem.\nSTATUS: done"} if tools else {"tool_calls": [("write", {"path": "/Notes/poem.md", "content": "roses"})]}
            if tools < 2:
                return {"tool_calls": [("read", {"path": "/Notes/in.md"})]}
            return {"content": "Read the notes; drafting next.\nSTATUS: continue — write it"}
        if "Call the user" in task:
            return {"content": "I wanted to call.\nSTATUS: blocked: waiting"} if tools else {"tool_calls": [("phone", {"action": "call", "number": "1"})]}
        if "Ask for the address" in task:
            return {"content": "I need the address.\nSTATUS: blocked: which address?"}
        if "from the plan" in task:
            return {"content": "Did the design.\nSTATUS: done"}
        return {"content": "ok\nSTATUS: done"}
    fake.default = model
    a.add({"title": "Poem", "goal": "Write the poem"}, "user")
    t = await a.tick()
    assert t["state"] == "ready" and t["level"] == 1 and t["slices"] == 1 and "drafting next" in t["memo"], t
    t = await a.tick()
    assert t["state"] == "done" and t["slices"] == 2 and jade.vault.read("/Notes/poem.md") == "roses"
    log = jade.vault.read("/Jade/Autonomy/log.md")
    assert '"Poem"' in log and "· done" in log and len(jade.vault.read("/Jade/Autonomy/slices.jsonl").strip().split("\n")) == 2
    # a tool on the deny list waits for the user
    if jade.tools.get("phone"):
        a.add({"title": "Call", "goal": "Call the user", "tier": 2, "kind": "goal"}, "user")
        t = await a.tick()
        assert t["state"] == "waiting" and t["need"]["tool"] == "phone", t
        with pytest.raises(ToolError, match="only they can allow"):
            a.act(t["id"], "resume", "jade")
        a.act(t["id"], "allow", "user")
        assert t["state"] == "ready" and "phone" in t["allow"]
        a.act(t["id"], "cancel", "user")
    # blocked
    a.add({"title": "Addr", "goal": "Ask for the address"}, "user")
    t = await a.tick()
    assert t["state"] == "blocked" and t["why"] == "which address?"
    # non-model kinds
    jade.vault.write("/Jade/Lisp/sum.jl", "(print (sum (range 101)))")
    a.add({"kind": "lisp", "file": "/Jade/Lisp/sum.jl", "tier": 2}, "user")
    t = await a.tick()
    assert t["state"] == "done" and "5050" in t["result"]
    a.add({"kind": "shell", "command": "echo from-shell", "tier": 2}, "user")
    t = await a.tick()
    assert t["state"] == "done" and "from-shell" in t["result"]
    # plan tasks become tier 3 goals and are marked done
    jade.plans.tool({"action": "create", "name": "Shed"}, "user")
    jade.plans.tool({"action": "add", "project": "shed", "name": "Design", "days": 2}, "user")
    st.auto_gantt = True
    a.last_plans = 0
    t = await a.tick()
    assert t["title"] == "Shed: Design" and t["state"] == "done" and t["gantt"]["task"] == "t1"
    assert jade.plans.load("shed")["tasks"][0]["progress"] == 100
    assert a.from_plans() == 0
    # off: nothing runs
    a.set_on(False)
    a.add({"title": "After", "goal": "x"}, "user")
    assert await a.tick() is None and next(t for t in a.tasks if t["title"] == "After")["state"] == "ready"
    assert len(jade.autonomy.tool({"action": "log", "limit": 3}).split("\n")) == 3
    assert jade.vault.is_file("/Jade/skills/autonomy/SKILL.md")


def test_api_and_cli(jade, tmp_path, fake):
    import os
    import subprocess
    import sys

    from fastapi.testclient import TestClient

    from jade.api import create_app
    c = TestClient(create_app(jade))
    r = c.post("/v1/lisp", json={"code": "(map inc (range 3))"}).json()
    assert r["ok"] and r["value"] == "(1 2 3)"
    assert c.post("/v1/lisp", json={"code": "(nope)"}).json()["error"]["code"] == "lisp_error"
    assert c.post("/v1/plans", json={"action": "create", "name": "Trip"}).json()["ok"]
    assert "Added t1" in c.post("/v1/plans", json={"action": "add", "project": "trip", "name": "Book", "days": 2}).json()["result"]
    g = c.get("/v1/plans/trip").json()
    assert g["states"]["t1"] == "ready" and g["critical"] == ["t1"] and "Book" in g["text"]
    t = c.post("/v1/sched/tasks", json={"goal": "tidy notes"}).json()["task"]
    assert t["tier"] == 3 and t["by"] == "user"
    assert c.post(f"/v1/sched/tasks/{t['id']}/pause").json()["ok"] and jade.autonomy.load()[-1]["state"] == "paused"
    s = c.get("/v1/sched").json()
    assert s["on"] is False and s["tasks"][-1]["id"] == t["id"] and "Autonomous mode: OFF" in s["text"]
    assert c.post("/v1/sched/tick").json()["status"] == "off"
    # the CLI
    env = {**os.environ, "JADE_CONFIG": str(tmp_path / "c.toml"), "JADE_HOST": fake.url, "JADE_MODEL": "fake:1b", "JADE_EMBED_ON": "false"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "jade", "--vault", str(tmp_path / "v"), *a], env=env, capture_output=True, text=True, timeout=120)   # noqa: E731
    r = run("lisp", "-e", "(print (+ 1 2)) (list 4 5)")
    assert r.returncode == 0 and r.stdout == "3\n(4 5)\n", (r.stdout, r.stderr)
    r = run("lisp", "/Jade/Lisp/examples/state-machine.jl")
    assert "final: opened" in r.stdout, r.stderr
    r = run("lisp", "-e", "(car 1)")
    assert r.returncode == 1 and "car: expected a non-empty list" in r.stderr
    r = run("sched", "add", "write the weekly review")
    assert r.returncode == 0 and "[T3 ready] goal" in r.stdout, r.stderr
    r = run("sched", "on")
    assert "Autonomous mode: ON" in r.stdout and "auto_on = true" in (tmp_path / "c.toml").read_text()
    r = run("sched", "list")
    assert "write the weekly review" in r.stdout
    r = run("plan-do", "create", "name=Shed")
    assert "Made project shed" in r.stdout
    run("plan-do", "add", "project=shed", "name=Design", "days=2")
    r = run("plan", "shed")
    assert "t1 Design" in r.stdout and "Critical path: t1" in r.stdout, r.stderr
