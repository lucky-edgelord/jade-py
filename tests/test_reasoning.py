import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jade import reasoning as R

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")


def near(a, b, eps=1e-6):
    return abs(a - b) < eps


def ev(score, tier=3, kind="k", **kw):
    return {"kind": kind, "tier": tier, "score": score, "evidence": "e", **kw}


# ---------------------------------------------------------------- the engine (numbers from the app's tests)
def test_expand_priors_duplicates_limits():
    t = R.new_tree("g", {"width": 2, "depth": 2, "nodes": 4})
    a = R.expand(t, 0, [{"text": "one"}, {"text": "two"}, {"text": "ONE "}])
    pri = [t["nodes"][i]["p"] for i in a["created"]]
    assert len(a["created"]) == 2 and "duplicate" in a["skipped"] and near(sum(pri), 1) and pri[0] > pri[1]
    kid = a["created"][0]
    R.expand(t, kid, ["x"])
    with pytest.raises(R.ThinkError, match="depth limit"):
        R.expand(t, t["nodes"][kid]["children"][0], ["too deep"])
    part = R.expand(t, 0, ["a", "b", "c", "d", "e"])
    assert len(part["created"]) == 1 and any("over the node budget" in x for x in part["skipped"])
    with pytest.raises(R.ThinkError, match="budget used up"):
        R.expand(t, 0, ["f"])
    assert t["spent"]["nodes"] == 4


def test_evidence_weights_veto_backup():
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    with pytest.raises(R.ThinkError, match="needs evidence"):
        R.add_evals(t, 1, [{"kind": "x", "tier": 3, "score": 0.9, "evidence": ""}])
    with pytest.raises(R.ThinkError, match="between 0 and 1"):
        R.add_evals(t, 1, [ev(1.5)])
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    R.add_evals(t, 1, [ev(1, 1, "code"), ev(0.5, 3, "decide")])
    assert near(t["nodes"][1]["score"], 1.25 / 1.5)
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    R.add_evals(t, 1, [ev(0.99, 3), ev(0, 1, "code", veto=True)])
    n = t["nodes"][1]
    assert n["score"] == 0 and n["vetoed"] and n["status"] == "dead" and R.best(t)["node"] is None
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    R.add_evals(t, 1, [ev(0.1, 3, veto=True)])
    assert t["nodes"][1]["vetoed"] is False
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    R.expand(t, 1, ["b"])
    snap = lambda: [[t["nodes"][i]["N"], round(t["nodes"][i]["W"], 3)] for i in (0, 1, 2)]   # noqa: E731
    R.add_evals(t, 2, [ev(0.8)])
    assert snap() == [[1, 0.8], [1, 0.8], [1, 0.8]]
    R.add_evals(t, 1, [ev(0.4)])
    assert snap() == [[2, 1.2], [2, 1.2], [1, 0.8]]
    R.add_evals(t, 2, [ev(0.4)])
    assert snap() == [[2, 1.0], [2, 1.0], [1, 0.6]]


def test_gae():
    r = R.adv_array([0.5, 0.6, 0.8], 1, 0.95, 0.9)
    assert near(r["delta"][0], 0.07) and near(r["delta"][1], 0.16) and near(r["delta"][2], 0.2)
    assert near(r["A"][2], 0.2) and near(r["A"][1], 0.331) and near(r["A"][0], 0.353005)
    assert near(R.adv_array([0.7], 0.9, 0.95, 0.9)["A"][0], 0.2)


def test_selection():
    t = R.new_tree("g", {"mode": "beam", "width": 2, "depth": 3, "nodes": 50, "stop_on_solve": False})
    seq = []

    def rec():
        d = R.select(t)
        seq.append(f"{d['action']}:{d.get('node', d.get('stop'))}")

    def sc(i, s):
        R.add_evals(t, i, [ev(s)])
    rec()
    R.expand(t, 0, ["a", "b", "c"])
    rec(); sc(1, 0.9); rec(); sc(2, 0.7); rec(); sc(3, 0.2)
    rec()
    R.expand(t, 1, ["a1", "a2"])
    rec(); sc(4, 0.95); rec(); sc(5, 0.5)
    rec()
    R.expand(t, 2, ["b1", "b2"])
    rec(); sc(6, 0.3); rec(); sc(7, 0.85)
    rec()
    assert seq == ["expand:0", "check:1", "check:2", "check:3", "expand:1", "check:4", "check:5", "expand:2", "check:6", "check:7", "expand:4"]

    t = R.new_tree("g", {"nodes": 3})
    R.expand(t, 0, ["a", "b", "c"])
    assert R.select(t)["action"] == "check"
    for i in (1, 2, 3):
        R.add_evals(t, i, [ev(0.5)])
    assert R.select(t)["stop"] == "nodes"
    u = R.new_tree("g")
    R.expand(u, 0, ["a"])
    R.add_evals(u, 1, [ev(1, 1, "code")])
    assert R.select(u)["stop"] == "solved"
    for mode in ("best_first", "puct"):
        t = R.new_tree("g", {"mode": mode, "depth": 3, "stop_on_solve": False})
        R.expand(t, 0, ["a", "b"])
        R.add_evals(t, 1, [ev(0.9)])
        R.add_evals(t, 2, [ev(0.3)])
        assert R.select(t)["node"] == 1, mode


