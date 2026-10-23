import asyncio
import json
import shutil
import time

import pytest

from jade import flows as FL

needs_node = pytest.mark.skipif(not (shutil.which("node") or shutil.which("nodejs")), reason="needs Node.js")


def save(jade, f):
    return jade.flows.save(f)


def test_validate_and_clean():
    errs = FL.validate({"name": "x", "steps": [{"type": "nope"}, {"type": "tool"}, {"type": "if", "condition": "1", "then": "no"},
                                                {"type": "wait", "seconds": "abc"}, {"type": "note", "path": "/a", "action": "eat"},
                                                {"type": "llm", "prompt": "p", "retries": 9, "onError": "maybe"}]}, ["read"])
    text = "\n".join(errs)
    for want in ('unknown type "nope"', '"tool" is required', '"then" must be a list', '"seconds" must be a number', '"action" must be one of',
                 "retries must be 0-3", "onError must be"):
        assert want in text, want
    assert FL.validate({"name": "x", "trigger": {"type": "schedule", "when": "tomorrow at 9"}, "steps": []})[0].startswith("trigger.when must repeat")
    assert FL.validate({"name": "x", "steps": [{"type": "tool", "tool": "nope"}]}, ["read"]) == ['steps.0 (tool): no tool named "nope"']
    c = FL.clean({"steps": [{"type": "set", "id": "a", "_x": 1}, {"type": "if", "id": "a", "then": [{"type": "stop"}]}], "name": "n", "_path": "p"})
    assert list(c) == ["name", "steps"] and [s["id"] for s in c["steps"]] == ["a", "s1"] and c["steps"][1]["then"][0]["id"] == "s2" and "_x" not in c["steps"][0]
    assert FL.match([1, "two"], "1") and FL.match({"a": 1}, {"a": 1}) and not FL.match("x", "y")


@needs_node
async def test_flow_runs_steps(jade, fake):
    jade.vault.write("/Inbox.md", "- buy milk\n- call bob urgent\nnot a task\n")
    fake.script = [{"content": "normal"}, {"content": "urgent"}]
    save(jade, {"name": "Triage", "vars": {"tag": "#todo"}, "steps": [
        {"type": "note", "name": "Inbox", "action": "read", "path": "Inbox"},
        {"type": "each", "name": "Each line", "items": '$json.split("\\n").filter(l => /^\\s*[-*] /.test(l)).map(l => l.replace(/^\\s*[-*]\\s+/, ""))', "steps": [
            {"type": "llm", "name": "Classify", "prompt": "Classify {{ $item }}"},
            {"type": "if", "name": "Urgent?", "condition": "/urgent/i.test($json)", "then": [
                {"type": "note", "action": "append", "path": "/Urgent.md", "content": "- [ ] {{ $item }} {{ $vars.tag }}"}]}]},
        {"type": "set", "name": "Out", "fields": {"n": "{{ $node['Each line'].length }}", "first": "{{ $node.Inbox.slice(0, 5) }}", "today": "{{ $today }}"}},
        {"type": "js", "code": "const t = fs.readFileSync('/Urgent.md', 'utf8'); console.log('hello'); return { ...$json, urgent: t.trim(), list: (await jade.tool('notes_search', {query: 'milk'})).length > 0 };"},
    ]})
    run = await jade.flows.run_by_name("triage", None)
    assert run["status"] == "ok", jade.flows.run_text(run)
    out = run["output"]
    assert out["n"] == 2 and out["first"] == "- buy" and out["urgent"] == "- [ ] call bob urgent #todo" and out["list"] is True
    assert len(out["today"]) == 10
    assert "Classify call bob urgent" in fake.requests[1]["messages"][-1]["content"]
    assert [s["name"] for s in run["steps"]][:3] == ["Inbox", "Each line", "Classify"]
    assert jade.flows.recent()[0]["id"] == run["id"]


@needs_node
async def test_flow_errors_retries_switch_stop(jade):
    save(jade, {"name": "E", "steps": [
        {"type": "shell", "name": "bad", "command": "echo nope; exit 3", "onError": "continue"},
        {"type": "switch", "value": "$json.error.includes('exit 3') ? 'x' : 'y'", "cases": [{"match": ["a", "x"], "steps": [{"type": "set", "fields": {"hit": "x"}}]}],
         "default": [{"type": "set", "fields": {"hit": "default"}}]},
        {"type": "stop", "value": "{{ $json }}"},
        {"type": "set", "fields": {"never": True}}]})
    run = await jade.flows.run_by_name("E")
    assert run["status"] == "ok" and run["output"] == {"hit": "x"} and run.get("stopped")
    assert run["steps"][0]["status"] == "error" and run["steps"][0]["note"] == "continued"

    save(jade, {"name": "F", "steps": [{"type": "js", "name": "boom", "code": "throw new Error('kaput')", "retries": 1}]})
    run = await jade.flows.run_by_name("F")
    assert run["status"] == "error" and "kaput" in run["error"] and run["steps"][0]["note"] == "retry 1"

    save(jade, {"name": "G", "steps": [{"type": "set", "fields": {"x": "{{ while(true){} }}"}}]})
    run = await jade.flows.run_by_name("G")
    assert run["status"] == "error"


