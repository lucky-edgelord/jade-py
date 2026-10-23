"""MCP servers, configured the same way as the app: /Jade/mcp.json

    {"mcpServers": {"<name>": {"url": "https://…/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"},
                               "command": "npx", "args": ["-y", "some-server"], "env": {…}, "cwd": "…",
                               "timeout": 60, "enabled": true, "description": "…",
                               "exposure": "codemode|deferred|direct|hidden", "toolExposure": {"tool_or_pattern*": "direct"}}}}

The app can only reach streamable-HTTP servers; here a server may also be a local command (stdio). Header values
may use ${NAME} (from `secrets` in the config) or "!command" (the output of a shell command).

Tools are named mcp__<server>__<tool>. Exposure, per server or per tool:
  codemode (default)  callable from the codemode tool's JavaScript: await tools.mcp__s__t(args)
  deferred            an optional tool: the tool router or more_tools loads it when a chat needs it
  direct              always offered to the model, like a built-in tool
  hidden              registered nowhere
Resources: list_mcp_resources, list_mcp_resource_templates, read_mcp_resource.
Servers connect the first time a model call needs them and stay connected; edits to mcp.json apply on the next turn."""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .tools.registry import Tool, ToolError, truncate_head

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade")
MCP_PATH = "/Jade/mcp.json"
EXPOSURES = ("codemode", "deferred", "direct", "hidden")


def safe(s) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))


def tool_name(server: str, tool: str) -> str:
    return f"mcp__{safe(server)}__{safe(tool)}"


def _dump(x) -> Any:
    return x.model_dump(by_alias=True, exclude_none=True, mode="json") if hasattr(x, "model_dump") else x


class Server:
    def __init__(self, mgr: "MCP", name: str, cfg: dict):
        self.mgr, self.name, self.cfg = mgr, name, cfg
        self.state, self.error = "idle", ""
        self.client = None
        self.tools: list[dict] = []
        self.instructions = ""
        self.title = ""
        self.has_resources = False
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._ready: asyncio.Event | None = None

    def exposure_for(self, tool: str) -> str:
        te = self.cfg.get("toolExposure") or {}
        if tool in te:
            return te[tool]
        for pat, ex in te.items():
            if "*" in pat and fnmatch.fnmatchcase(tool, pat):
                return ex
        e = self.cfg.get("exposure")
        e = "codemode" if e == "codemode-deferred" else e
        return e if e in EXPOSURES else "codemode"

    async def _headers(self) -> dict:
        out = {}
        for k, v in (self.cfg.get("headers") or {}).items():
            out[k] = await self.mgr.resolve_value(v)
        return out

    async def _target(self):
        from mcp import StdioServerParameters
        if self.cfg.get("command"):
            env = {k: await self.mgr.resolve_value(v) for k, v in (self.cfg.get("env") or {}).items()}
            return StdioServerParameters(command=str(self.cfg["command"]), args=[str(a) for a in self.cfg.get("args") or []], env=env or None,
                                         cwd=self.cfg.get("cwd") or str(self.mgr.jade.vault.root))
        url = str(self.cfg.get("url") or "")
        if not re.match(r"^https?://", url):
            raise ValueError("needs a url (http:// or https://) or a command")
        if self.cfg.get("type") == "sse":
            raise ValueError('legacy SSE servers are not supported: use the server\'s streamable HTTP endpoint')
        h = await self._headers()
        if not h:
            return url
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        return streamable_http_client(url, http_client=create_mcp_http_client(headers=h))

    async def _run(self):
        from mcp import Client
        try:
            target = await self._target()
            async with Client(target, read_timeout_seconds=float(self.cfg.get("timeout") or 60)) as c:
                self.client = c
                tools, cursor = [], None
                while True:
                    r = await c.list_tools(cursor=cursor)
                    tools += [_dump(t) for t in r.tools]
                    cursor = getattr(r, "next_cursor", None)
                    if not cursor:
                        break
                self.tools = tools
                self.instructions = c.instructions or ""
                info = c.server_info
                self.title = (getattr(info, "title", None) or "") if info else ""
                caps = c.server_capabilities
                self.has_resources = bool(caps and getattr(caps, "resources", None))
                self.state = "connected"
                self._ready.set()
                await self._stop.wait()
        except BaseException as e:   # noqa: BLE001 (anyio wraps errors in groups)
            if isinstance(e, asyncio.CancelledError):
                raise
            while isinstance(e, BaseExceptionGroup) and e.exceptions:
                e = e.exceptions[0]
            self.state, self.error = "error", str(e) or type(e).__name__
            log.warning("mcp %s: %s", self.name, self.error)
        finally:
            self.client = None
            if self.state == "connected":
                self.state = "closed"
            self._ready.set()

    async def start(self, timeout: float = 30):
        self._stop, self._ready = asyncio.Event(), asyncio.Event()
        self.state, self.error = "connecting", ""
        self._task = asyncio.get_running_loop().create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            self.state, self.error = "error", f"no answer in {timeout:g}s"
            self._task.cancel()

    async def stop(self):
        if self._stop:
            self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
        self.state = "closed"


