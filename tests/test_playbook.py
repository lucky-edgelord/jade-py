"""The app's playbook tests (tests/playbook_test.mjs in jade-vault), ported: the same playbooks must behave the same."""
import json

import pytest

from jade import playbook as PB


def X(src, vars=None):
    try:
        return {"v": PB.expr(src, PB.make_scope(vars or {}, {}))}
    except PB.PBError as e:
        return {"err": str(e)}


def test_expressions_like_the_app():
    assert X("1 + 2 * 3")["v"] == 7 and X('"a" + "b"')["v"] == "ab" and X("10 % 4")["v"] == 2 and X("-3 + 5")["v"] == 2
    assert X("x > 2 and not done", {"x": 3, "done": False})["v"] is True and X("x in [1,2,3]", {"x": 2})["v"] is True and X('"b" not in "abc"')["v"] is False
    assert X("items | length", {"items": [1, 2, 3]})["v"] == 3 and X("len(items) == 3", {"items": [1, 2, 3]})["v"] is True and X("p | float * 2", {"p": "$12.50"})["v"] == 25
    assert X("a.b.c", {"a": {"b": {"c": 7}}})["v"] == 7 and X("l[1]", {"l": [5, 6]})["v"] == 6 and X('missing | default("d")')["v"] == "d" and X("missing is defined")["v"] is False
    assert "undefined variable" in X("nope + 1")["err"] and "unknown function" in X("evil(1)")["err"]
    assert X("a.__proto__ is defined", {"a": {}})["v"] is False and X('"abc".constructor is defined')["v"] is False
    assert any(w in X("1 +")["err"] for w in ("too early", "unexpected")) and "unexpected" in X("a ) b")["err"] and "division by zero" in X("1 / 0")["err"]
    assert X('"7" == 7')["v"] is True and X('"10" > "9"')["v"] is True and X('range(3) | join("-")')["v"] == "0-1-2"
    assert X('regex_find("order #4412 ok", "#(\\\\d+)", 1)')["v"] == "4412"
    assert "forever" in X('matches("aaaa", "(a+)+b")')["err"] and "forever" in X('matches("x", "(a|b)*c")')["err"]
    assert X('matches("order 12", "order \\\\d+")')["v"] is True
    assert X("round(2.5)")["v"] == 3 and X("round(1.2345, 2)")["v"] == 1.23 and X("int('42px')")["v"] == 42


def test_interp_keeps_types():
    s = PB.make_scope({"n": 3, "l": [1, 2], "who": "ann"}, {})
    assert PB.interp("{{ n }}", s) == 3 and PB.interp("{{ l }}", s) == [1, 2] and PB.interp("hi {{ who }} x{{ n }}", s) == "hi ann x3"
    assert PB.interp({"a": "{{ n + 1 }}", "when": "{{ n }}"}, s, PB.NOINTERP) == {"a": 4, "when": "{{ n }}"}
    assert PB.interp("{{ l }}!", s) == "[1,2]!"


def test_hash_matches_the_app():
    # pbHash(pbCanon({name:'x', tasks:[{do:'debug', msg:'a'}]})) in the app
    assert PB.canon({"name": "x", "tasks": [{"do": "debug", "msg": "a"}]}) == '{"name":"x","allow_hosts":[],"vars":{},"seconds":0,"tasks":[{"do":"debug","msg":"a"}]}'


@pytest.fixture
def T(jade):
    async def call(**a):
        try:
            return await jade.run_tool("playbook", a)
        except RuntimeError as e:
            return "ERR " + str(e)
    return call


@pytest.fixture
def R(jade, T):
    async def run(name="x", **extra):
        r = await T(action="run", name=name, **extra)
        return {"err": r} if r.startswith("ERR") else json.loads(r[r.index("{"):])
    return run


async def save(T, pb, name="x"):
    return await T(action="save", name=name, playbook=pb)


async def test_validation(T):
    r = await T(action="validate", playbook={"tasks": [{"do": "dance"}, {"block": [], "do": "set"}, {"do": "set"}, {"name": "x"}, {"do": "browser.click"},
                                                      {"do": "delegate", "task": "x"}, {"do": "assert", "that": ["1 +"]}, {"do": "debug", "when": "a b", "retries": 99}]})
    for want in ('unknown "do": "dance"', "not both", "set needs vars", 'needs "do"', "browser.click needs a target", "delegate puts a model", '"when"', "retries must be"):
        assert want in r, want
    assert "valid: 1 tasks" in await T(action="validate", playbook={"tasks": [{"do": "notes_search", "query": "x"}]})
    assert "old browser script" in await T(action="validate", playbook={"steps": [{"do": "goto", "url": "http://shop.test/"}]})
    m = await T(action="modules")
    assert "browser.goto" in m and "notes_search" in m and "Not allowed" in m


