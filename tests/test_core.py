import asyncio
import json

import pytest

from jade import Done, Jade, ModelDone, Text, ToolEnd, ToolStart
from jade.providers import FallbackProvider, OllamaProvider, OpenAIProvider
from jade.vault import Vault, VaultError


# ---------------------------------------------------------------- vault
def test_vault_seed_links_tags_search(tmp_path):
    v = Vault(tmp_path / "v")
    assert v.is_file("/Welcome.md") and v.is_file("/Jade/SOUL.md") and v.is_file("/Jade/skills/daily-review/SKILL.md")
    assert v.resolve("Linking Notes") == "/guides/Linking Notes.md"
    assert v.resolve("linking-notes") == "/guides/Linking Notes.md"   # loose name match
    L = v.links("/Welcome.md")
    assert "/guides/Linking Notes.md" in L["outgoing"] and "start" in L["tags"]
    assert "/Welcome.md" in v.links("/guides/Linking Notes.md")["backlinks"]
    assert v.search("spaced repetition")[0]["path"] == "/Inbox.md"
    assert any(r["path"] == "/Todo List.md" for r in v.search("#tasks"))
    assert v.write("/x/../../../../etc/evil", "x") == "/etc/evil" and (v.root / "etc" / "evil").is_file()   # ".." stops at the vault root
    v.write("/a/b/New.md", "# New\n[[Welcome]] #fresh")
    assert "/a/b/New.md" in v.links("/Welcome.md")["backlinks"] and "fresh" in v.index().tags