class MCP:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.servers: dict[str, Server] = {}
        self.cfg_text: str | None = None
        self.error = ""
        self.loop = None
        self.lock = asyncio.Lock()
        self.store: dict[str, dict] = {}   # codemode store(key, value) per chat

    # ---- config
    def read_config(self) -> dict:
        v = self.jade.vault
        if not v.is_file(MCP_PATH):
            return {"servers": {}, "autoEnableCodemode": True}
        try:
            j = json.loads(re.sub(r"(?m)^\s*//.*$", "", v.read(MCP_PATH)))
            self.error = ""
            return {"servers": j.get("mcpServers") or j.get("servers") or {}, "autoEnableCodemode": j.get("autoEnableCodemode") is not False}
        except ValueError as e:
            self.error = f"mcp.json is not valid JSON: {e}"
            return {"servers": {}, "autoEnableCodemode": True}

    def write_config(self, mutate) -> None:
        v = self.jade.vault
        j: dict = {"mcpServers": {}}
        if v.is_file(MCP_PATH):
            try:
                j = json.loads(re.sub(r"(?m)^\s*//.*$", "", v.read(MCP_PATH)))
            except ValueError:
                raise ToolError("fix the JSON in /Jade/mcp.json first") from None
        j.setdefault("mcpServers", {})
        mutate(j["mcpServers"])
        v.write(MCP_PATH, json.dumps(j, indent=2) + "\n")

    async def resolve_value(self, v) -> str:
        v = str(v if v is not None else "")
        if v.startswith("!"):
            from .tools.builtin import run_shell
            out, code = await run_shell(self.jade, v[1:], timeout=30)
            if code:
                raise ValueError(f"header command failed (exit {code})")
            return out.strip()
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(self.jade.settings.secrets.get(m.group(1), "")), v)

    # ---- connecting
    async def ensure(self) -> None:
        """Connect to the servers in mcp.json (again if it changed, or if the event loop is a new one)."""
        loop = asyncio.get_running_loop()
        v = self.jade.vault
        text = v.read(MCP_PATH) if v.is_file(MCP_PATH) else ""
        if text == self.cfg_text and loop is self.loop:
            return
        if self.lock._loop is not None and self.lock._loop is not loop:   # noqa: SLF001 (a lock from an old loop)
            self.lock = asyncio.Lock()
        async with self.lock:
            if text == self.cfg_text and loop is self.loop:
                return
            if loop is not self.loop:
                self.servers = {}          # connections of an old event loop are gone with it
            self.loop = loop
            self.cfg_text = text
            cfg = self.read_config()["servers"]
            want = {n: c for n, c in cfg.items() if isinstance(c, dict) and c.get("enabled", True) is not False}
            for n in [n for n in self.servers if n not in want or self.servers[n].cfg != want[n]]:
                await self.servers.pop(n).stop()
            new = [Server(self, n, c) for n, c in want.items() if n not in self.servers]
            for s in new:
                self.servers[s.name] = s
            if new:
                await asyncio.gather(*(s.start(float(s.cfg.get("connectTimeout") or 30)) for s in new))
            self.register()

    async def reconnect(self, name: str | None = None) -> None:
        self.cfg_text = None
        for n in list(self.servers):
            if name is None or n == name:
                await self.servers.pop(n).stop()
        await self.ensure()

    async def aclose(self):
        for s in list(self.servers.values()):
            await s.stop()
        self.servers = {}
        self.cfg_text = None

    def connected(self) -> list[Server]:
        return [s for s in self.servers.values() if s.state == "connected"]

    def all_tools(self) -> list[dict]:
        out = []
        for s in self.connected():
            for t in s.tools:
                out.append({**t, "server": s.name, "tool": t["name"], "name": tool_name(s.name, t["name"]), "exposure": s.exposure_for(t["name"])})
        return out

    def find(self, name: str) -> dict | None:
        return next((t for t in self.all_tools() if t["name"] == name), None)

    # ---- registering tools with Jade
    def register(self) -> None:
        reg = self.jade.tools
        for n in [n for n in reg.names() if n.startswith("mcp__")]:
            reg.remove(n)
        for t in self.all_tools():
            ex = t["exposure"]
            if ex == "hidden":
                continue
            params = t.get("inputSchema") if (t.get("inputSchema") or {}).get("type") else {"type": "object", "properties": {}}

            async def call(ctx, _name=t["name"], **args):
                return await self.call_text(_name, args)
            reg.add(Tool(t["name"], (t.get("description") or t["tool"])[:1024], params, call, core=ex == "direct", risky=True, hidden=ex == "codemode",
                         hints=f"{t['server']} {t['tool'].replace('_', ' ')} mcp", summary=""))

    # ---- calling
    async def call_raw(self, name: str, args: dict | None) -> dict:
        t = self.find(name)
        if not t:
            raise ToolError(f"Unknown MCP tool {name}. Connected servers: {', '.join(s.name for s in self.connected()) or 'none'}.")
        s = self.servers[t["server"]]
        r = await s.client.call_tool(t["tool"], args or {})
        return _dump(r)

    def save_blob(self, b64: str, mime: str | None, hint: str) -> str:
        ext = re.sub(r"[^a-z0-9]", "", ((mime or "").split("/")[1] if "/" in (mime or "") else "bin").replace("jpeg", "jpg"), flags=re.I) or "bin"
        base = f"/attachments/mcp/{self.jade.now().strftime('%Y-%m-%d')}-{safe(hint)[:40]}"
        p, i = f"{base}.{ext}", 2
        while self.jade.vault.exists(p):
            p, i = f"{base}-{i}.{ext}", i + 1
        self.jade.vault.write(p, base64.b64decode(b64 or ""))
        return p

    def result_text(self, res: dict, hint: str) -> str:
        parts = []
        for c in res.get("content") or []:
            ty = c.get("type")
            if ty == "text":
                parts.append(c.get("text", ""))
            elif ty in ("image", "audio"):
                parts.append(f"[{ty} saved to {self.save_blob(c.get('data'), c.get('mimeType'), hint)}]")
            elif ty == "resource":
                r = c.get("resource") or {}
                if r.get("text") is not None:
                    parts.append(f"[resource {r.get('uri')}]\n{r['text']}")
                else:
                    parts.append(f"[resource {r.get('uri')}{' saved to ' + self.save_blob(r['blob'], r.get('mimeType'), hint) if r.get('blob') else ''}]")
            elif ty == "resource_link":
                parts.append(f"[resource link {c.get('uri')}{' (' + c['name'] + ')' if c.get('name') else ''}]")
        if not parts and res.get("structuredContent") is not None:
            parts.append(json.dumps(res["structuredContent"], indent=1, ensure_ascii=False))
        return "\n".join(parts) or "(no content)"

    async def call_text(self, name: str, args: dict) -> str:
        res = await self.call_raw(name, args)
        text = self.result_text(res, name.split("__")[-1])
        if res.get("isError"):
            raise ToolError(text)
        return truncate_head(text, self.jade.settings.max_tool_output)

    # ---- the prompt
    def prompt_block(self, jade, session) -> str:
        srvs = self.connected()
        if not srvs:
            return ""
        allt = self.all_tools()
        rows = []
        for s in srvs:
            ts = [t for t in allt if t["server"] == s.name]
            ex = "/".join(dict.fromkeys(t["exposure"] for t in ts)) or "no tools"
            ins = re.sub(r"\s+", " ", s.instructions)[:300]
            rows.append(f"- {s.name} ({len(ts)} tools, {ex}){': ' + s.cfg['description'] if s.cfg.get('description') else ''}" + (f"\n  Server instructions: {ins}" if ins else ""))
        return ("MCP servers (connected):\n" + "\n".join(rows) + "\nUse codemode for codemode servers (await tools.mcp__<server>__<tool>(args) inside the script; "
                "searchTools/describeTool to discover), more_tools to load deferred tools, and call direct tools like any other tool.")


