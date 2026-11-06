"""The knowledge graph: facts, rules and constraints, and a reasoner that proves its answers, for verification.

The reasoner is the Rust engine from Jade Vault (rust/kg), shipped here as ``kg.wasm`` and run with wasmtime, so the app
and jade-py give the same answers (both pass Jade Vault's tests/kg_cases.json). Same files as the app:

- ``/Jade/Knowledge/facts.jsonl``: one fact per line, ``{"id","fact","status","by","source","ts"}``. status is confirmed
  (the user's own, or approved), proposed (Jade's, until the user approves) or rejected.
- ``/Jade/Knowledge/rules.dl``: rules and constraints, the user's to edit.

Jade's facts are always proposed; the user confirms them (``jade kg confirm``, the dashboard, or the API). Every answer
says when it rests on unconfirmed facts. Verify mode (``kg_verify``) asks Jade to check her factual claims.
"""
from __future__ import annotations

import json
import random
import re
import string
import threading
import time
from datetime import datetime, timezone
from importlib import resources
from typing import TYPE_CHECKING, Any

from .tools.registry import Tool, ToolContext, ToolError

if TYPE_CHECKING:
    from .core import Jade

KG_DIR = "/Jade/Knowledge"
KG_FACTS = KG_DIR + "/facts.jsonl"
KG_RULES = KG_DIR + "/rules.dl"
RULES_SEED = """% Rules and constraints for Jade's knowledge graph. Facts live in facts.jsonl.
%
%   rule:        head :- condition, condition.        (variables start with a capital letter)
%   constraint:  ! name: condition, condition.         (any match is a contradiction)
%   conditions:  relation(a, X)   not relation(a, X)   X = Y   X != Y   X < Y   X <= Y   X > Y   X >= Y
%
% Examples, switched off (delete the % to use one):
% knows(X, Y) :- knows(Y, X).
% above(X, Y) :- reports_to(X, Y).
% above(X, Z) :- reports_to(X, Y), above(Y, Z).
% ! one_birthday: born_on(P, A), born_on(P, B), A != B.
% ! no_self_report: reports_to(P, P).
% ! ends_after_start: starts(T, S), ends(T, E), E < S.
"""


class KgError(ToolError):
    def __init__(self, msg: str, code: str = ""):
        super().__init__(msg)
        self.code = code


class Engine:
    """The Rust reasoner (WebAssembly) behind a JSON call. One instance per process; calls are serialised."""

    _lock = threading.Lock()
    _inst: "Engine | None" = None

    def __init__(self) -> None:
        import wasmtime
        wasm = resources.files("jade").joinpath("kg.wasm").read_bytes()
        self._wt = wasmtime
        self.store = wasmtime.Store()
        module = wasmtime.Module(self.store.engine, wasm)
        inst = wasmtime.Instance(self.store, module, [])
        ex = inst.exports(self.store)
        self.mem, self.alloc, self.free, self.call_, self.out_len = ex["memory"], ex["kg_alloc"], ex["kg_free"], ex["kg_call"], ex["kg_out_len"]

    @classmethod
    def get(cls) -> "Engine":
        if cls._inst is None:
            cls._inst = Engine()
        return cls._inst

    def call(self, req: dict) -> dict:
        data = json.dumps(req).encode()
        with self._lock:
            try:
                p = self.alloc(self.store, len(data))
                self.mem.write(self.store, data, p)
                q = self.call_(self.store, p, len(data))
                out = json.loads(bytes(self.mem.read(self.store, q, q + self.out_len(self.store))).decode())
                self.free(self.store, p, len(data))
            except self._wt.WasmtimeError as e:   # a trap: start a fresh instance next time
                Engine._inst = None
                raise KgError(f"the knowledge engine stopped: {e}") from None
        if not out.get("ok"):
            raise KgError(out.get("error", "error"), out.get("code", ""))
        return out


def call(req: dict) -> dict:
    """One JSON request to the engine (see rust/kg/src/lib.rs for the ops)."""
    return Engine.get().call(req)