def test_vault_symlink_escape(tmp_path):
    v = Vault(tmp_path / "v")
    (tmp_path / "outside").mkdir()
    (v.root / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(VaultError):
        v.write("/link/x.md", "nope")


def test_memory(jade):
    m = jade.memory
    assert "Saved" in m.save("The user's cat is called Miso", "about")
    assert "Already" in m.save("the user's cat is called miso", "about")
    assert "Miso" in m.search_text("cat")
    assert "Miso" in jade.memory.for_prompt()[0]
    assert "Forgot 1" in m.forget("Miso")
    assert "Miso" not in jade.vault.read("/Jade/Memory.md")


# ---------------------------------------------------------------- the agent loop
async def test_turn_with_tool_calls(jade, fake):
    fake.script = [
        {"content": "", "tool_calls": [("write", {"path": "/Ideas.md", "content": "# Ideas\n- [[Welcome]]"})]},
        {"content": "", "tool_calls": [("notes_search", {"query": "Ideas"})]},
        {"content": "Made [[Ideas]]."},
    ]
    s = jade.session()
    evs = [e async for e in s.run("make an ideas note")]
    kinds = [e.type for e in evs]
    assert kinds.count("tool_start") == 2 and kinds.count("tool_end") == 2 and kinds[-1] == "done"
    assert evs[-1].text == "Made [[Ideas]]."
    assert jade.vault.read("/Ideas.md").startswith("# Ideas")
    ends = [e for e in evs if isinstance(e, ToolEnd)]
    assert "Successfully wrote" in ends[0].output and "/Ideas.md" in ends[1].output
    # the model saw the tool results and the user message carried a time stamp
    last = fake.requests[-1]["messages"]
    assert any(m["role"] == "tool" and "/Ideas.md" in m["content"] for m in last)
    assert last[1]["role"] == "user" and last[1]["content"].startswith("[")
    assert fake.requests[0]["messages"][0]["role"] == "system" and "Jade" in fake.requests[0]["messages"][0]["content"]
    # saved and reloadable
    s2 = jade.session(s.id)
    assert s2.title.startswith("make an ideas note") and len(s2.messages) == len(s.messages)
    assert jade.chats()[0]["id"] == s.id


async def test_streaming_and_ask(jade, fake):
    fake.script = [{"content": "Hello there choom", "thinking": "hmm"}]
    s = jade.session()
    seen = []
    ans = await s.ask("hi", on_event=seen.append)
    assert ans == "Hello there choom"
    assert "".join(e.text for e in seen if isinstance(e, Text)) == "Hello there choom"
    assert any(e.type == "thinking" for e in seen)


def test_sync_chat(jade, fake):
    fake.script = [{"content": "sync answer"}]
    assert jade.chat("hello") == "sync answer"


async def test_stepper(jade, fake):
    fake.script = [
        {"content": "", "tool_calls": [("bash", {"command": "echo one"}), ("bash", {"command": "echo two"})]},
        {"content": "done stepping"},
    ]
    s = jade.session()
    st = s.stepper("run two commands")
    evs = await st.step()
    md = [e for e in evs if isinstance(e, ModelDone)][0]
    assert len(md.tool_calls) == 2 and len(st.pending) == 2 and not st.done
    st.edit(st.pending[0].id, {"command": "echo edited"})
    evs = await st.step()
    assert "edited" in [e for e in evs if isinstance(e, ToolEnd)][0].output
    st.skip(reason="not needed")
    assert not st.pending
    evs = await st.step()
    assert st.done and st.final == "done stepping"
    tool_msgs = [m for m in s.messages if m["role"] == "tool"]
    assert "edited" in tool_msgs[0]["content"] and "Not run: not needed" in tool_msgs[1]["content"]


async def test_real_shell(jade):
    out = await jade.run_tool("bash", {"command": "pwd; python3 -c 'print(6*7)'; echo $VAULT"})
    assert str(jade.vault.root) in out and "42" in out
    out = await jade.run_tool("bash", {"command": "exit 3"})
    assert "exited with code 3" in out
    out = await jade.run_tool("bash", {"command": "sleep 5", "timeout": 1})
    assert "timed out" in out and "code 124" in out


async def test_files_tools(jade):
    assert "Successfully wrote" in await jade.run_tool("write", {"path": "Plan", "content": "# Plan\nstep one\n"})
    assert jade.vault.is_file("/Plan.md")
    assert "line 2" in await jade.run_tool("edit", {"path": "/Plan.md", "oldText": "step one", "newText": "step 1"})
    assert "step 1" in await jade.run_tool("read", {"path": "Plan"})
    with pytest.raises(RuntimeError, match="Could not find"):
        await jade.run_tool("edit", {"path": "/Plan.md", "oldText": "nope", "newText": "x"})
    big = "\n".join(f"line {i}" for i in range(3000))
    jade.vault.write("/big.txt", big)
    r = await jade.run_tool("read", {"path": "/big.txt", "offset": 10, "limit": 5})
    assert r.startswith("line 9") and "more lines" in r
    assert "Backlinks" in await jade.run_tool("note_links", {"note": "Linking Notes"})
    assert "notes" in await jade.run_tool("notes_search", {"query": ""})


async def test_memory_and_skill_tools(jade):
    assert "Saved" in await jade.run_tool("memory", {"action": "save", "text": "Prefers tea over coffee", "section": "preferences"})
    assert "tea" in await jade.run_tool("memory", {"action": "search", "text": "tea"})
    assert "Skill: daily-review" in await jade.run_tool("skill", {"name": "daily-review"})
    assert "daily-review" in await jade.run_tool("find_skill", {"query": "plan my day review"})


async def test_approval(jade, fake):
    jade.settings.approve = "risky"
    asked = []
    jade.approver = lambda call, tool: asked.append(call.name) or False
    fake.script = [{"content": "", "tool_calls": [("bash", {"command": "touch should_not_exist"})]}, {"content": "ok"}]
    await jade.session().ask("do it")
    assert asked == ["bash"] and not jade.vault.exists("/should_not_exist")
    jade.approver = lambda call, tool: True
    fake.script = [{"content": "", "tool_calls": [("bash", {"command": "touch made_it"})]}, {"content": "ok"}]
    await jade.session().ask("do it")
    assert jade.vault.exists("/made_it")


async def test_custom_tool(jade, fake):
    @jade.tool(description="Add two numbers.")
    def add(a: int, b: int) -> str:
        return str(a + b)

    fake.script = [{"content": "", "tool_calls": [("add", {"a": 2, "b": 3})]}, lambda body: {"content": "sum is " + body["messages"][-1]["content"]}]
    assert await jade.session().ask("add") == "sum is 5"
    assert any(t["function"]["name"] == "add" for t in fake.requests[0]["tools"]) or "add" in jade.router.hidden


async def test_errors_reach_the_model(jade, fake):
    fake.script = [{"content": "", "tool_calls": [("read", {"path": "/missing.md"}), ("nonexistent_tool", {})]},
                   lambda body: {"content": " | ".join(m["content"][:40] for m in body["messages"] if m["role"] == "tool")}]
    ans = await jade.session().ask("read it")
    assert "no such file" in ans and "no tool named" in ans


async def test_text_tool_call_fallback(jade, fake):
    fake.script = [{"content": json.dumps({"name": "notes_search", "arguments": {"query": "Inbox"}})}, {"content": "found it"}]
    s = jade.session()
    assert await s.ask("find inbox") == "found it"
    assert any(m["role"] == "tool" and m["name"] == "notes_search" for m in s.messages)


async def test_compaction(jade, fake):
    jade.settings.num_ctx = 4000
    s = jade.session()
    for i in range(30):
        s.messages.append({"role": "user", "content": f"message {i} " + "words " * 120})
        s.messages.append({"role": "assistant", "content": "reply " * 50})
    fake.script = [{"content": "- the user sent many messages about words"}, {"content": "still here"}]
    evs = [e async for e in s.run("and now?")]
    assert any(e.type == "compacted" for e in evs) and "many messages" in s.summary
    assert len(s.messages) < 60 and s.messages[0]["role"] == "user"
    assert "many messages" in fake.requests[-1]["messages"][0]["content"]


async def test_max_steps(jade, fake):
    jade.settings.max_steps = 3
    fake.default = lambda body: {"content": "", "tool_calls": [("notes_search", {"query": "x"})]}
    evs = [e async for e in jade.session().run("loop forever")]
    assert evs[-1].type == "done" and any(e.type == "error" and "max_steps" in e.message for e in evs)


# ---------------------------------------------------------------- providers
async def test_openai_provider_assembles_tool_calls(fake):
    fake.script = [{"content": "abc", "tool_calls": [("read", {"path": "/Welcome.md"})]}]
    p = OpenAIProvider(fake.url + "/v1", "m")
    chunks = [c async for c in p.chat([{"role": "user", "content": "x"}], [{"type": "function", "function": {"name": "read", "parameters": {}}}])]
    assert "".join(c.text for c in chunks) == "abc"
    tc = [t for c in chunks for t in c.tool_calls]
    assert tc[0].name == "read" and tc[0].arguments == {"path": "/Welcome.md"} and tc[0].id == "c0"
    assert chunks[-1].usage.prompt_tokens == 50
    assert await p.models() == ["fake-oai"]


async def test_openai_message_conversion(jade, fake, tmp_path):
    j = Jade(tmp_path / "v2", data_dir=tmp_path / "d2", provider=OpenAIProvider(fake.url + "/v1", "m"), embed_on=False)
    fake.script = [{"content": "", "tool_calls": [("notes_search", {"query": "x"})]}, {"content": "fine"}]
    assert await j.session().ask("go") == "fine"
    msgs = fake.requests[-1]["messages"]
    asst = [m for m in msgs if m["role"] == "assistant"][0]
    tool = [m for m in msgs if m["role"] == "tool"][0]
    assert tool["tool_call_id"] == asst["tool_calls"][0]["id"]


async def test_fallback(fake):
    p = FallbackProvider([OllamaProvider("http://127.0.0.1:9", "a"), OllamaProvider(fake.url, "b")])
    fake.script = [{"content": "from the second"}]
    out = "".join([c.text async for c in p.chat([{"role": "user", "content": "x"}])])
    assert out == "from the second" and p.last.host == fake.url


async def test_provider_error_ends_turn(jade, fake):
    fake.script = [{"status": 500, "error": "model exploded"}]
    evs = [e async for e in jade.session().run("hi")]
    assert any(e.type == "error" and "model exploded" in e.message for e in evs) and evs[-1].type == "done"