# ---------------------------------------------------------------- codemode
PRELUDE = r"""
const ALL_TOOLS = %CATALOG%;
const __NS = %NS%;
const __texts = [], __images = [];
const text = s => { __texts.push(String(s)); };
const image = b => { __images.push(b); };
const store = (key, value) => jade.call('store', { key, value });
const load = key => jade.call('load', { key });
const __tok = s => String(s).replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 1);
const searchTools = (q, n = 10) => { const w = __tok(q); return ALL_TOOLS.map(t => ({ t, s: w.filter(x => __tok(t.name + ' ' + (t.description || '')).includes(x)).length + (t.name.includes(String(q).replace(/\s+/g, '_')) ? 2 : 0) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, n).map(x => ({ name: x.t.name, description: (x.t.description || '').slice(0, 200) })); };
const describeTool = name => { const t = ALL_TOOLS.find(x => x.name === name); if (!t) throw new Error('no tool named ' + name); return t; };
const describeNamespace = ns => { const n = __NS[ns]; if (!n) throw new Error('no namespace ' + ns + '. Namespaces: ' + Object.keys(__NS).join(', ')); return n; };
const tools = new Proxy({}, { get: (_, n) => a => jade.call('calltool', { name: String(n), args: a || {} }) });
"""


async def codemode_tool(ctx, code: str = "", script: str = "", **kw):
    j = ctx.jade
    M: MCP = j.mcp
    src = str(code or script or "")
    if not src.strip():
        raise ToolError('codemode needs JavaScript in "code"')
    timeout = 60.0
    m = re.match(r"\s*//\s*@options\s*(\{.*\})", src)
    if m:
        try:
            timeout = max(1, min(600, float(json.loads(m.group(1)).get("timeout", 60))))
        except ValueError:
            pass
    reachable = [t for t in M.all_tools() if t["exposure"] in ("codemode", "direct")]
    builtins = [t for t in j.tools.active(j) if not t.name.startswith("mcp__") and t.name not in ("codemode", "more_tools")]
    catalog = [{"name": t["name"], "description": t.get("description", ""), "inputSchema": t.get("inputSchema"), "server": t["server"]} for t in reachable] + \
              [{"name": t.name, "description": t.describe(j)[:300], "inputSchema": t.parameters} for t in builtins]
    ns = {"mcp__" + safe(s.name): {"name": s.name, "description": s.cfg.get("description") or s.title, "instructions": s.instructions,
                                   "tools": [t["name"] for t in reachable if t["server"] == s.name]} for s in M.connected()}
    chat = ctx.session.id if ctx.session else "_"
    st = M.store.setdefault(chat, {})
    names_ok = {t["name"] for t in reachable} | {t.name for t in builtins}

    async def rpc(op, a):
        if op == "calltool":
            n = str(a.get("name") or "")
            if n.startswith("mcp__"):
                if n not in names_ok:
                    t = M.find(n)
                    raise ToolError(f"{n} is a {t['exposure']} tool: it cannot be called from codemode" if t else f"no tool named {n}. Use searchTools(\"…\")")
                return await M.call_raw(n, a.get("args") or {})
            if n not in names_ok:
                raise ToolError(f"no tool named {n}")
            return await j.run_tool(n, a.get("args") or {}, ctx.session)
        if op == "store":
            st[str(a.get("key"))] = a.get("value")
            return True
        if op == "load":
            return st.get(str(a.get("key")))
        from .bridge import jade_bridge
        return await jade_bridge(j, op, a, session=ctx.session)

    from .node import run_script
    pre = PRELUDE.replace("%CATALOG%", json.dumps(catalog, default=str)).replace("%NS%", json.dumps(ns, default=str))
    body = pre + "\nconst __r = await (async () => {\n" + src + "\n})();\nreturn { __r, __texts, __images };"
    r = await run_script(j, body, timeout=timeout, rpc=rpc)
    parts = []
    if r["out"].strip():
        parts.append(r["out"].rstrip())
    ret = r.get("ret") or {}
    parts += ret.get("__texts") or []
    for im in ret.get("__images") or []:
        if isinstance(im, dict) and im.get("data"):
            parts.append(f"[image saved to {M.save_blob(im['data'], im.get('mimeType'), 'codemode')}]")
    if ret.get("__r") is not None:
        v = ret["__r"]
        parts.append(v if isinstance(v, str) else json.dumps(v, indent=1, ensure_ascii=False))
    if r["code"] != 0:
        err = "\n".join(ln for ln in r["err"].split("\n") if not re.match(r"^\s+at\s", ln)).strip()
        raise ToolError(("\n".join(parts) + "\n" if parts else "") + f"codemode error: {err or 'exit ' + str(r['code'])}"[-3000:])
    out = "\n".join(parts) or "(no output: console.log or return what you need)"
    if len(out) > 20000:
        p = f"/tmp/codemode-{int(time.time())}.txt"
        j.vault.write(p, out)
        out = out[:12000] + f"\n\n…[{len(out) - 18000} characters cut; full output in {p}]…\n\n" + out[-6000:]
    return out