def test_best_features_reward():
    t = R.new_tree("g")
    R.expand(t, 0, ["judged", "verified"])
    R.add_evals(t, 1, [ev(0.99, 4, "critique")])
    R.add_evals(t, 2, [ev(0.9, 1, "code"), ev(1, 1, "code")])
    b = R.best(t)
    assert b["node"] == 2 and b["verified"]
    t = R.new_tree("g")
    R.expand(t, 0, ["a"])
    f = R.features(t)
    R.add_evals(t, 1, [ev(1, 1, "code")])
    rw = R.reward(t)
    assert len(f) == 13 and all(0 <= x <= 1 for x in f) and rw["outcome"] == 1 and 0.7 < rw["total"] < 1


# ---------------------------------------------------------------- the tool, with real verifiers
@needs_node
async def test_think_tool_verifiers(jade):
    T = lambda **a: jade.run_tool("think", a)   # noqa: E731
    assert "Name the check FIRST" in await T(action="help")
    r = json.loads(await T(action="start", goal="what is six times seven", verifiers=[{"kind": "code", "source": "if (!/42/.test(THOUGHT)) throw new Error('no 42 in it')"}]))
    assert r["verifiers"] == 1 and r["type"] == "other" and r["mode"] == "beam"
    assert json.loads(await T(action="expand", node=0, thoughts=[{"text": "it is 41"}, {"text": "it is 42"}]))["created"] == [1, 2]
    c1 = await T(action="check", node=1)
    assert c1.startswith("[Verifier output") and '"vetoed": true' in c1 and "no 42 in it" in c1
    c2 = json.loads((await T(action="check", node=2)).split("\n", 1)[1])
    assert c2["status"] == "solved" and c2["score"] == 1
    b = json.loads((await T(action="best")).split("\n", 1)[1])
    assert b["node"] == 2 and b["verified"] and b["thoughts"] == ["it is 42"]
    assert json.loads(await T(action="next"))["stop"] == "solved"
    closed = json.loads(await T(action="close", outcome="ok", note="worked"))
    assert closed["closed"] == "ok" and closed["reward"]["outcome"] == 1
    ep = json.loads(jade.vault.read("/Jade/Tot/episodes.jsonl").strip().split("\n")[-1])
    assert ep["outcome"] == "ok" and ep["verifiers"] == ["code"] and ep["note"] == "worked"
    steps = [json.loads(x) for x in jade.vault.read("/Jade/Tot/steps.jsonl").strip().split("\n")]
    assert steps[-1]["done"] and all(len(s["s"]) == 13 for s in steps)
    with pytest.raises(RuntimeError, match="closed"):
        await T(action="expand", node=0, thoughts=["x"])

    # partial credit, read-only fs, shell with {file}, file checks
    await T(action="start", goal="g", verifiers=[{"kind": "code", "source": "console.log(fs.readFileSync('/Notes/n.md').length > 0 ? 'ok' : ''); console.log('0.4')"}])
    jade.vault.write("/Notes/n.md", "hello")
    await T(action="expand", node=0, thoughts=["a"])
    c = json.loads((await T(action="check", node=1)).split("\n", 1)[1])
    assert near(c["score"], 0.4) and c["status"] == "scored"
    await T(action="start", goal="g", verifiers=[{"kind": "code", "source": "fs.writeFileSync('/evil.txt', 'x')"}])
    await T(action="expand", node=0, thoughts=["a"])
    assert '"vetoed": true' in await T(action="check", node=1) and not jade.vault.exists("/evil.txt")
    await T(action="start", goal="g", verifiers=[{"kind": "shell", "cmd": "grep -q yes {file}"}])
    await T(action="expand", node=0, thoughts=["no", "yes"])
    assert '"vetoed": true' in await T(action="check", node=1)
    assert '"status": "solved"' in await T(action="check", node=2)
    assert not jade.vault.list("/Jade/Tot/tmp")
    await T(action="start", goal="g", verifiers=[{"kind": "file", "path": "/Notes/n.md", "contains": "hello"}, {"kind": "file", "path": "/nope.md", "exists": False}])
    await T(action="expand", node=0, thoughts=["a"])
    assert '"status": "solved"' in await T(action="check", node=1)
    show = await T(action="show")
    assert "[1] ✓" in show
    with pytest.raises(RuntimeError, match="kind must be"):
        await T(action="start", goal="g", verifiers=[{"kind": "trust_me"}])
    with pytest.raises(RuntimeError, match="cannot be pruned"):
        await T(action="prune", node=0)


