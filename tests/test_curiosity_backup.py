import gzip
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jade import backup as B
from jade import curiosity as CU

NOW = datetime(2026, 10, 23, 10, 15, tzinfo=ZoneInfo("UTC"))


def test_engine_learns_and_ranks():
    st = CU.new_state()
    ids = [a["id"] for a in CU.ACTS]
    sc = CU.scores(st, ids, NOW)
    assert {s["id"] for s in sc} == set(ids) and sc[-1]["id"] == "rest"      # untried ones are optimistic, rest is flat
    for _ in range(6):
        CU.update(st, "explore", 0.8, NOW)
    for r in (0.0, 1.0, 0.0, 1.0, 0.0, 1.0):
        CU.update(st, "connect", r, NOW)
    a, c = CU.act(st, "explore"), CU.act(st, "connect")
    assert a["rA"] > 0.7 and a["n"] == 6 and st["total"] == 12
    assert CU.lp(a) >= 0 and c["eF"] > a["eF"]                               # the noisy one keeps a high error
    assert CU.reward("rest", {}, 1) == CU.REST_REWARD
    assert CU.reward("explore", {"created": 0}, 1.0) <= 0.2 * 0.25 + 1e-9 + 0.25 * 0.2   # self score cannot lift far above measured
    assert CU.measured({"created": 2, "links": 3}) > CU.measured({"changed": 1})
    assert CU.pick(sc, 0.2, rand=lambda: 0.0) == sc[0]["id"]


def test_engine_windows():
    o = {"min": 45, "max": 180, "from": "08:00", "to": "22:00"}
    late = NOW.replace(hour=21, minute=59)
    t = CU.next_at(late, o, rand=lambda: 0.5)
    assert CU.in_window(t, "08:00", "22:00") and t.hour == 8 and t.date() > late.date()
    assert CU.in_window(NOW.replace(hour=23), "22:00", "06:00") and not CU.in_window(NOW, "22:00", "06:00")
    assert 45 * 60 <= CU.next_delay(45, 180, rand=lambda: 0.0, nudge="later") - 0 and CU.next_delay(45, 180, rand=lambda: 1.0, nudge="sooner") <= 112.5 * 60 + 60


async def test_wake_runs_and_records(jade, fake):
    jade.settings.cu_tools = "notes"
    jade.vault.write("/Jade/CURIOSITY.md", CU.CU_SEED.replace("<!-- specific things you would like her to research or prepare when she has time -->", "- tides on Mars"))
    fake.script = [
        {"tool_calls": [("curiosity", {"action": "choose", "activity": "explore", "intent": "look into tides"})]},
        {"tool_calls": [("write", {"path": "/Jade/Curiosity/Explorations/Tides.md", "content": "# Tides\nSee [[Moon]] and [[Mars]]."}),
                        ("web_search", {"query": "mars tides"})]},
        {"tool_calls": [("curiosity", {"action": "report", "summary": "wrote a tides note", "self_score": 0.9, "next": "sooner", "notify": "tides note ready"})]},
        {"content": "Done."},
    ]
    seen = []
    jade.notifiers.append(lambda t, b: seen.append((t, b)))
    e = await jade.curiosity.wake(manual=True)
    assert e["id"] == "explore" and e["measured"]["created"] == 1 and e["measured"]["links"] == 2 and e["reward"] > 0.3
    assert ("✦ Jade", "tides note ready") in seen
    prompt = fake.requests[0]["messages"][-1]["content"]
    assert "tides on Mars" in prompt and "notes and memory tools" in prompt
    sent = {t["function"]["name"] for t in fake.requests[0].get("tools") or []}
    assert "web_search" not in sent and "curiosity" in sent
    s = jade.session(jade.state["cu_session"])
    assert any("not available right now" in str(m.get("content")) for m in s.messages if m.get("role") == "tool")   # web_search was refused
    assert jade.state["cu_next"] > 0 and jade.curiosity.today()["n"] == 1
    assert "Wake-ups so far: 1" in await jade.run_tool("curiosity", {"action": "status"})
    with pytest.raises(RuntimeError, match="only available during"):
        await jade.run_tool("curiosity", {"action": "choose", "activity": "rest"})


