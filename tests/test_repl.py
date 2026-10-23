from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from jade.repl import Repl


async def test_repl_turns_commands_and_step_mode(jade, fake, capsys):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        r = Repl(jade)
        fake.script = [{"content": "", "tool_calls": [("notes_search", {"query": "Inbox"})]}, {"content": "Here is your **inbox**."}]
        await r.turn("show my inbox")
        assert await r.command("/tools") and await r.command("/context") and await r.command("/chats")
        assert await r.command("/step on") and r.step_mode
        fake.script = [{"content": "", "tool_calls": [("bash", {"command": "echo stepped"})]}, {"content": "done"}]
        inp.send_text("\n")       # Enter: run the pending call
        await r.turn("run it")
        assert not await r.command("/exit")
    out = capsys.readouterr().out
    assert "notes_search" in out and "inbox" in out and "bash" in out and "stepped" in out
    assert any(m.get("role") == "tool" and "stepped" in m["content"] for m in r.s.messages)
