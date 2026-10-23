"""The `jade` command."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import __version__

app = typer.Typer(add_completion=False, no_args_is_help=False, help="Jade, the agent from Jade Vault: chat, REPL, HTTP API, tools.")
state: dict = {}


def _jade():
    from .core import Jade
    o = state.get("opts", {})
    return Jade(o.get("vault"), config=o.get("config"), **{k: v for k, v in o.items() if k in ("model", "provider", "host") and v})


@app.callback(invoke_without_command=True)
def main_opts(ctx: typer.Context,
              vault: Optional[Path] = typer.Option(None, "--vault", "-v", envvar="JADE_VAULT", help="vault folder (default from config, else ~/JadeVault)"),
              model: Optional[str] = typer.Option(None, "--model", "-m", help="model name"),
              provider: Optional[str] = typer.Option(None, "--provider", "-p", help="ollama, ollama-cloud, llamacpp, openai, groq, openrouter…"),
              host: Optional[str] = typer.Option(None, "--host", help="Ollama address"),
              config: Optional[Path] = typer.Option(None, "--config", help="config file (default ~/.config/jade/config.toml)"),
              version: bool = typer.Option(False, "--version", help="print the version")):
    if version:
        typer.echo(f"jade {__version__}")
        raise typer.Exit()
    state["opts"] = {"vault": vault, "model": model, "provider": provider, "host": host, "config": config}
    if ctx.invoked_subcommand is None:
        from .repl import main
        main(_jade())


@app.command()
def repl(chat: Optional[str] = typer.Option(None, "--chat", "-c", help="continue this chat"), step: bool = typer.Option(False, "--step", help="start in step mode")):
    """Chat in the terminal (also what plain `jade` does)."""
    from .repl import main
    main(_jade(), chat, step)


@app.command()
def ask(message: list[str] = typer.Argument(..., help="the message (or - to read stdin)"), chat: Optional[str] = typer.Option(None, "--chat", "-c"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="only print the answer"), as_json: bool = typer.Option(False, "--json", help="print events as JSON lines")):
    """One turn: send a message, print the answer."""
    from . import events as E
    text = sys.stdin.read() if message == ["-"] else " ".join(message)
    j = _jade()
    s = j.session(chat)

    async def go():
        async for ev in s.run(text):
            if as_json:
                print(json.dumps(ev.to_dict(), ensure_ascii=False), flush=True)
            elif isinstance(ev, E.Text) and not quiet:
                print(ev.text, end="", flush=True)
            elif isinstance(ev, E.ToolStart) and not quiet:
                print(f"\n▍{ev.name} {json.dumps(ev.arguments, ensure_ascii=False)[:120]}", file=sys.stderr, flush=True)
            elif isinstance(ev, E.Error):
                print(f"\nerror: {ev.message}", file=sys.stderr)
            elif isinstance(ev, E.Done):
                if quiet:
                    print(ev.text)
                elif not as_json:
                    print()
        await j.aclose()
    asyncio.run(go())
    if not as_json and not quiet:
        print(f"(chat {s.id})", file=sys.stderr)


@app.command()
def serve(host: Optional[str] = typer.Option(None, help="listen address (default 127.0.0.1)"), port: Optional[int] = typer.Option(None, help="port (default 8760)")):
    """Run the HTTP API."""
    from .api import serve as run
    run(_jade(), host, port)


@app.command()
def tool(name: str, args: Optional[str] = typer.Argument(None, help="arguments as JSON, or key=value pairs"), extra: list[str] = typer.Argument(None)):
    """Run one of Jade's tools yourself, e.g. jade tool notes_search query=inbox"""
    parts = ([args] if args else []) + (extra or [])
    if len(parts) == 1 and parts[0].strip().startswith("{"):
        a = json.loads(parts[0])
    else:
        a = {}
        for p in parts:
            k, _, v = p.partition("=")
            try:
                a[k] = json.loads(v)
            except ValueError:
                a[k] = v
    j = _jade()

    async def go():
        try:
            print(await j.run_tool(name, a))
        finally:
            await j.aclose()
    asyncio.run(go())


@app.command()
def tools():
    """List Jade's tools."""
    j = _jade()
    for t in j.tools.active(j):
        typer.echo(f"{t.name:15} {'' if t.core else '(optional) '}{t.describe(j)[:100]}")


@app.command()
def chats(limit: int = 20):
    """List saved chats."""
    for c in _jade().chats()[:limit]:
        typer.echo(f"{c['id']}  {c['messages']:>3}  {c['title']}")


@app.command()
def init(vault: Path = typer.Argument(Path.home() / "JadeVault"), model: str = typer.Option("qwen3:8b"), provider: str = typer.Option("ollama"),
         host: str = typer.Option("http://localhost:11434")):
    """Make a vault (with the starter notes and skills) and a config file."""
    from .settings import Settings
    from .vault import Vault
    v = Vault(vault)
    s = Settings(vault=v.root, model=model, provider=provider, host=host)
    p = s.save()
    typer.echo(f"vault: {v.root}\nconfig: {p}\nNow run: jade")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