@needs_node
async def test_flow_tool_nested_and_shell(jade):
    r = await jade.run_tool("flow", {"action": "create", "flow": {"name": "Double", "steps": [{"type": "js", "code": "return $json.n * 2"}]}})
    assert "Created flow" in r
    r = await jade.run_tool("flow", {"action": "create", "flow": {"name": "Outer", "steps": [
        {"type": "flow", "flow": "Double", "input": {"n": 21}},
        {"type": "js", "code": "const sh = await jade.shell('echo $(( 1 + 1 ))'); return { v: $json, sh: sh.stdout.trim(), sub: await jade.flow('Double', { n: 5 }) }"}]}})
    r = await jade.run_tool("flow", {"action": "run", "name": "outer"})
    assert 'Flow "Outer" ok' in r and '"v": 42' in r and '"sh": "2"' in r and '"sub": 10' in r
    assert "Double" in await jade.run_tool("flow", {"action": "list"})
    assert "Outer: ok" in await jade.run_tool("flow", {"action": "runs"})
    assert "disabled" in await jade.run_tool("flow", {"action": "disable", "name": "Double"})
    assert jade.flows.find("double")["enabled"] is False
    with pytest.raises(RuntimeError, match="already exists"):
        await jade.run_tool("flow", {"action": "create", "flow": {"name": "Double", "steps": []}})
    with pytest.raises(RuntimeError, match="fix these"):
        await jade.run_tool("flow", {"action": "update", "name": "Double", "flow": {"steps": [{"type": "zzz"}]}})
    assert "Deleted" in await jade.run_tool("flow", {"action": "delete", "name": "Outer"})
    assert "type" in await jade.run_tool("flow", {"action": "help"})


@needs_node
async def test_schedule_and_note_triggers(jade):
    save(jade, {"name": "Watch", "trigger": {"type": "note", "path": "/projects"}, "steps": [
        {"type": "note", "action": "append", "path": "/seen.md", "content": "{{ $json.changed.join(',') }}"}]})
    save(jade, {"name": "Tick", "trigger": {"type": "schedule", "when": "every 5 minutes"}, "steps": [{"type": "note", "action": "append", "path": "/ticks.md", "content": "{{ $json.trigger }}"}]})
    F = jade.flows
    jade.vault.write("/projects/a.md", "one")
    assert F.note_check() == [] and F.tick() == []        # first look only arms
    jade.vault.write("/projects/b.md", "two")
    tasks = F.note_check()
    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert jade.vault.read("/seen.md").strip() == "/projects/b.md"
    assert F.note_check() == []
    due = F.next[F.find("Tick")["_path"]]
    assert due and due > time.time()
    tasks = F.tick(due + 1)
    await asyncio.gather(*tasks)
    assert jade.vault.read("/ticks.md").strip() == "schedule"


@needs_node
async def test_scheduler_runs_flow_and_playbook_step(jade):
    jade.vault.write("/Jade/Playbooks/hello.json", json.dumps({"vars": {"who": "x"}, "tasks": [{"do": "set", "vars": {"msg": "hi {{ who }}"}}]}))
    save(jade, {"name": "PB", "steps": [{"type": "playbook", "playbook": "hello", "vars": {"who": "{{ $json.name }}"}}, {"type": "set", "fields": {"m": "{{ $json.vars.msg }}"}}]})
    s = jade.scheduler.add(when="every day at 9am", flow="PB", input={"name": "ann"})
    status = await jade.scheduler.run(s, "test")
    assert status == "ok", jade.vault.read("/Jade/Logs/cron.log")
    assert jade.flows.recent()[0]["output"] == {"m": "hi ann"}


def test_cli_flow_run(jade, tmp_path):
    from typer.testing import CliRunner

    from jade.cli import app
    if not shutil.which("node"):
        pytest.skip("needs Node.js")
    save(jade, {"name": "Hi", "steps": [{"type": "set", "fields": {"hi": "{{ $json.who }}"}}]})
    r = CliRunner().invoke(app, ["--vault", str(jade.vault.root), "flow", "run", "Hi", '{"who": "bo"}'], env={"JADE_DATA_DIR": str(tmp_path / "data"), "JADE_CONFIG": str(tmp_path / "none.toml")})
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"hi": "bo"}