async def test_think_auto(jade, fake):
    """The harness proposes, checks (self-review), and stops at the budget; only the result comes back."""
    calls = {"n": 0}

    def reply(body):
        prompt = body["messages"][-1]["content"]
        if "Propose" in prompt:
            calls["n"] += 1
            return {"content": json.dumps({"thoughts": [{"text": f"idea {calls['n']}a", "p": 0.6}, {"text": f"idea {calls['n']}b"}]})}
        return {"content": json.dumps({"score": 0.9 if "a\n" in prompt or prompt.rstrip().endswith("a") else 0.3, "flaw": "none"})}
    fake.default = reply
    out = await jade.run_tool("think", {"action": "auto", "goal": "plan a trip", "nodes": 6, "depth": 2, "width": 2, "n": 2})
    r = json.loads(out.split("\n", 1)[1])
    assert r["stopped"] in ("nodes", "exhausted") and r["best"]["verified"] is False and r["spent"]["nodes"] <= 6
    assert "no hard check" in r["warning"]


# ---------------------------------------------------------------- decide (a fake Ollaya)
@pytest.fixture
def ollaya():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(body)
            q = body["questions"]["q"]
            ans = {"noul": True, "confidence": 0.88, "probabilities": [0.12, 0.88]} if q["type"] == "noul" else \
                {"choice": q["criteria"][1], "confidence": 0.7, "probabilities": {q["criteria"][0]: 0.3, q["criteria"][1]: 0.7}} if q["type"] == "choice" else \
                {"score": 1, "confidence": 0.6, "probabilities": [0.2, 0.6, 0.2]}
            b = json.dumps({"model": "judge-1", "answers": {"q": ans}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


async def test_decide(jade, ollaya):
    url, seen = ollaya
    assert "decide" not in [t.name for t in jade.tools.active(jade)]
    jade.settings.ollaya_host, jade.settings.ollaya_model = url, "judge-1"
    assert "decide" in [t.name for t in jade.tools.active(jade)]
    r = await jade.run_tool("decide", {"state": "disk full", "question": "How urgent?", "type": "choice", "options": ["low", "high"]})
    assert r.startswith("choice: high | confidence 0.700") and "model: judge-1" in r
    o = await R.decide_data(jade, {"x": 1}, "ok?", "yesno")
    assert o["answer"] is True and o["result"] is True and o["probabilities"] == {"0": 0.12, "1": 0.88}
    assert seen[-1]["questions"]["q"]["type"] == "noul" and seen[-1]["state"] == '{"x": 1}'
    with pytest.raises(RuntimeError, match="at least 2 options"):
        await jade.run_tool("decide", {"state": "s", "question": "q", "type": "choice", "options": ["one"]})
    # as a think verifier: yes at 0.88 scores 0.88 at tier 3, never "solved" by a judge alone
    await jade.run_tool("think", {"action": "start", "goal": "g", "verifiers": [{"kind": "decide", "question": "Does it work?"}]})
    await jade.run_tool("think", {"action": "expand", "node": 0, "thoughts": ["a"]})
    c = json.loads((await jade.run_tool("think", {"action": "check", "node": 1})).split("\n", 1)[1])
    assert near(c["score"], 0.88) and c["evaluations"][0]["tier"] == 3 and c["status"] == "scored" and "judge-1" in c["evaluations"][0]["evidence"]


# ---------------------------------------------------------------- delegate
async def test_delegate(jade, fake):
    jade.vault.write("/Notes/Secret plan.md", "launch on friday")
    fake.script = [
        {"tool_calls": [("notes_search", {"query": "plan"}), ("write", {"path": "/x.md", "content": "no"})]},
        {"tool_calls": [("read", {"path": "/Notes/Secret plan.md"})]},
        {"content": "<think>hmm</think>It launches on Friday. Evidence: /Notes/Secret plan.md"},
    ]
    r = await jade.run_tool("delegate", {"task": "When is the launch?"})
    assert r.startswith("[Sub-agent report.") and "It launches on Friday" in r and "hmm" not in r and "read-only" in r
    assert not jade.vault.exists("/x.md")
    assert "not available to a sub-agent (it is read-only)" in json.dumps(fake.requests[1]["messages"])
    sent = {t["function"]["name"] for t in fake.requests[0]["tools"]}
    assert "write" not in sent and "delegate" not in sent and "read" in sent
    assert "Task: When is the launch?" in fake.requests[0]["messages"][1]["content"]

    fake.script = [{"tool_calls": [("read", {"path": "/Notes/Secret plan.md"})]}, {"content": "partial answer"}]
    r = await jade.run_tool("delegate", {"task": "t", "max_steps": 1})
    assert "step limit (1)" in r and "partial answer" in r
