"""Jade Lisp: the Python port gives the app's answers (tests/fixtures/lisp_cases.json comes from the app's engine),
plus the machine's own guarantees (deep recursion, tail calls, fuel, sandboxes) and the lisp tool inside Jade."""
import asyncio
import json
from pathlib import Path

import pytest

from jade.lisp import Lisp, LispError, err_text, show

CASES = json.loads((Path(__file__).parent / "fixtures" / "lisp_cases.json").read_text())


def fake_host():
    """The same stand-in for Jade as the app's tests/lisp_engine_test.mjs."""
    kv, calls = {}, []

    async def host(op, a):
        calls.append((op, a))
        if op == "tool":
            n, args = a["name"], a.get("args") or {}
            if n == "litert":
                return json.dumps({"id": "m1-abc", "inputs": [[1, 224, 224, 3]]}) if args.get("action") == "load" else json.dumps({"top5": [[3, 0.9]]})
            if n == "write":
                return "wrote"
            return f"result of {n} {json.dumps(args, separators=(',', ':'))}"
        if op == "ask":
            return "answer to " + a["prompt"]
        if op == "flow":
            return {"flow": a["name"], "input": a.get("input")}
        if op == "shell":
            return {"code": 0, "stdout": "ran " + a["command"], "stderr": ""}
        if op == "search":
            return f"found {a['query']} {a['limit']}"
        if op == "kv":
            if a["op"] == "set":
                kv[a["key"]] = a["value"]
                return True
            return kv.get(a["key"])
        if op == "fs.write":
            return a["path"]
        if op == "list":
            return ["read"]
        raise RuntimeError("unexpected " + op)
    host.calls = calls
    return host


async def ev(code, host=None, **o):
    lisp = Lisp(None if o.get("pure") else (host or fake_host()), fuel=o.get("fuel", 50_000_000), pure=bool(o.get("pure")))
    try:
        return show(await lisp.run(code))
    except LispError as e:
        return "ERR " + err_text(e)


@pytest.mark.parametrize("case", [c for c in CASES if not c.get("app_only") or "write-file" in c["code"]], ids=lambda c: c["code"][:50])
def test_same_answers_as_the_app(case):
    assert asyncio.run(ev(case["code"], **(case.get("opts") or {}))) == case["want"]


def test_machine_guarantees():
    async def go():
        # deep recursion does not touch Python's recursion limit; tail calls are free
        assert await ev("(define (count n) (if (= n 0) 0 (+ 1 (count (- n 1))))) (count 100000)") == "100000"
        assert await ev("(let loop ((i 0)) (if (= i 300000) i (loop (+ i 1))))") == "300000"
        # output, and the JS-compatible number printing
        lisp = Lisp()
        await lisp.run('(print "a" 1 :b) (display "x") (printf "~a~%" 2)')
        assert lisp.output() == "a 1 :b\nx2\n"
        assert await ev("(/ 1 4)") == "0.25" and await ev("(* 1.5 2)") == "3" and await ev("(/ 6 3)") == "2"
        # fuel inside a sandbox is an ordinary error outside it; the outer program goes on
        assert await ev('(try (eval-in (sandbox :pure) "(let l () (l))" {:fuel 300}) (catch e (str "caught: " e)))') == '"caught: out of fuel: the sandboxed program used more than 300 steps"'
        # a pure interpreter has no way out
        assert (await ev("(read-file \"/x\")", pure=True)).startswith("ERR unbound symbol: read-file")
        # from Python
        l2 = Lisp()
        l2.define("twice", lambda x: x * 2)
        assert await l2.run("(twice 21)") == 42
        await l2.run("(define (sq x) (* x x))")
        assert await l2.call("sq", 7) == 49
        # a read error says where
        assert (await ev("(+ 1 2")).startswith("ERR read error at line 1: ( is never closed")
    asyncio.run(go())


def test_tool_calls_reach_the_host():
    async def go():
        h = fake_host()
        assert await ev('(map (lambda (n) (tool/echo :n n)) (list 1 2))', h) == '("result of echo {\\"n\\":1}" "result of echo {\\"n\\":2}")'
        assert [c[1]["args"] for c in h.calls] == [{"n": 1}, {"n": 2}]
        # a failing call is an ordinary, catchable error
        async def bad(op, a):
            raise RuntimeError("no tool named nope")
        assert await ev('(try (tool/nope) (catch e (str "got: " e)))', bad) == '"got: no tool named nope"'
    asyncio.run(go())


def test_lisp_in_jade(jade):
    from jade import lisp as LI
    LI.seed(jade)
    v = jade.vault
    assert v.is_file("/Jade/Lisp/lib/dsl.jl") and v.is_file("/Jade/skills/lisp-dsl/SKILL.md") and v.is_file("/Jade/Lisp/examples/rules.jl")
    v.write("/Notes/lisp-in.md", "hello lisp")

    async def go():
        out, val, err = await LI.run_lisp(jade, '(write-file "/Notes/lisp-out.md" (string-upcase (read-file "/Notes/lisp-in.md"))) (read-file "/Notes/lisp-out.md")')
        assert not err and val == "HELLO LISP" and v.read("/Notes/lisp-out.md") == "HELLO LISP"
        out, val, err = await LI.run_lisp(jade, file="/Jade/Lisp/examples/rules.jl")
        assert not err, err
        assert "alerts: ((hot 31) (muggy) (weekend))" in out and "blocked: unbound symbol: read-file" in out and "stopped: out of fuel" in out
        out, val, err = await LI.run_lisp(jade, file="/Jade/Lisp/examples/state-machine.jl")
        assert "final: opened" in out and "refused: no transition from locked on open" in out
        out, val, err = await LI.run_lisp(jade, file="/Jade/Lisp/examples/hello.jl", args=["Jade"])
        assert "Hello, Jade" in out and "338350" in out
        r = await jade.run_tool("lisp", {"code": "(length (list-dir \"/Jade/Lisp/examples\"))"})
        assert r.strip() == "5", r
        r = await jade.run_tool("lisp", {"code": "(tool/read :path \"/Notes/lisp-in.md\")"})
        assert "hello lisp" in r
        r = await jade.run_tool("lisp", {"code": "(nope)"})
        assert "[exit 1]" in r and "unbound symbol: nope" in r
    asyncio.run(go())
    # a seeded file the user changed (and unmarked) is kept
    v.write("/Jade/Lisp/lib/dsl.jl", ";; mine\n")
    LI.seed(jade)
    assert v.read("/Jade/Lisp/lib/dsl.jl") == ";; mine\n"