def _codemode_desc(j) -> str:
    M: MCP = j.mcp
    allt = M.all_tools()
    rows = []
    for s in M.connected():
        ts = [t for t in allt if t["server"] == s.name and t["exposure"] in ("codemode", "direct")]
        if ts:
            rows.append(f"- mcp__{safe(s.name)} ({len(ts)} tools){': ' + (s.cfg.get('description') or s.title) if (s.cfg.get('description') or s.title) else ''}")
    return ("Run JavaScript (Node) that can call tools, to use MCP servers. Call a tool with `await tools.<name>(args)`: MCP tools are named mcp__<server>__<tool> and resolve to "
            "{content, structuredContent, isError}; your built-in tools work too and resolve to their text (e.g. await tools.read({path: \"Inbox.md\"})). Run independent calls together "
            "with Promise.all. Find tools with searchTools(\"words\"), describeTool(name) (gives the inputSchema), describeNamespace(\"mcp__<server>\") and ALL_TOOLS. Only console.log output, "
            "text(...), the return value and image(block) reach you, so filter and summarise inside the script. store(key, value) / load(key) keep JSON between scripts in this chat. "
            "Optional first line: // @options {\"timeout\": 120}\nMCP servers:\n" + "\n".join(rows))


async def resources_tool(ctx, server: str = "", cursor: str = "", _tmpl: bool = False, **kw):
    M: MCP = ctx.jade.mcp
    srvs = [s for s in M.connected() if s.has_resources and (not server or s.name == server)]
    if not srvs:
        raise ToolError(f"{server} is not connected or has no resources" if server else "no connected server offers resources")
    out = {}
    for s in srvs:
        if _tmpl:
            r = _dump(await s.client.list_resource_templates(cursor=cursor or None))
            out[s.name] = {"resourceTemplates": r.get("resourceTemplates") or []}
        else:
            r = _dump(await s.client.list_resources(cursor=cursor or None))
            out[s.name] = {"resources": r.get("resources") or []}
        if r.get("nextCursor"):
            out[s.name]["nextCursor"] = r["nextCursor"]
    return json.dumps(out, indent=1, ensure_ascii=False)