async def test_approval(jade, T, R):
    jade.settings.approve = "risky"
    jade.approver = lambda call, tool: False
    assert "did not approve" in await save(T, {"tasks": [{"do": "debug", "msg": "hi"}]})
    assert "No playbooks" in await T(action="list")
    jade.approver = lambda call, tool: True
    assert "saved" in await save(T, {"tasks": [{"do": "debug", "msg": "a"}]})
    jade.vault.write("/Jade/Playbooks/x.json", json.dumps({"tasks": [{"do": "debug", "msg": "changed"}]}))
    jade.approver = lambda call, tool: False
    assert "not approved" in (await R())["err"]
    # schedules and flows run only approved, unchanged playbooks
    assert "not approved" in (await T(action="run", name="x", unattended=True))


async def test_tools_as_tasks(jade, T, R):
    jade.vault.write("/Notes/Alpha.md", "# Alpha\nlamp price list #lamps\n")
    r = await save(T, {"description": "tools", "vars": {"names": ["one", "two", "three"], "base": "/Jade/Playbooks/out/t"}, "tasks": [
        {"name": "write files", "do": "write", "path": "{{ base }}-{{ item }}.txt", "content": "file {{ item }} #{{ index }}", "loop": "names"},
        {"name": "read one", "do": "read", "path": "{{ base }}-two.txt", "register": "two"},
        {"do": "assert", "that": ["two == 'file two #1'", "len(names) == 3"]},
        {"do": "set", "vars": {"total": "{{ len(names) * 2 }}", "label": "n={{ len(names) }}"}},
        {"do": "debug", "msg": "skipped?", "when": "total > 100"},
        {"do": "debug", "var": "label", "register": "dbg"},
        {"do": "notes_search", "query": "lamps", "register": "hits", "when": ["total == 6", 'label == "n=3"']},
        {"do": "assert", "that": ["'Alpha' in hits"], "msg": "search should find Alpha"},
        {"do": "tool", "tool": "read", "args": {"path": "{{ base }}-one.txt"}, "register": "one"},
        {"do": "write", "path": "{{ base }}.json", "content": '{"a": [1,2,3]}'}, {"do": "read", "path": "{{ base }}.json", "register": "js", "json": True},
        {"do": "assert", "that": ["js.a[2] == 3"]},
    ]})
    assert "saved" in r, r
    j = await R()
    assert j["ok"] and j["vars"]["total"] == 6 and j["vars"]["label"] == "n=3" and "file two #1" in j["vars"]["two"] and "file one #0" in j["vars"]["one"], j
    assert j["recap"]["skipped"] == 1 and "Alpha" in j["vars"]["hits"] and j["vars"]["js"]["a"] == [1, 2, 3]
    assert "file three #2" in jade.vault.read("/Jade/Playbooks/out/t-three.txt")


async def test_failures_rescue_retries(T, R):
    await save(T, {"tasks": [{"do": "set", "vars": {"a": 1}}, {"name": "boom", "do": "assert", "that": ["a == 2"], "msg": "a is not two"}, {"do": "debug", "msg": "never"}]})
    j = await R()
    assert not j["ok"] and j["failed"]["task"] == 2 and j["failed"]["name"] == "boom" and "a is not two" in j["failed"]["error"] and len(j["log"]) == 2
    await save(T, {"tasks": [{"do": "fail", "msg": "soft", "ignore_errors": True, "register": "f"}, {"do": "assert", "that": ["f_error == 'soft'", "f == null"]},
                             {"do": "debug", "msg": "{{ nope }}", "ignore_errors": True}]})
    j = await R()
    assert j["ok"] and j["recap"]["ignored"] == 2, j
    await save(T, {"tasks": [{"block": [{"do": "fail", "msg": "inner"}, {"do": "debug", "msg": "skipped by the failure"}], "rescue": [{"do": "set", "vars": {"got": "{{ error }}"}}],
                              "always": [{"do": "set", "vars": {"always": True}}]}, {"do": "assert", "that": ["got == 'inner'", "always", "failed_task is defined"]}]})
    j = await R()
    assert j["ok"] and j["recap"]["rescued"] == 1 and j["vars"]["always"] is True
    await save(T, {"tasks": [{"block": [{"do": "fail", "msg": "x"}], "always": [{"do": "set", "vars": {"cleaned": True}}]}]})
    j = await R()
    assert not j["ok"] and j["vars"]["cleaned"] is True
    # until / failed_when with a tool
    await save(T, {"tasks": [{"do": "set", "vars": {"n": 0}}, {"name": "poll", "do": "bash", "command": "echo $(( $(cat n.txt 2>/dev/null || echo 0) + 1 )) > n.txt; cat n.txt",
                                                             "until": "int(result) >= 3", "retries": 5, "delay": 0.05, "register": "n"},
                             {"do": "bash", "command": "echo 1", "failed_when": "'1' in result", "ignore_errors": True, "register": "fw"}]})
    j = await R()
    assert j["ok"] and j["vars"]["n"].strip() == "3" and j["vars"]["fw"] is None and "failed_when" in j["vars"]["fw_error"], j
    await save(T, {"tasks": [{"do": "bash", "command": "echo 5", "until": "int(result) > 100", "retries": 2, "delay": 0.05}]})
    j = await R()
    assert not j["ok"] and "until was still false after 3 tries" in j["failed"]["error"]


