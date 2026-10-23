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
def daemon(api: bool = typer.Option(False, "--api", help="also serve the HTTP API")):
    """Run schedules, the heartbeat and curiosity in the foreground (systemd-friendly)."""
    from .scheduler import Daemon
    j = _jade()
    j.notifiers.append(lambda t, b: print(f"[notify] {t}: {b}", flush=True))
    if api:
        from .api import serve as run
        run(j)
        return
    try:
        asyncio.run(Daemon(j).run())
    except KeyboardInterrupt:
        pass


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


flow_app = typer.Typer(help="Automation flows (/Jade/Flows/*.flow.json).", no_args_is_help=True)
app.add_typer(flow_app, name="flow")


@flow_app.command("list")
def flow_list():
    """List flows."""
    j = _jade()
    for f in j.flows.list():
        typer.echo(f"{f['name']:30} {'INVALID ' + f['_bad'] if f.get('_bad') else j.flows.trigger_text(f)}  ({len(f.get('steps') or [])} steps)")


@flow_app.command("run")
def flow_run(name: str, input: Optional[str] = typer.Argument(None, help="input as JSON (or text); - reads stdin"),   # noqa: A002
             as_json: bool = typer.Option(False, "--json", help="print the run record as JSON")):
    """Run a flow and print its output."""
    raw = sys.stdin.read() if input == "-" else input
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = raw
    j = _jade()

    async def go():
        try:
            return await j.flows.run_by_name(name, data, via="shell")
        finally:
            await j.aclose()
    run = asyncio.run(go())
    if as_json:
        print(json.dumps(run, ensure_ascii=False, default=str, indent=1))
    else:
        out = run.get("output")
        if run["status"] == "ok":
            print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str, indent=1))
        else:
            print(j.flows.run_text(run), file=sys.stderr)
    raise typer.Exit(0 if run["status"] == "ok" else 1)


@flow_app.command("runs")
def flow_runs(limit: int = 10):
    """Recent flow runs."""
    from datetime import datetime
    for r in _jade().flows.recent()[:limit]:
        typer.echo(f"{datetime.fromtimestamp(r['started']):%Y-%m-%d %H:%M:%S}  {r['flow']}: {r['status']}  via {r['via']}{'  ' + str(r['error'])[:100] if r.get('error') else ''}")


backup_app = typer.Typer(help="Backups: to a Jade backup server (ab_url, ab_token) or a file.", no_args_is_help=True)
app.add_typer(backup_app, name="backup")


@backup_app.command("now")
def backup_now():
    """Back up to the server now."""
    r = asyncio.run(_jade().backups.run(manual=True))
    typer.echo(f"{r['name']}: {r['msg']}" if r["ok"] else f"failed: {r['msg']}")
    raise typer.Exit(0 if r["ok"] else 1)


@backup_app.command("list")
def backup_list():
    """Backups on the server."""
    for b in asyncio.run(_jade().backups.list()):
        typer.echo(f"{b['name']}  {b.get('size', 0)}")


@backup_app.command("save")
def backup_save(path: Path, kind: str = typer.Option("full", help="full or config"), keys: bool = typer.Option(False, help="include API keys (forces encryption)"),
                passphrase: Optional[str] = typer.Option(None, "--passphrase", envvar="JADE_BACKUP_PASS", help="encrypt with this passphrase")):
    """Write a backup file (the app can restore it)."""
    j = _jade()
    m = j.backups.make({"kind": kind, "keys": keys, "encrypt": bool(passphrase) or keys, "pass": passphrase or ""})
    path.write_bytes(m["bytes"])
    typer.echo(f"{path}: {m['files']} files, {len(m['bytes'])} bytes")


@backup_app.command("restore")
def backup_restore(source: str = typer.Argument(..., help="a backup file, or the name of one on the server"),
                   passphrase: Optional[str] = typer.Option(None, "--passphrase", envvar="JADE_BACKUP_PASS"),
                   keep: bool = typer.Option(False, "--keep-existing", help="do not overwrite files that exist"), yes: bool = typer.Option(False, "--yes", "-y")):
    """Restore a backup into the vault (files are added or overwritten; nothing is deleted)."""
    from .backup import decode
    j = _jade()
    raw = Path(source).read_bytes() if Path(source).is_file() else asyncio.run(j.backups.fetch(source))
    if b"jade-vault-encrypted" in raw[:200] and not passphrase:
        passphrase = typer.prompt("Passphrase", hide_input=True)
    data = decode(raw, passphrase or j.settings.ab_pass)
    n = len(data.get("vault", {}).get("files") or {})
    if not yes and not typer.confirm(f"Restore {n} files ({data.get('kind')}, {data.get('exportedAt')}) into {j.vault.root}?"):
        raise typer.Exit(1)
    r = j.backups.restore(data, overwrite=not keep)
    typer.echo(f"restored {r['files']} files ({r['skipped']} skipped), {r['chats']} chats")


@app.command("backup-server")
def backup_server(host: str = "0.0.0.0", port: int = 8787, folder: Path = typer.Option(Path("./backups"), "--dir"), token_file: Path = Path("./jade-backup.token"),
                  keep: int = 30, init: bool = typer.Option(False, "--init", help="make the token file and folder, print the token")):
    """Run the backup server (the same one the app uploads to)."""
    import secrets as _s

    from .backup_server import make_server
    if init:
        if token_file.exists():
            typer.echo(f"{token_file} already exists; keeping it.")
            return
        tok = _s.token_urlsafe(32)
        token_file.write_text(tok + "\n")
        token_file.chmod(0o600)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        typer.echo(f"Token saved to {token_file}:\n\n{tok}")
        return
    import os as _os
    tok = _os.environ.get("JADE_BACKUP_TOKEN") or (token_file.read_text().strip() if token_file.exists() else "")
    if len(tok) < 20:
        raise typer.BadParameter("no usable token: run with --init first")
    srv = make_server(host, port, str(folder), tok, keep, 1024, None, True)
    typer.echo(f"Jade backup server on http://{host}:{port}  folder {srv.store.root}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


@app.command()
def curiosity(now: bool = typer.Option(False, "--now", help="wake her up now")):
    """Curiosity: show the status, or start a wake-up."""
    j = _jade()

    async def go():
        try:
            if now:
                e = await j.curiosity.wake(manual=True)
                return f"{e['id']}: reward {e['reward']} · {e.get('note') or ''}" if e else "did not run"
            return await j.run_tool("curiosity", {"action": "status"})
        finally:
            await j.aclose()
    typer.echo(asyncio.run(go()))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
