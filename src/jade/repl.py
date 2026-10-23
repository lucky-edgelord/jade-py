"""`jade repl`: chat with Jade in the terminal. Replies stream as they come; tool calls show as they run.

Type a message, or a command:
  /help                 this list                     /new          a new chat
  /chats                saved chats                   /open <id>    continue one
  /step [on|off]        step mode: Jade stops before each tool call (Enter runs it, s skips, e edits, q stops)
  /approve [auto|risky|all]  ask before tools run    /model [name] show or set the model
  /tools                the tools Jade has            /tool <name> {json}   run one yourself
  /memory               what she remembers            /context      what the prompt costs
  /system               the instructions she gets     /skill:<name> [text]  use a skill
  /exit                 leave (Ctrl-D works too)
"""
from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text as RText

from . import events as E
from . import prompt as P
from .core import Jade
from .session import Session, estimate_tokens

ACCENT = "#1fe5a5"


class Repl:
    def __init__(self, jade: Jade, session: Session | None = None, step: bool = False):
        self.jade = jade
        self.s = session or jade.session()
        self.step_mode = step
        self.console = Console(highlight=False)
        hist = jade.settings.data_path / "repl_history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        self.prompt = PromptSession(history=FileHistory(str(hist)))
        jade.approver = self._approve

    # ---------------------------------------------------------------- approvals (settings.approve)
    async def _approve(self, call, tool) -> bool:
        self.console.print(Panel(RText(json.dumps(call.arguments, indent=1, ensure_ascii=False)[:1500]), title=f"run {call.name}?", border_style="yellow"))
        with patch_stdout():
            a = (await self.prompt.prompt_async("allow? [Y/n] ")).strip().lower()
        return a in ("", "y", "yes")

    # ---------------------------------------------------------------- output
    def banner(self) -> None:
        st = self.jade.settings
        self.console.print(f"[bold {ACCENT}]JADE[/] · {st.provider}:{st.model} · vault {self.jade.vault.root}")
        self.console.print(f"[dim]chat {self.s.id} · /help for commands{' · step mode on' if self.step_mode else ''}[/]")

    async def show(self, stream) -> None:
        reply = ""
        live = Live(Markdown(""), console=self.console, refresh_per_second=12, transient=False)
        live.start()
        try:
            async for ev in stream:
                if isinstance(ev, E.Text):
                    reply += ev.text
                    live.update(Markdown(reply))
                elif isinstance(ev, E.Thinking):
                    pass
                elif isinstance(ev, E.ToolStart):
                    if reply:
                        live.stop(); live = Live(Markdown(""), console=self.console, refresh_per_second=12); live.start(); reply = ""
                    args = json.dumps(ev.arguments, ensure_ascii=False)
                    self.console.print(f"[{ACCENT}]▍{ev.name}[/] [dim]{args[:160]}{'…' if len(args) > 160 else ''}[/]")
                elif isinstance(ev, E.ToolProgress):
                    self.console.print(f"[dim]  {ev.line[:200]}[/]")
                elif isinstance(ev, E.ToolEnd):
                    first = ev.output.strip().split("\n")[0][:160]
                    self.console.print(f"  [{'red' if ev.error else 'dim'}]{'✗' if ev.error else '✓'} {first}{' …' if len(ev.output) > len(first) else ''}[/] [dim]{ev.ms} ms[/]")
                elif isinstance(ev, E.Compacted):
                    self.console.print(f"[dim]  (summarised {ev.dropped} older messages to make room)[/]")
                elif isinstance(ev, E.Error):
                    self.console.print(f"[red]{ev.message}[/]")
        finally:
            live.stop()

    # ---------------------------------------------------------------- a turn
    async def turn(self, text: str) -> None:
        if not self.step_mode:
            try:
                await self.show(self.s.run(text))
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.s.cancel()
                self.console.print("[yellow]stopped[/]")
            return
        st = self.s.stepper(text)
        while not st.done:
            if st.pending:
                c = st.pending[0]
                self.console.print(Panel(RText(json.dumps(c.arguments, indent=1, ensure_ascii=False)[:2000]), title=f"next: {c.name}  ({len(st.pending)} pending)", border_style=ACCENT))
                with patch_stdout():
                    a = (await self.prompt.prompt_async("[Enter] run · s skip · e edit · q stop > ")).strip().lower()
                if a == "q":
                    self.s.cancel()
                elif a == "s":
                    st.skip(c.id)
                    continue
                elif a == "e":
                    with patch_stdout():
                        raw = await self.prompt.prompt_async("arguments (JSON): ", default=json.dumps(c.arguments, ensure_ascii=False))
                    try:
                        st.edit(c.id, json.loads(raw))
                    except ValueError as e:
                        self.console.print(f"[red]not JSON: {e}[/]")
                        continue
            await self.show(st.stream())

    # ---------------------------------------------------------------- commands
    async def command(self, line: str) -> bool:
        cmd, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        j, st = self.jade, self.jade.settings
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd == "help":
            self.console.print(__doc__.split("\n", 2)[2])
        elif cmd == "new":
            self.s = j.session()
            self.console.print(f"[dim]new chat {self.s.id}[/]")
        elif cmd == "chats":
            for c in j.chats()[:20]:
                self.console.print(f"{c['id']}  [dim]{c['messages']:>3} msgs[/]  {c['title']}")
        elif cmd == "open":
            try:
                self.s = j.session(rest)
                self.console.print(f"[dim]opened {self.s.title}[/]")
                for m in self.s.messages[-6:]:
                    if m.get("role") in ("user", "assistant") and (m.get("display") or m.get("content")):
                        self.console.print(f"[bold]{m['role']}[/]: {(m.get('display') or m['content'])[:300]}")
            except (KeyError, ValueError) as e:
                self.console.print(f"[red]{e}[/]")
        elif cmd == "step":
            self.step_mode = (rest != "off") if rest else not self.step_mode
            self.console.print(f"step mode {'on' if self.step_mode else 'off'}")
        elif cmd == "approve":
            if rest:
                st.approve = rest
            self.console.print(f"approve: {st.approve}")
        elif cmd == "model":
            if rest:
                st.model = rest
                j.provider.model = rest
            self.console.print(f"{st.provider}:{st.model}")
            if not rest:
                try:
                    self.console.print("[dim]" + ", ".join(await j.provider.models()) + "[/]")
                except Exception as e:
                    self.console.print(f"[dim]({e})[/]")
        elif cmd == "tools":
            for t in j.tools.active(j):
                self.console.print(f"[{ACCENT}]{t.name}[/]{'' if t.core else ' [dim](optional)[/]'}  {t.describe(j)[:110]}")
        elif cmd == "tool":
            name, _, arg = rest.partition(" ")
            try:
                out = await j.run_tool(name, json.loads(arg) if arg.strip() else {}, self.s)
                self.console.print(out)
            except Exception as e:
                self.console.print(f"[red]{e}[/]")
        elif cmd == "memory":
            self.console.print(Markdown(j.vault.read("/Jade/Memory.md")))
        elif cmd == "system":
            tools = j.router.select(self.s, "")
            self.console.print(P.build(j, self.s, tools))
        elif cmd == "context":
            tools = j.router.select(self.s, "")
            sp = P.build(j, self.s, tools)
            td = sum(estimate_tokens(json.dumps(t.schema(j))) for t in tools)
            ms = estimate_tokens(self.s.messages)
            self.console.print(f"system prompt ~{estimate_tokens(sp)} tokens · {len(tools)} tools ~{td} · messages ~{ms} · window {st.num_ctx}"
                               + (f" · last prompt {self.s.meta.get('last_prompt_tokens')} (model's count)" if self.s.meta.get("last_prompt_tokens") else ""))
        elif cmd.startswith("skill:"):
            name = cmd.split(":", 1)[1]
            try:
                body = j.skills.load(name, rest)
            except ValueError as e:
                self.console.print(f"[red]{e}[/]")
                return True
            await self.turn(f"Use the skill {name}.\n\n{body}" + (f"\n\n{rest}" if rest else ""))
        else:
            self.console.print(f"[red]unknown command /{cmd}[/] (/help)")
        return True

    async def run(self) -> None:
        self.banner()
        while True:
            try:
                with patch_stdout():
                    line = await self.prompt.prompt_async([("fg:" + ACCENT + " bold", "jade› ")])
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/") and not line.startswith("//"):
                if not await self.command(line):
                    break
                continue
            await self.turn(line[1:] if line.startswith("//") else line)
        await self.jade.aclose()


def main(jade: Jade, session_id: str | None = None, step: bool = False) -> None:
    asyncio.run(Repl(jade, jade.session(session_id) if session_id else None, step).run())


__all__ = ["Repl", "main", "shlex", "Path"]
