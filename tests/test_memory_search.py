import json

from jade.embeddings import vhash


def test_hash_matches_the_app():
    # values from the app's JavaScript (vmemHash in embed.js), so the app and Python share one vectors.db
    want = {"": "wvjl67o8030", "hello": "19jqrdkps125", "About you\u0001The user likes tea (2026-10-23)": "c469bamc1s15",
            "émoji 🚀 ünïcode": "oi0v0f5ibvg", "x" * 5000: "ymivxkut0y3uw"}
    for s, h in want.items():
        assert vhash(s) == h, (s[:20], vhash(s), h)


async def test_embeddings_index_and_hybrid_search(tmp_path, fake, monkeypatch):
    from jade import Jade
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "none.toml"))
    j = Jade(tmp_path / "v", data_dir=tmp_path / "d", host=fake.url, model="fake:1b", journal=False, embed_on=True)
    j.memory.save("The user grows tomatoes on the balcony", "about")
    j._memory_changed()
    out = await j.memory_search("tomatoes balcony")
    assert "tomatoes" in out
    st = j.embeddings.status()
    assert st["dim"] == 3 and any(c["name"] == "memory" and c["embedded"] >= 1 for c in st["collections"])
    r = await j.run_tool("embeddings", {"action": "index"})
    assert "notes" in r and "passages" in r
    r = await j.run_tool("embeddings", {"action": "search", "query": "spaced repetition", "collection": "notes"})
    assert "Inbox" in r
    r = await j.run_tool("embeddings", {"action": "add", "collection": "research", "texts": ["Quantum dots glow", "Bananas are berries"], "source": "web"})
    assert "Stored 2" in r
    r = await j.run_tool("embeddings", {"action": "search", "query": "berries", "collection": "research", "mode": "keyword"})
    assert "Bananas" in r and "Quantum" not in r
    assert "Removed 2" in await j.run_tool("embeddings", {"action": "remove", "collection": "research"})
    assert "Cosine similarity" in await j.run_tool("embeddings", {"action": "compare", "a": "aaa", "b": "aab"})
    # the index is a real file in the vault, readable by any sqlite3 with sqlite-vec
    assert j.vault.is_file("/Jade/.index/vectors.db")
    # forgetting removes it from the index on the next search
    j.memory.forget("tomatoes")
    j._memory_changed()
    assert "tomatoes" not in (await j.memory_search("tomatoes balcony"))


async def test_journal_and_chat_history(tmp_path, fake, monkeypatch):
    from jade import Jade
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "none.toml"))
    j = Jade(tmp_path / "v", data_dir=tmp_path / "d", host=fake.url, model="fake:1b", journal=True, embed_on=True)
    s = j.session()
    fake.script = [{"content": "Sure, I'll remember your telescope."}]
    await s.ask("I just bought a Dobsonian telescope for stargazing")
    fake.script = [{"content": "SUMMARY:\n- The user bought a telescope; talked about stargazing.\n\nREMEMBER:\n- Owns a Dobsonian telescope"}]
    await s.close()
    day = j.now().date().isoformat()
    jt = j.vault.read(f"/Jade/Journal/Journal {day}.md")
    assert "telescope" in jt and "#journal" in jt and "## " in jt
    assert "Dobsonian" in j.vault.read("/Jade/Memory.md")
    assert s.meta["journaled"] == len(s.messages)
    assert "telescope" in await j.memory_search("telescope")
    # chat history
    r = json.loads((await j.run_tool("chat_history", {"action": "list"}, s)))
    assert r["chats"][0]["session"] == s.id
    r = await j.run_tool("chat_history", {"action": "read", "session": "current"}, s)
    assert "Dobsonian" in r and '"sender": "User"' in r
    r = await j.run_tool("chat_history", {"action": "search", "query": "Dobsonian telescope"}, s)
    assert s.id in r and '"keyword": true' in r
