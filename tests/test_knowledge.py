"""The knowledge graph: the shared engine cases (the same file Jade Vault's tests run), the knowledge tool, its files,
verify mode in the prompt, the CLI and the API."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jade import knowledge as K
from jade.api import create_app

CASES = json.loads((Path(__file__).parent / "fixtures" / "kg_cases.json").read_text())["cases"]


def _norm(rows):
    return sorted(json.dumps(dict(sorted(r.items()))) for r in rows)


def _flat(p):
    return [p["fact"]] if p.get("by") == "fact" else [x for c in p.get("because", []) for x in _flat(c)]


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_shared_cases(c):
    try:
        load = K.call({"op": "load", "rules": c["rules"], "facts": c.get("facts", [])})
    except K.KgError as e:
        assert c["op"] == "error" and e.code == c["code"] and c.get("message_has", "") in str(e), str(e)
        return
    assert c["op"] != "error", "expected an error"
    h = load["handle"]
    if c["op"] == "query":
        assert _norm([a["bindings"] for a in K.call({"op": "query", "handle": h, "q": c["input"]})["answers"]]) == _norm(c["answers"])
    elif c["op"] == "check":
        got = sorted(json.dumps([v["constraint"], dict(sorted(v["bindings"].items()))]) for v in K.call({"op": "check", "handle": h})["violations"])
        assert got == sorted(json.dumps([v[0], dict(sorted(v[1].items()))]) for v in c["violations"])
    else:
        r = K.call({"op": "verify", "handle": h, "claim": c["input"]})
        assert r["verdict"] == c["verdict"], r["text"]
        if "related" in c:
            assert r["related"] == c["related"]
        if "proof_facts" in c:
            assert sorted(_flat(r["proof"])) == sorted(c["proof_facts"])
        if "constraints" in c:
            assert [v["constraint"] for v in r["violations"]] == c["constraints"]
        if "rests_on_unconfirmed" in c:
            assert [f["id"] for f in r["rests_on"] if f["status"] == "proposed"] == c["rests_on_unconfirmed"]
    K.call({"op": "free", "handle": h})


async def test_the_tool_files_and_proofs(jade):
    T = lambda **a: jade.run_tool("knowledge", a)   # noqa: E731
    v = jade.vault
    assert v.is_file(K.KG_RULES) and v.is_file(K.KG_FACTS) and "name: knowledge-graph" in v.read(K.SKILL_PATH)
    assert (await T(action="stats")).startswith("0 facts")
    r = await T(action="add", facts=["reports_to(bob, alice)", "reports_to(carol, bob)"], source="People.md")
    assert r.count("proposed (id") == 2
    assert all(f["status"] == "proposed" and f["by"] == "jade" and f["source"] == "People.md" for f in K.kg_of(jade).facts())
    assert 'works_at("Carol", "Acme Corp"): proposed' in await T(action="add", subject="Carol", relation="Works at", object="Acme Corp")
    assert "already in the graph" in await T(action="add", fact="reports_to(bob, alice)")
    assert "not added (a fact cannot have variables" in await T(action="add", fact="reports_to(X, alice)")
    await T(action="add_rule", rule="above(X, Y) :- reports_to(X, Y).")
    await T(action="add_rule", rule="above(X, Z) :- reports_to(X, Y), above(Y, Z)", comment="chain of command")
    with pytest.raises(RuntimeError, match="only used under not"):
        await T(action="add_rule", rule="bad(X) :- not good(X).")
    assert "bad(X)" not in v.read(K.KG_RULES)
    r = await T(action="verify", claim="above(carol, alice)")
    assert r.startswith("✓ PROVEN (rests on 2 unconfirmed facts") and "[by rule: above(X, Z)" in r
    assert (await T(action="verify", claim="above(alice, carol)")).startswith("? UNKNOWN")
    r = await T(action="query", q="above(carol, X)")
    assert "2 answers" in r and "X = bob" in r and "X = alice" in r
    # the user confirms; Jade cannot undo the user's facts
    k = K.kg_of(jade)
    k.set_status([f["id"] for f in k.facts()], "confirmed")
    assert (await T(action="verify", claim="above(carol, alice)")).startswith("✓ PROVEN: above(carol, alice)")
    assert "Not removed" in await T(action="remove", id=k.facts()[0]["id"])
    # constraints
    k.add({"fact": "works_at(alice, acme)"}, "user")
    v.write(K.KG_RULES, v.read(K.KG_RULES) + "! one_employer: works_at(P, A), works_at(P, B), A != B.\n")
    r = await T(action="verify", claim="works_at(alice, globex)")
    assert r.startswith("✗ CONTRADICTED: works_at(alice, globex) (it would break a constraint)") and "one_employer" in r
    assert (await T(action="check")).startswith("No contradictions")
    k.add({"fact": "works_at(alice, globex)"}, "user")
    assert (await T(action="check")).startswith("1 contradiction")
    gid = next(f["id"] for f in k.facts() if f["fact"] == "works_at(alice, globex)")
    k.set_status([gid], "rejected")
    assert "rejected this fact before" in await T(action="add", fact="works_at(alice, globex)")
    # a broken line is reported with the file to fix
    v.write(K.KG_FACTS, v.read(K.KG_FACTS) + json.dumps({"id": "kx", "fact": "oops(", "status": "confirmed"}) + "\n")
    with pytest.raises(RuntimeError, match=r"fact kx: .*facts\.jsonl"):
        await T(action="verify", claim="above(carol, alice)")


def test_same_files_as_the_app(jade):
    """The facts file is written the way the app writes it, so a vault can move between them."""
    k = K.kg_of(jade)
    k.add({"fact": "likes(ann, tea)", "source": "chat"}, "jade")
    line = jade.vault.read(K.KG_FACTS).splitlines()[0]
    f = json.loads(line)
    assert list(f) == ["id", "fact", "status", "by", "source", "ts"] and f["fact"] == "likes(ann, tea)" and f["status"] == "proposed"
    assert "% Rules and constraints for Jade's knowledge graph" in jade.vault.read(K.KG_RULES)


async def test_verify_mode_is_a_prompt_line(jade, fake):
    from jade import prompt
    s = jade.session()
    tools = jade.tools.active(jade)
    assert "VERIFY MODE IS ON" not in prompt.build(jade, s, tools)
    jade.settings.kg_verify = True
    assert "VERIFY MODE IS ON" in prompt.build(jade, s, tools)


async def test_a_model_turn_verifies(jade, fake):
    K.kg_of(jade).add({"fact": "reports_to(bob, alice)"}, "user")
    fake.script = [{"content": "", "tool_calls": [("knowledge", {"action": "verify", "claim": "reports_to(bob, alice)"})]}, {"content": "✓ yes"}]
    s = jade.session()
    assert await s.ask("Does bob report to alice? verify it against the knowledge graph") == "✓ yes"
    tool_msg = next(m for m in s.messages if m.get("role") == "tool")
    assert str(tool_msg["content"]).startswith("✓ PROVEN: reports_to(bob, alice)")


def test_api(jade):
    c = TestClient(create_app(jade))
    h = {"authorization": f"Bearer {jade.settings.api_key}"} if jade.settings.api_key else {}
    assert c.post("/v1/knowledge/facts", json={"fact": "reports_to(bob, alice)"}, headers=h).json()["status"] == "confirmed"
    assert c.post("/v1/knowledge/verify", json={"claim": "reports_to(bob, alice)"}, headers=h).json()["text"].startswith("✓ PROVEN")
    assert c.post("/v1/knowledge/query", json={"q": "reports_to(X, alice)"}, headers=h).json()["text"].startswith("1 answer")
    assert c.get("/v1/knowledge/check", headers=h).json()["text"].startswith("No contradictions")
    K.kg_of(jade).add({"fact": "likes(ann, tea)"}, "jade")
    facts = c.get("/v1/knowledge/facts?status=proposed", headers=h).json()["facts"]
    assert [f["fact"] for f in facts] == ["likes(ann, tea)"]
    assert c.patch(f"/v1/knowledge/facts/{facts[0]['id']}", json={"status": "confirmed"}, headers=h).json()["changed"] == 1
    assert c.delete(f"/v1/knowledge/facts/{facts[0]['id']}", headers=h).json()["removed"] == 1
    assert c.post("/v1/knowledge/verify", json={"claim": "p(X :- y"}, headers=h).status_code == 400


def test_cli(tmp_path, fake):
    env = {**os.environ, "JADE_CONFIG": str(tmp_path / "c.toml"), "JADE_HOST": fake.url, "JADE_MODEL": "fake:1b", "JADE_EMBED_ON": "false"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "jade", "--vault", str(tmp_path / "v"), "kg", *a], capture_output=True, text=True, env=env, timeout=120)   # noqa: E731
    r = run("add", "reports_to(bob, alice)")
    assert r.returncode == 0 and "confirmed" in r.stdout, r.stdout + r.stderr
    r = run("verify", "reports_to(bob, alice)")
    assert r.returncode == 0 and r.stdout.startswith("✓ PROVEN"), r.stdout + r.stderr
    r = run("verify", "reports_to(alice, bob)")
    assert r.returncode == 2 and r.stdout.startswith("? UNKNOWN")   # the exit code says the verdict: 0 proven, 1 contradicted, 2 unknown
    assert run("facts").stdout.count("reports_to") == 1
    assert run("check").stdout.startswith("No contradictions")
    assert run("verify", "p(X :- y").returncode == 3