async def templates_tool(ctx, server: str = "", cursor: str = "", **kw):
    return await resources_tool(ctx, server, cursor, _tmpl=True)


async def read_resource_tool(ctx, server: str = "", uri: str = "", **kw):
    M: MCP = ctx.jade.mcp
    s = M.servers.get(server)
    if not s or s.state != "connected":
        raise ToolError(f"server {server} is not connected")
    r = _dump(await s.client.read_resource(uri))
    parts = [c["text"] if c.get("text") is not None else f"[{c.get('mimeType') or 'binary'} saved to {M.save_blob(c.get('blob'), c.get('mimeType'), 'resource')}]" for c in r.get("contents") or []]
    return "\n\n".join(parts) or "(empty resource)"


async def mcp_admin_tool(ctx, action: str = "list", name: str = "", url: str = "", command: str = "", args=None, headers=None, exposure: str = "", **kw):
    """mcp: list, add, remove, enable, disable, reconnect servers in /Jade/mcp.json."""
    M: MCP = ctx.jade.mcp
    a = action or "list"
    if a == "list":
        await M.ensure()
        cfg = M.read_config()["servers"]
        if not cfg:
            return (M.error + "\n" if M.error else "") + "No MCP servers yet. Use action add (name + url, or name + command and args)."
        rows = []
        for n, c in cfg.items():
            s = M.servers.get(n)
            st = "disabled" if c.get("enabled") is False else (s.state if s else "idle")
            rows.append(f"- {n}: {st}{' (' + s.error + ')' if s and s.error else ''} · {len(s.tools) if s and s.state == 'connected' else 0} tools · {c.get('exposure') or 'codemode'} · "
                        f"{c.get('url') or (str(c.get('command')) + ' ' + ' '.join(map(str, c.get('args') or [])))}")
        return "\n".join(rows)
    if not name:
        raise ToolError("name is required")
    if a == "add":
        if not url and not command:
            raise ToolError("add needs url or command")
        if exposure and exposure not in EXPOSURES:
            raise ToolError(f"exposure must be one of {', '.join(EXPOSURES)}")
        c: dict = {"url": url} if url else {"command": command, "args": list(args or [])}
        if headers:
            c["headers"] = headers
        if exposure and exposure != "codemode":
            c["exposure"] = exposure
        M.write_config(lambda s: s.__setitem__(name, c))
    elif a == "remove":
        M.write_config(lambda s: s.pop(name, None))
    elif a in ("enable", "disable"):
        def f(s):
            if name not in s:
                raise ToolError(f"no server named {name}")
            s[name]["enabled"] = a == "enable"
        M.write_config(f)
    elif a == "reconnect":
        await M.reconnect(name)
    else:
        raise ToolError("action must be list, add, remove, enable, disable or reconnect")
    await M.ensure()
    s = M.servers.get(name)
    return f"{name}: {s.state if s else 'removed or disabled'}{' (' + s.error + ')' if s and s.error else ''}{f', {len(s.tools)} tools' if s and s.state == 'connected' else ''}."


