"""The model router: laya (on Ollaya) picks the model for each chat turn from router_models."""
from __future__ import annotations

import json

from jade import events as E
from jade import modelrouter as MR

from test_parity import J, server  # noqa: F401  (the fixture)

MODELS = [{"model": "ollama:small:1b", "note": "quick everyday chat"}, {"model": "big:7b", "note": "coding and hard reasoning"}]


def laya(seen):
    def systemone(h, body):
        b = json.loads(body)
        seen.append(b)
        q = b["questions"]["q"]
        pick = q["criteria"][1] if "code" in b["state"] else q["criteria"][0]
        return 200, "application/json", J({"model": b["model"], "answers": {"q": {"choice": pick, "confidence": 0.8, "probabilities": {c: (0.8 if c == pick else 0.2) for c in q["criteria"]}}}})
    return systemone


def setup(jade, server, **kw):
    st = jade.settings
    st.router_on, st.router_models, st.ollaya_host, st.ollaya_model = True, MODELS, server.url, "laya"
    for k, v in kw.items():
        setattr(st, k, v)


async def test_router_picks_the_model_for_each_turn(jade, fake, server):
    seen = []
    server.routes[("POST", "/v1/systemone")] = laya(seen)
    setup(jade, server)
    s = jade.session()
    evs = [ev async for ev in s.run("hello there")]
    r = [e for e in evs if isinstance(e, E.Routed)]
    assert len(r) == 1 and (r[0].provider, r[0].model, r[0].confidence) == ("ollama", "small:1b", 0.8) and not r[0].error
    assert fake.requests[-1]["model"] == "small:1b"
    assert seen[-1]["model"] == "laya" and seen[-1]["questions"]["q"]["criteria"] == ["quick everyday chat", "coding and hard reasoning"]
    assert "hello there" in seen[-1]["state"]

    # a coding question goes to the big model, for every model call of the turn (one decision per turn)
    fake.script = [{"content": "", "tool_calls": [("think", {"problem": "x"})]}, {"content": "done"}]
    jade.settings.think_on = False
    n = len(fake.requests)
    evs = [ev async for ev in s.run("write code for a parser")]
    assert sum(isinstance(e, E.Routed) for e in evs) == 1
    assert [q["model"] for q in fake.requests[n:]] == ["big:7b", "big:7b"]
    assert len(seen) == 2
    assert "Previous reply" in seen[-1]["state"]


async def test_router_falls_back_to_the_main_model(jade, fake, server):
    server.routes[("POST", "/v1/systemone")] = lambda h, b: (500, "application/json", J({"error": "laya is loading"}))
    setup(jade, server)
    s = jade.session()
    evs = [ev async for ev in s.run("hi")]
    r = [e for e in evs if isinstance(e, E.Routed)][0]
    assert r.error and "laya is loading" in r.error and r.model == "fake:1b"
    assert fake.requests[-1]["model"] == "fake:1b" and any(isinstance(e, E.Done) for e in evs)

    # unsure: below router_min_confidence the main model answers
    seen = []
    server.routes[("POST", "/v1/systemone")] = laya(seen)
    jade.settings.router_min_confidence = 0.9
    evs = [ev async for ev in s.run("hi again")]
    r = [e for e in evs if isinstance(e, E.Routed)][0]
    assert "unsure" in r.error and fake.requests[-1]["model"] == "fake:1b"


async def test_router_off_or_background(jade, fake, server):
    seen = []
    server.routes[("POST", "/v1/systemone")] = laya(seen)
    setup(jade, server, router_on=False)
    s = jade.session()
    evs = [ev async for ev in s.run("hi")]
    assert not any(isinstance(e, E.Routed) for e in evs) and not seen
    # a background turn (only some tools allowed, e.g. a curiosity wake-up) keeps the main model
    jade.settings.router_on = True
    s2 = jade.session()
    s2.allow_tools = {"memory"}
    [ev async for ev in s2.run("look around")]
    assert not seen and fake.requests[-1]["model"] == "fake:1b"
    s3 = jade.session()
    s3.route = False   # heartbeat, schedules, flows and messenger chats
    [ev async for ev in s3.run("write code")]
    assert not seen
    # the decider can be another decision model than ollaya_model
    jade.settings.router_decider = "laya-small"
    [ev async for ev in jade.session().run("hi")]
    assert seen[-1]["model"] == "laya-small"


def test_parse_spec(jade):
    st = jade.settings
    assert MR.parse_spec(st, "ollama:qwen3:8b") == ("ollama", "qwen3:8b")
    assert MR.parse_spec(st, "qwen3:8b") == (st.provider, "qwen3:8b")
    assert MR.parse_spec(st, "openrouter:anthropic/claude-sonnet-4.5") == ("openrouter", "anthropic/claude-sonnet-4.5")
    assert MR.parse_spec(st, "groq:") == ("groq", st.model)
    st.router_on, st.router_models, st.ollaya_model = True, ["a", {"model": "b", "note": "big"}], "laya"
    assert st.router_models[0] == {"model": "a"} and MR.options(st) == [{"model": "a", "note": "a"}, {"model": "b", "note": "big"}] and MR.router_ready(st)
    st.router_models = ["only-one"]
    assert not MR.router_ready(st)