def _rel(s: str) -> str:
    r = re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")
    r = re.sub(r"^(\d)", r"r_\1", r)
    return r or "related_to"


def _new_id() -> str:
    return "k" + format(int(time.time() * 1000), "x") + "".join(random.choices(string.ascii_lowercase + string.digits, k=3))


class Knowledge:
    """The graph for one vault: its files, and a loaded engine handle that is rebuilt when they change."""

    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.handle = 0
        self.sig = ""
        self.err: str | None = None

    # ---------------------------------------------------------------- files
    def _read(self, p: str) -> str:
        v = self.jade.vault
        return v.read(p) if v.is_file(p) else ""

    def ensure(self) -> None:
        v = self.jade.vault
        if not v.is_file(KG_RULES):
            v.write(KG_RULES, RULES_SEED)
        if not v.is_file(KG_FACTS):
            v.write(KG_FACTS, "")

    def facts(self) -> list[dict]:
        out = []
        for i, line in enumerate(self._read(KG_FACTS).splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                f = json.loads(line)
            except ValueError:
                f = {"id": f"bad{i}", "fact": "", "status": "rejected", "bad": line}
            if f.get("id"):
                out.append(f)
        return out

    def save(self, facts: list[dict]) -> None:
        keep = ("id", "fact", "status", "by", "source", "ts")
        lines = [json.dumps({k: f.get(k, "") for k in keep}, ensure_ascii=False, separators=(",", ":")) for f in facts]
        self.jade.vault.write(KG_FACTS, "\n".join(lines) + ("\n" if lines else ""))
        self.sig = ""

    def rules(self) -> str:
        self.ensure()
        return self._read(KG_RULES)

    # ---------------------------------------------------------------- engine
    def load(self) -> int:
        self.ensure()
        rules, facts_txt = self._read(KG_RULES), self._read(KG_FACTS)
        sig = f"{len(rules)}:{len(facts_txt)}:{rules}\0{facts_txt}"
        if self.handle and sig == self.sig:
            return self.handle
        if self.handle:
            try:
                call({"op": "free", "handle": self.handle})
            except KgError:
                pass
            self.handle = 0
        try:
            r = call({"op": "load", "rules": rules, "facts": [f for f in self.facts() if f.get("fact")], "rules_source": "rules.dl"})
        except KgError as e:
            self.err, self.sig = str(e), ""
            where = KG_FACTS if str(e).startswith("fact ") else KG_RULES
            raise KgError(f"the knowledge graph has a problem: {e}. Fix it in {where}.", e.code) from None
        self.handle, self.sig, self.err = r["handle"], sig, None
        return self.handle

    def op(self, op: str, **kw: Any) -> dict:
        return call({"op": op, "handle": self.load(), **kw})

    # ---------------------------------------------------------------- changes
    def fact_text(self, a: dict) -> str:
        if a.get("fact"):
            return call({"op": "parse_fact", "fact": str(a["fact"])})["fact"]
        if a.get("subject") is None or a.get("relation") is None or a.get("object") is None:
            raise KgError('add needs "fact", like works_at(alice, acme), or subject, relation and object')
        val = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x).strip()
        f = call({"op": "format", "values": [val(a["subject"]), val(a["object"])]})["values"]
        return call({"op": "parse_fact", "fact": f"{_rel(a['relation'])}({f[0]}, {f[1]})"})["fact"]

    def add(self, a: dict, by: str) -> dict:
        """Add one fact. by "user" → confirmed; "jade" → proposed. Returns the fact and a note."""
        fact = self.fact_text(a)
        facts = self.facts()
        old = next((f for f in facts if f.get("fact") == fact), None)
        if old:
            if by == "user" and old.get("status") != "confirmed":
                old.update(status="confirmed", by="user")
                self.save(facts)
                return {**old, "note": "confirmed"}
            return {**old, "note": "the user rejected this fact before; it stays out" if old.get("status") == "rejected" else "already in the graph"}
        f = {"id": _new_id(), "fact": fact, "status": "confirmed" if by == "user" else "proposed", "by": by,
             "source": str(a.get("source") or ("entered" if by == "user" else "chat"))[:200], "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        facts.append(f)
        self.save(facts)
        try:
            self.load()
        except KgError:
            self.save([x for x in self.facts() if x["id"] != f["id"]])
            raise
        return {**f, "note": "added"}

    def set_status(self, ids: list[str], status: str) -> int:
        facts, want, n = self.facts(), set(ids), 0
        for f in facts:
            if f["id"] in want and f.get("status") != status:
                f["status"] = status
                n += 1
        if n:
            self.save(facts)
        return n

    def remove(self, ids: list[str]) -> int:
        facts, want = self.facts(), set(ids)
        keep = [f for f in facts if f["id"] not in want]
        if len(keep) != len(facts):
            self.save(keep)
        return len(facts) - len(keep)

    def add_rule(self, text: str, comment: str = "") -> str:
        text = text.strip()
        if not text:
            raise KgError('add_rule needs "rule", like  above(X, Z) :- reports_to(X, Y), above(Y, Z).  or  ! one_birthday: born_on(P, A), born_on(P, B), A != B.')
        cur = self.rules()
        nxt = cur.rstrip("\n") + "\n" + (f"% {comment.replace(chr(10), ' ')}\n" if comment else "") + (text if text.endswith(".") else text + ".") + "\n"
        r = call({"op": "parse_rules", "rules": nxt})
        self.jade.vault.write(KG_RULES, nxt)
        self.sig = ""
        self.load()
        return (f"Saved to {KG_RULES}. Now {r['rules']} rule{'' if r['rules'] == 1 else 's'} and {r['constraints']} constraint{'' if r['constraints'] == 1 else 's'}. "
                "Run check to see whether the facts break any constraint.")

    def stats_text(self) -> str:
        s = self.op("stats")
        rels = ", ".join(f"{r['name']}/{r['arity']} ({r['count']})" for r in s["relations"]) or "none yet"
        return (f"{s['facts']} facts ({s['confirmed']} confirmed, {s['proposed']} proposed), {s['derived']} more follow from {s['rules']} rule{'' if s['rules'] == 1 else 's'}; "
                f"{s['constraints']} constraint{'' if s['constraints'] == 1 else 's'}.\nRelations: {rels}")

    def verify_text(self, claims: list[str]) -> str:
        h, out = self.load(), []
        for c in claims:
            try:
                out.append(call({"op": "verify", "handle": h, "claim": c})["text"])
            except KgError as e:
                out.append(f"! {c}: {e}")
        return "\n\n".join(out)


ACTIONS = ["verify", "query", "why", "check", "add", "facts", "rules", "add_rule", "remove", "stats"]


def kg_of(jade: "Jade") -> Knowledge:
    k = getattr(jade, "knowledge", None)
    if k is None:
        k = jade.knowledge = Knowledge(jade)
    return k


async def knowledge_tool(ctx: ToolContext, action: str = "stats", **a: Any) -> str:
    k = kg_of(ctx.jade)
    action = (action or "stats").lower()
    if action not in ACTIONS:
        raise ToolError(f"unknown action {action}: use {', '.join(ACTIONS)}")
    if action == "add":
        items = a.get("facts") if isinstance(a.get("facts"), list) else [a]
        lines = []
        for it in items[:50]:
            it = {"fact": it, "source": a.get("source")} if isinstance(it, str) else {"source": a.get("source"), **it}
            try:
                r = k.add(it, "jade")
                lines.append(f"{r['fact']}: " + (f"proposed (id {r['id']})" if r["note"] == "added" else r["note"]))
            except KgError as e:
                lines.append(f"{it.get('fact') or it.get('subject')}: not added ({e})")
        return "\n".join(lines) + "\nYour facts are proposed until the user confirms them; answers say when they rest on unconfirmed facts."
    if action == "facts":
        st, q = a.get("status"), str(a.get("match") or "").lower()
        lst = [f for f in k.facts() if f.get("fact") and (not st or f.get("status") == st)
               and (not q or q in f["fact"].lower() or q in str(f.get("source", "")).lower())]
        lim = min(200, int(a.get("limit") or 60))
        if not lst:
            return "No facts match."
        return (f"{len(lst)} fact{'s' if len(lst) > 1 else ''}{f' (first {lim})' if len(lst) > lim else ''}:\n"
                + "\n".join(f"- [{f['id']}] {f['fact']} ({f.get('status')}{', ' + f['source'] if f.get('source') else ''})" for f in lst[:lim]))
    if action == "rules":
        return "```\n" + k.rules() + "\n```"
    if action == "add_rule":
        return k.add_rule(str(a.get("rule") or a.get("text") or ""), str(a.get("comment") or ""))
    if action == "remove":
        ids = [str(x) for x in (a.get("ids") or ([a["id"]] if a.get("id") else []))]
        facts = {f["id"]: f for f in k.facts()}
        mine = [i for i in ids if facts.get(i, {}).get("by") == "jade" and facts.get(i, {}).get("status") == "proposed"]
        n = k.remove(mine)
        refused = [i for i in ids if i not in mine]
        return f"Removed {n}." + (f" Not removed: {', '.join(refused)} (only your own unconfirmed facts; the user removes the rest)." if refused else "")
    if action == "stats":
        return k.stats_text()
    if action == "check":
        return k.op("check")["text"]
    if action == "why":
        return k.op("why", fact=str(a.get("fact") or a.get("claim") or ""))["text"]
    if action == "query":
        return k.op("query", q=str(a.get("q") or a.get("query") or ""), limit=int(a.get("limit") or 50))["text"]
    claims = [str(c) for c in a["claims"][:30]] if isinstance(a.get("claims"), list) else [str(a.get("claim") or a.get("fact") or "")]
    return k.verify_text(claims)


DESCRIPTION = ("The knowledge graph: facts (relation(a, b)), rules and constraints, and a reasoner that proves answers. verify: check a claim (claim) "
               "or several (claims): ✓ PROVEN with the facts and rules it rests on, ✗ CONTRADICTED (with what it conflicts with), or ? UNKNOWN (the graph is "
               "open-world: missing is not false). Say which facts were unconfirmed. query: answers to conditions with variables, e.g. "
               "q=\"above(bob, X), works_at(X, C)\". why: the proof of one fact. check: every constraint violation in the graph. add: propose a fact "
               "(fact=\"works_at(alice, acme)\", or subject, relation, object; or facts=[...]) with source (a note path, or \"chat\"); your facts stay "
               "proposed until the user confirms them. facts: list (status, match). rules / add_rule (rule, comment): read or add a rule or constraint. "
               "remove: your own unconfirmed facts (id). stats. Names: lowercase words with _, or \"quoted text\"; variables start with an uppercase letter. "
               "The knowledge-graph skill explains how to extract facts and verify answers.")
PARAMS = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ACTIONS},
    "claim": {"type": "string", "description": "verify: one claim, e.g. works_at(alice, acme) or not works_at(alice, acme)"},
    "claims": {"type": "array", "items": {"type": "string"}, "description": "verify: several claims"},
    "q": {"type": "string", "description": "query: conditions, e.g. above(bob, X), works_at(X, C)"},
    "fact": {"type": "string", "description": "add / why: one fact, e.g. works_at(alice, acme)"},
    "facts": {"type": "array", "items": {"type": "string"}, "description": "add: several facts"},
    "subject": {"type": "string"}, "relation": {"type": "string"}, "object": {"type": "string"},
    "source": {"type": "string", "description": "add: where the fact comes from: a note path or \"chat\""},
    "rule": {"type": "string", "description": "add_rule: a rule or a constraint (! name: …)"}, "comment": {"type": "string"},
    "status": {"type": "string", "enum": ["confirmed", "proposed", "rejected"]}, "match": {"type": "string"},
    "id": {"type": "string"}, "limit": {"type": "number"}}, "required": ["action"]}