async def test_loops_and_tags(T, R):
    await save(T, {"tasks": [{"do": "set", "vars": {"acc": []}}, {"do": "set", "vars": {"acc": "{{ acc + [item] }}"}, "loop": "range(3)"}, {"do": "set", "vars": {"s": '{{ join(acc, "") }}'}}]})
    j = await R()
    assert j["ok"] and j["vars"]["acc"] == [0, 1, 2] and j["vars"]["s"] == "012"
    await save(T, {"tasks": [{"do": "set", "vars": {"a": 1}, "tags": ["only"]}, {"do": "set", "vars": {"b": 1}, "tags": ["other"]}, {"block": [{"do": "set", "vars": {"c": 1}}], "tags": ["only"]}, {"do": "set", "vars": {"d": 1}}]})
    j = await R(tags=["only"])
    assert j["ok"] and j["vars"]["a"] == 1 and j["vars"]["c"] == 1 and "b" not in j["vars"] and "d" not in j["vars"] and j["recap"]["skipped"] == 2
    j = await R(skip_tags=["other"])
    assert j["ok"] and "b" not in j["vars"] and j["vars"]["d"] == 1
    await save(T, {"tasks": [{"do": "debug", "msg": "x", "loop": "range(300)"}]})
    j = await R()
    assert not j["ok"] and "at most 200 per loop" in j["failed"]["error"] or "capped at 1000" in json.dumps(j)


async def test_safety(jade, T, R):
    await save(T, {"allow_hosts": ["other.test"], "tasks": [{"do": "web_fetch", "url": "http://shop.test/"}]})
    j = await R()
    assert not j["ok"] and "allow_hosts" in j["failed"]["error"]
    jade.settings.secrets = {"SHOPPW": "hunter22x"}
    await save(T, {"tasks": [{"do": "debug", "msg": "pw is {{ SHOPPW }}", "register": "m"}, {"do": "write", "path": "/Jade/Playbooks/out/pw.txt", "content": "{{ SHOPPW }}"}]})
    j = await R()
    assert j["ok"] and "hunter22x" not in json.dumps(j) and jade.vault.read("/Jade/Playbooks/out/pw.txt") == "hunter22x"
    await save(T, {"tasks": [{"do": "save", "path": "/Jade/Config/Sentry.json", "value": "x"}]})
    j = await R()
    assert not j["ok"] and "only write under" in j["failed"]["error"]
    assert "model in the loop" in await T(action="validate", playbook={"tasks": [{"do": "think", "question": "x"}]})
    await save(T, {"vars": {"who": "a"}, "tasks": [{"do": "debug", "msg": "{{ who }}"}]})
    assert "can only set variables the playbook declares" in (await R(vars={"evil": "x"}))["err"]
    assert "not a plain variable name" in await T(action="validate", playbook={"tasks": [{"do": "set", "vars": {"constructor": 2}}]})


async def test_nested_playbook_plan_list_delete(T, R):
    await save(T, {"vars": {"n": 1}, "tasks": [{"do": "set", "vars": {"doubled": "{{ n * 2 }}"}}]}, "child")
    await save(T, {"tasks": [{"do": "playbook", "playbook": "child", "vars": {"n": 21}, "register": "c"}, {"do": "assert", "that": ["c.doubled == 42"]}]})
    j = await R()
    assert j["ok"], j
    assert "x:" in await T(action="list") and "playbook" in await T(action="plan", name="x")
    assert "deleted" in await T(action="delete", name="x")


async def test_from_a_schedule(jade, T):
    await save(T, {"tasks": [{"do": "write", "path": "/sched-pb.txt", "content": "from a schedule"}]}, "nightly")
    s = jade.scheduler.add(when="every day at 3am", playbook="nightly")
    assert await jade.scheduler.run(s) == "ok"
    assert jade.vault.read("/sched-pb.txt") == "from a schedule"


def test_legacy_conversion():
    pb = PB.from_legacy({"steps": [{"do": "goto", "url": "https://x.test/"}, {"do": "if", "css": "#a", "then": [{"do": "click", "css": "#a"}]},
                                   {"do": "each", "in": "rows", "as": "r", "steps": [{"do": "set", "var": "v", "value": "{{ r }}"}]}]})
    assert pb["tasks"][0]["do"] == "browser.goto" and pb["tasks"][1]["do"] == "browser.exists" and pb["tasks"][2]["when"] == "_c1"
    assert pb["tasks"][3]["loop"] == "rows" and pb["tasks"][3]["loop_var"] == "r" and pb["tasks"][3]["block"][0] == {"do": "set", "vars": {"v": "{{ r }}"}}