def setup(jade: "Jade") -> None:
    import mcp  # noqa: F401  (the part needs the SDK)
    M = jade.mcp = MCP(jade)
    jade.prepare_hooks.append(M.ensure)
    jade.prompt_extras.append(M.prompt_block)
    jade.close_hooks.append(M.aclose)
    res = lambda j: any(s.has_resources for s in M.connected())   # noqa: E731
    jade.tools.add(Tool("codemode", _codemode_desc, {"type": "object", "properties": {"code": {"type": "string", "description": "JavaScript (the body of an async function; use await and return)"}}, "required": ["code"]},
                        codemode_tool, core=True, risky=True, enabled=lambda j: M.read_config()["autoEnableCodemode"] and any(t["exposure"] == "codemode" for t in M.all_tools())))
    jade.tools.add(Tool("list_mcp_resources", lambda j: f"List resources (files, records, documents) offered by MCP servers: {', '.join(s.name for s in M.connected() if s.has_resources)}. Returns JSON.",
                        {"type": "object", "properties": {"server": {"type": "string"}, "cursor": {"type": "string"}}}, resources_tool, core=False, enabled=res, hints="mcp resources list"))
    jade.tools.add(Tool("list_mcp_resource_templates", "List MCP resource URI templates.", {"type": "object", "properties": {"server": {"type": "string"}, "cursor": {"type": "string"}}},
                        templates_tool, core=False, enabled=res, hints="mcp resource templates"))
    jade.tools.add(Tool("read_mcp_resource", "Read one MCP resource by server and uri.", {"type": "object", "properties": {"server": {"type": "string"}, "uri": {"type": "string"}}, "required": ["server", "uri"]},
                        read_resource_tool, core=False, enabled=res, hints="mcp resource read"))
    jade.tools.add(Tool("mcp", "Manage MCP servers in /Jade/mcp.json: list (status and tool counts), add {name, url | command+args, headers?, exposure?}, remove, enable, disable, reconnect {name}.",
                        {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "add", "remove", "enable", "disable", "reconnect"]}, "name": {"type": "string"},
                                                          "url": {"type": "string"}, "command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
                                                          "headers": {"type": "object"}, "exposure": {"type": "string", "enum": list(EXPOSURES)}}, "required": ["action"]},
                        mcp_admin_tool, core=False, risky=True, hints="mcp server connect add model context protocol", summary="mcp: list, add or remove MCP servers"))