SKILL_PATH, SKILL_VER = "/Jade/skills/knowledge-graph/SKILL.md", 1
SKILL = f"""---
name: knowledge-graph
description: Use the knowledge tool to verify claims against the user's knowledge graph with proofs, to check notes for contradictions, and to extract facts from notes or chat as proposals. Use when the user asks to verify, fact-check, prove or explain something, to check their notes for consistency, to "add to the graph", or when verify mode is on.
---
<!-- knowledge-graph-skill v{SKILL_VER}: written by jade-py; delete this line to keep your own edits -->
The knowledge graph is facts like `works_at(alice, acme)` plus rules and constraints, and a reasoner that proves answers. The point is verification: the reasoner decides, not you, and every answer comes with the facts and rules it rests on.

**Verify a claim.** Translate the claim into the graph's terms first: look at the relations it uses (`knowledge action=stats`) and use those names. Then `knowledge action=verify claim="works_at(alice, acme)"` (or `claims=[…]` for several). Report what came back, faithfully:
- ✓ **proven**: give the proof in one line (the facts it rests on). If it rests on unconfirmed facts, say so.
- ✗ **contradicted**: say what it conflicts with.
- ? **unknown**: the graph does not know. Never turn unknown into false or true. You may say what related facts the graph has.
Always show the user the exact claim you checked, so they can see your translation was right.

**Check consistency.** `knowledge action=check` lists every constraint the facts break, with the facts involved. To check notes, extract their facts first (below), then check.

**Extract facts** from a note or from what the user tells you: `knowledge action=add facts=["works_at(alice, acme)", …] source="People/Alice.md"`. Rules for good facts:
- One relation per fact; short snake_case relation names, reused across the graph (`works_at`, not `is_employed_by` one day and `works_for` the next). Check `stats` for the names already in use.
- Lowercase names for things (`alice`, `acme`); "quoted text" for anything with spaces or capitals; plain numbers for numbers; dates as "YYYY-MM-DD" text, so they compare correctly.
- Only what the text actually says. No guesses, no opinions.
- Your facts are **proposed** until the user confirms them (`jade kg confirm`, or the dashboard's Knowledge panel). Tell them how many you proposed.

**Rules and constraints** make the graph useful: `knowledge action=add_rule rule="above(X, Z) :- reports_to(X, Y), above(Y, Z)."` or a constraint `rule="! one_birthday: born_on(P, A), born_on(P, B), A != B."`. Suggest one when the user's domain has an obvious rule, explain it in a sentence, and add it if they agree.

**Verify mode** (setting kg_verify): before stating facts the graph could cover, verify them and mark each claim ✓, ✗ or ? in your answer.
"""