async def test_silent_rest_leaves_no_trace(jade, fake):
    fake.script = [{"tool_calls": [("curiosity", {"action": "choose", "activity": "rest"})]}, {"content": "Resting."}]
    e = await jade.curiosity.wake(manual=True)
    assert e["id"] == "rest" and e["note"] == "no report was made"
    assert jade.session(jade.state["cu_session"]).messages == []


def test_encrypt_roundtrip_and_format(jade):
    jade.vault.write("/Notes/a.md", "hello [[b]]")
    jade.vault.write("/Notes/bin.dat", b"\x00\x01\x02")
    jade.settings.api_key = "sk-secret"
    data = jade.backups.build("full")
    assert data["app"] == "jade-vault" and data["format"] == 1 and data["vault"]["files"]["/Notes/a.md"] == "hello [[b]]"
    assert data["vault"]["files"]["/Notes/bin.dat"] == {"base64": "AAEC"} and "/Notes" in data["vault"]["dirs"]
    assert "api_key" not in data["settings"] and not any(p.startswith("/Jade/.index") for p in data["vault"]["files"])
    cfg = jade.backups.build("config")
    assert all(p.lower().startswith(("/jade/", "/agents.md")) for p in cfg["vault"]["files"]) and "/Notes/a.md" not in cfg["vault"]["files"]
    m = jade.backups.make({"kind": "full", "keys": True, "encrypt": True, "pass": "pw"})
    assert m["name"].endswith(".enc.json") and b"sk-secret" not in m["bytes"]
    back = B.decode(m["bytes"], "pw")
    assert back["settings"]["api_key"] == "sk-secret" and back["vault"]["files"]["/Notes/a.md"] == "hello [[b]]"
    with pytest.raises(ValueError, match="Wrong passphrase"):
        B.decode(m["bytes"], "nope")
    assert B.decode(gzip.compress(json.dumps({"app": "jade-vault"}).encode())) == {"app": "jade-vault"}


def test_decrypts_app_style_envelope():
    """An envelope made like the app's WebCrypto code (PBKDF2-SHA256 + AES-GCM, ciphertext||tag, standard base64)."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = b"s" * 16, b"i" * 12
    key = hashlib.pbkdf2_hmac("sha256", b"pw", salt, 1000, 32)
    inner = gzip.compress(json.dumps({"app": "jade-vault", "format": 1}).encode())
    env = {"app": "jade-vault-encrypted", "v": 1, "iter": 1000, "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
           "data": base64.b64encode(AESGCM(key).encrypt(iv, inner, None)).decode()}
    assert B.decode(json.dumps(env).encode(), "pw")["format"] == 1


async def test_backup_to_server_and_restore(jade, tmp_path):
    from jade.backup_server import make_server
    token = "t" * 32
    srv = make_server("127.0.0.1", 0, str(tmp_path / "srv"), token, 30, 64, None, False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        jade.settings.ab_url = f"http://127.0.0.1:{srv.server_address[1]}"
        jade.settings.ab_token = token
        jade.vault.write("/Notes/keep.md", "v1")
        r = await jade.run_tool("backup", {"action": "now"})
        assert "Backup uploaded: jade-vault-full-" in r
        listing = await jade.run_tool("backup", {"action": "list"})
        name = listing.split()[0]
        assert name.endswith(".json.gz")
        assert "Last success" in await jade.run_tool("backup", {"action": "status"})
        jade.vault.write("/Notes/keep.md", "changed")
        data = B.decode(await jade.backups.fetch(name))
        out = jade.backups.restore(data)
        assert out["files"] >= 1 and jade.vault.read("/Notes/keep.md") == "v1"
        jade.settings.ab_token = "wrong-token-wrong-token-wrong"
        bad = await jade.backups.run()
        assert not bad["ok"] and "401" in bad["msg"] and jade.state["ab_fails"] == 1
    finally:
        srv.shutdown()