def seed_skill(jade: "Jade") -> None:
    v = jade.vault
    cur = v.read(SKILL_PATH) if v.is_file(SKILL_PATH) else ""
    if cur:
        m = re.search(r"knowledge-graph-skill v(\d+)", cur)
        if not m or int(m.group(1)) >= SKILL_VER:
            return
    v.write(SKILL_PATH, SKILL)


def summary(jade: "Jade") -> str:
    s = "knowledge: the knowledge graph (facts, rules, constraints) and a reasoner that proves answers: verify claims, query, check for contradictions"
    if jade.settings.kg_verify:
        s += (" (VERIFY MODE IS ON: before you state facts the graph could cover, check them with knowledge verify, and mark each such claim "
              "in your answer ✓ proven, ✗ contradicted or ? unknown; say when a proof rests on unconfirmed facts)")
    return s


def setup(jade: "Jade") -> None:
    Engine.get()   # fail here (and skip the tool) if wasmtime is missing
    kg_of(jade)
    jade.tools.add(Tool("knowledge", DESCRIPTION, PARAMS, knowledge_tool, core=False, summary=summary,   # type: ignore[arg-type]
                        hints="knowledge graph fact facts verify verification prove proof true false contradiction contradict consistent consistency check claim rule rules logic datalog reasoner"))
    try:
        kg_of(jade).ensure()
        seed_skill(jade)
    except Exception:
        pass
