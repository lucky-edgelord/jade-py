"""Jade's built-in tools: files, the real shell, tasks, the web, notes, memory and skills."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import signal
from datetime import date
from urllib.parse import urlencode, urlparse

import httpx

from ..vault import VaultError, is_note, note_name
from .registry import Registry, Tool, ToolContext, truncate_head, truncate_tail

BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp|ico|pdf|zip|xdc|gz|tar|db|sqlite3?|mp3|mp4|wav|ogg|webm|tflite|bin|so|exe)$", re.I)


def _squash(s) -> str:
    return re.sub(r"\s+", " ", s)


def _is_text(b: bytes) -> bool:
    if b"\x00" in b[:4096]:
        return False
    try:
        b[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _path(ctx: ToolContext, p: str, *, must_exist: bool = True) -> str:
    v = ctx.vault
    vp = v.norm(p)
    if must_exist and not v.exists(vp):
        if not vp.endswith(".md") and v.is_file(vp + ".md"):
            return vp + ".md"
        r = v.resolve(p) if not str(p).startswith("/") or "/" not in str(p).strip("/") else None
        if r:
            return r
        raise ToolError(f"{vp}: no such file. Use notes_search or bash ls to find it.")
    return vp


from .registry import ToolError  # noqa: E402


# ---------------------------------------------------------------- files
def read(ctx: ToolContext, path: str, offset: int | None = None, limit: int | None = None, **_):
    p = _path(ctx, path)
    if ctx.vault.is_dir(p):
        return "\n".join(ctx.vault.list(p)) or "(empty folder)"
    raw = ctx.vault.read_bytes(p)
    if BINARY_EXT.search(p) and not _is_text(raw):
        return f"[binary file {p}, {len(raw) / 1024:.1f} KB]"
    lines = raw.decode("utf-8", errors="replace").split("\n")
    off = max(1, int(offset or 1))
    lim = int(limit or 2000)
    s = truncate_head("\n".join(lines[off - 1: off - 1 + lim]), ctx.settings.max_tool_output)
    shown = min(len(lines), off - 1 + lim)
    if shown < len(lines):
        s += f"\n\n[{len(lines) - shown} more lines. Use offset={shown + 1} to continue.]"
    return s


def write(ctx: ToolContext, path: str, content: str = "", **_):
    p = _path(ctx, path, must_exist=False)
    if not os.path.splitext(p)[1] and not ctx.vault.is_dir(p):
        p += ".md"
    ctx.vault.write(p, str(content))
    return f"Successfully wrote {len(str(content).encode())} bytes to {p}"


def edit(ctx: ToolContext, path: str, oldText: str = "", newText: str = "", **kw):
    old = oldText or kw.get("old_text") or kw.get("old") or ""
    new = newText if newText != "" or "newText" in kw else (kw.get("new_text") or kw.get("new") or "")
    if not old:
        raise ToolError("oldText must not be empty")
    p = _path(ctx, path)
    text = ctx.vault.read(p)
    n = text.count(old)
    if n == 0:
        raise ToolError(f"Could not find the exact text in {p}. oldText must match exactly, including whitespace and newlines. Read the file and try again.")
    if n > 1:
        raise ToolError(f"Found {n} occurrences of the text in {p}. Include more surrounding context so oldText is unique.")
    i = text.index(old)
    ctx.vault.write(p, text[:i] + new + text[i + len(old):])
    return f"Successfully replaced text in {p} (at line {text[:i].count(chr(10)) + 1})."


# ---------------------------------------------------------------- the real shell
async def bash(ctx: ToolContext, command: str, timeout: float | None = None, **_):
    t = min(max(1, float(timeout or ctx.settings.bash_timeout)), 3600)
    out, code = await run_shell(ctx.jade, str(command), timeout=t, on_line=ctx.say)
    out = truncate_tail(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out), ctx.settings.max_tool_output)
    if not out.strip():
        out = "(no output)"
    if code != 0:
        out += f"\n\nCommand exited with code {code}"
    return out


async def run_shell(jade, command: str, *, timeout: float = 60, cwd: str | None = None, stdin: str = "", on_line=None) -> tuple[str, int]:
    """Run a command in the real shell, in the vault folder. Returns (stdout+stderr, exit code); 124 on timeout."""
    env = {**os.environ, "VAULT": str(jade.vault.root), "JADE_VAULT": str(jade.vault.root), "TERM": "dumb", **{k: v for k, v in jade.settings.secrets.items()}}
    proc = await asyncio.create_subprocess_exec(jade.settings.shell, "-c", command, cwd=cwd or str(jade.vault.root), env=env,
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                start_new_session=True)
    chunks: list[str] = []

    async def pump():
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            s = line.decode("utf-8", errors="replace")
            chunks.append(s)
            if on_line:
                on_line(s.rstrip("\n"))

    try:
        if stdin:
            proc.stdin.write(stdin.encode())
        proc.stdin.close()
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
        return "".join(chunks), proc.returncode or 0
    except asyncio.TimeoutError:
        _kill(proc)
        return "".join(chunks) + f"\ntimed out after {timeout:g}s\n", 124
    except asyncio.CancelledError:
        _kill(proc)
        raise


def _kill(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------- tasks
def tasks(ctx: ToolContext, action: str = "list", titles=None, title: str = "", id=None, status: str = "", **_):
    s = ctx.session
    if s is None:
        raise ToolError("tasks need a chat session")
    items = s.tasks
    a = str(action or "list").lower()
    if a == "add":
        ts = titles if isinstance(titles, list) else [titles] if titles else [title] if title else []
        if not ts:
            raise ToolError("add needs titles")
        for t in ts:
            s.task_seq += 1
            items.append({"id": s.task_seq, "title": str(t), "status": "pending"})
    elif a == "update":
        t = next((x for x in items if x["id"] == int(id or 0)), None)
        if not t:
            raise ToolError(f"no task #{id}")
        if status:
            t["status"] = status
        if title:
            t["title"] = title
    elif a == "remove":
        s.tasks = items = [x for x in items if x["id"] != int(id or 0)]
    elif a == "clear":
        s.tasks = items = []
    elif a != "list":
        raise ToolError(f"unknown action {a}")
    mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
    return "\n".join(f"{mark.get(x['status'], '[ ]')} #{x['id']} {x['title']}" for x in items) or "(no tasks)"


# ---------------------------------------------------------------- web
async def web_search(ctx: ToolContext, query: str, max_results: int = 5, **_):
    st = ctx.settings
    n = min(max(1, int(max_results or 5)), 10)
    order = [p for p in ("ollama" if st.ollama_key else "", "searxng" if st.searx_url else "", "ddg", "wikipedia") if p] if st.search == "auto" else [st.search]
    errors = []
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": st.user_agent}, follow_redirects=True) as c:
        for p in order:
            try:
                res = []
                if p == "ollama":
                    r = await c.post("https://ollama.com/api/web_search", json={"query": query, "max_results": n}, headers={"Authorization": "Bearer " + st.ollama_key})
                    r.raise_for_status()
                    res = [{"title": x.get("title"), "url": x.get("url"), "snippet": (x.get("content") or "")[:600]} for x in r.json().get("results", [])]
                elif p == "searxng":
                    r = await c.get(st.searx_url.rstrip("/") + "/search", params={"format": "json", "q": query})
                    r.raise_for_status()
                    res = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content") or ""} for x in r.json().get("results", [])[:n]]
                elif p == "ddg":
                    res = await _ddg_html(c, query, n)
                    if not res:
                        r = await c.get("https://api.duckduckgo.com/", params={"format": "json", "no_html": 1, "skip_disambig": 1, "q": query})
                        j = r.json()
                        if j.get("AbstractText"):
                            res.append({"title": j.get("Heading") or query, "url": j.get("AbstractURL"), "snippet": j["AbstractText"]})
                        for t in [y for x in j.get("RelatedTopics", []) for y in (x.get("Topics") or [x])]:
                            if t.get("FirstURL"):
                                res.append({"title": (t.get("Text") or "").split(" - ")[0], "url": t["FirstURL"], "snippet": t.get("Text") or ""})
                        res = res[:n]
                elif p == "wikipedia":
                    r = await c.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "format": "json", "srlimit": n, "srsearch": query})
                    r.raise_for_status()
                    res = [{"title": x["title"], "url": "https://en.wikipedia.org/wiki/" + x["title"].replace(" ", "_"), "snippet": re.sub(r"<[^>]+>", "", x.get("snippet", ""))} for x in r.json().get("query", {}).get("search", [])]
                if not res:
                    raise ToolError("no results")
                return f"Results from {p}:\n\n" + "\n\n".join(f"{i + 1}. {x['title']}\n   {x['url']}\n   {_squash(x['snippet'] or '').strip()}" for i, x in enumerate(res))
            except Exception as e:
                errors.append(f"{p}: {e}")
    raise ToolError("web search failed — " + "; ".join(errors) + ". Configure a provider (settings.search, searx_url or ollama_key).")


async def _ddg_html(c: httpx.AsyncClient, query: str, n: int) -> list[dict]:
    from bs4 import BeautifulSoup
    r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select(".result__a")[:n]:
        href = a.get("href") or ""
        if "uddg=" in href:
            from urllib.parse import parse_qs, unquote
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        sn = a.find_parent(class_="result")
        snippet = sn.select_one(".result__snippet").get_text(" ", strip=True) if sn and sn.select_one(".result__snippet") else ""
        out.append({"title": a.get_text(" ", strip=True), "url": href, "snippet": snippet})
    return out


def html_to_markdown(html: str, url: str = "") -> str:
    from bs4 import BeautifulSoup
    from markdownify import markdownify
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer"]):
        t.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else ""
    md = markdownify(str(main), heading_style="ATX")
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return (f"# {title}\n{url}\n\n" if title else "") + md


async def web_fetch(ctx: ToolContext, url: str, **_):
    if not re.match(r"^https?://", str(url)):
        raise ToolError("url must start with http:// or https://")
    st = ctx.settings
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": st.user_agent, "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5"}) as c:
        if st.ollama_key:
            try:
                r = await c.post("https://ollama.com/api/web_fetch", json={"url": url}, headers={"Authorization": "Bearer " + st.ollama_key})
                if r.status_code == 200:
                    j = r.json()
                    return truncate_head(f"# {j.get('title') or url}\n\n{j.get('content') or ''}", st.max_tool_output)
            except httpx.HTTPError:
                pass
        try:
            r = await c.get(url)
            ct = r.headers.get("content-type", "")
            if r.status_code >= 400:
                raise ToolError(f"HTTP {r.status_code}")
            if "html" in ct:
                return truncate_head(html_to_markdown(r.text, url), st.max_tool_output)
            if "json" in ct or ct.startswith("text/"):
                return truncate_head(r.text, st.max_tool_output)
            raise ToolError(f"not a page ({ct or 'unknown type'}); use http_request with save_to to download it")
        except (httpx.HTTPError, ToolError) as e:
            try:
                r = await c.get("https://r.jina.ai/" + url, headers={"X-Return-Format": "markdown"})
                if r.status_code == 200:
                    return truncate_head(r.text, st.max_tool_output)
            except httpx.HTTPError:
                pass
            raise ToolError(f"fetch failed: {e}") from e


def _fill_secrets(ctx: ToolContext, s):
    if isinstance(s, str):
        def sub(m):
            k = m.group(1)
            v = ctx.settings.secrets.get(k) or os.environ.get(k)
            if v is None:
                raise ToolError(f"no secret named {k}. Add it to settings.secrets (or the environment) and use {{{{{k}}}}}.")
            return v
        return re.sub(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}", sub, s)
    if isinstance(s, dict):
        return {k: _fill_secrets(ctx, v) for k, v in s.items()}
    if isinstance(s, list):
        return [_fill_secrets(ctx, v) for v in s]
    return s


def _redact(ctx: ToolContext, text: str) -> str:
    for v in ctx.settings.secrets.values():
        if v and len(v) >= 6:
            text = text.replace(v, "[secret]")
    return text


async def http_request(ctx: ToolContext, url: str, method: str = "GET", headers: dict | None = None, query: dict | None = None, json: dict | None = None,  # noqa: A002
                       form: dict | None = None, body: str | None = None, save_to: str = "", timeout: float = 30, **_):
    import json as _json
    method = str(method or "GET").upper()
    u = _fill_secrets(ctx, str(url))
    if not re.match(r"^https?://", u):
        raise ToolError("url must be absolute http(s)")
    if query:
        u += ("&" if "?" in u else "?") + urlencode(_fill_secrets(ctx, query))
    kw: dict = {"headers": {"User-Agent": ctx.settings.user_agent, **_fill_secrets(ctx, headers or {})}}
    if json is not None:
        kw["json"] = _fill_secrets(ctx, json)
    elif form is not None:
        kw["data"] = _fill_secrets(ctx, form)
    elif body is not None:
        kw["content"] = _fill_secrets(ctx, body)
    async with httpx.AsyncClient(timeout=min(max(1, float(timeout or 30)), 120), follow_redirects=True) as c:
        r = await c.request(method, u, **kw)
    ct = r.headers.get("content-type", "")
    head = f"HTTP {r.status_code} {r.reason_phrase}\ncontent-type: {ct}\n"
    for h in ("location", "retry-after", "x-ratelimit-remaining"):
        if h in r.headers:
            head += f"{h}: {r.headers[h]}\n"
    data = r.content
    text_like = "json" in ct or ct.startswith("text/") or "xml" in ct or "javascript" in ct or (not ct and _is_text(data))
    if save_to or not text_like:
        p = ctx.vault.norm(save_to) if save_to else _download_path(u, ct)
        ctx.vault.write(p, data)
        return head + f"\nSaved {len(data) / 1024:.1f} KB to {p}"
    t = r.text
    if "json" in ct:
        try:
            t = _json.dumps(r.json(), indent=1, ensure_ascii=False)
        except ValueError:
            pass
    return _redact(ctx, head + "\n" + truncate_head(t, ctx.settings.max_tool_output))


def _download_path(url: str, ct: str) -> str:
    name = os.path.basename(urlparse(url).path) or "download"
    if "." not in name:
        name += mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
    name = re.sub(r"[^\w.\- ]+", "_", name)
    return f"/attachments/{date.today().isoformat()}/{name}"


# ---------------------------------------------------------------- notes
def notes_search(ctx: ToolContext, query: str = "", limit: int = 10, **_):
    v = ctx.vault
    q = str(query or "").strip()
    if not q:
        idx = v.index()
        folders: dict[str, int] = {}
        for p in idx.notes:
            f = p.rsplit("/", 1)[0] or "/"
            folders[f] = folders.get(f, 0) + 1
        top = sorted(idx.notes, key=lambda p: -len(idx.back.get(p, ())))[:10]
        return (f"{v.overview()}\nFolders: " + ", ".join(f"{f} ({n})" for f, n in sorted(folders.items())) +
                "\nMost linked: " + ", ".join(f"[[{note_name(p)}]] ({len(idx.back.get(p, ()))})" for p in top if idx.back.get(p)))
    res = v.search(q, min(max(1, int(limit or 10)), 30))
    if not res:
        return f'No notes match "{q}".'
    return "\n".join(f"{r['path']} — {r['snippet']}" for r in res)


def note_links(ctx: ToolContext, note: str = "", **kw):
    v = ctx.vault
    target = note or kw.get("path") or kw.get("name") or ""
    p = v.resolve(target) or v.norm(target)
    if not v.is_file(p):
        raise ToolError(f'no note "{target}"')
    L = v.links(p)
    back_lines = []
    for b in L["backlinks"]:
        line = next((ln.strip() for ln in v.read(b).split("\n") if "[[" in ln and (note_name(p).lower() in ln.lower())), "")
        back_lines.append(f"- [[{note_name(b)}]] ({b}){': ' + line[:160] if line else ''}")
    return (f"{p}\nBacklinks ({len(L['backlinks'])}):\n" + ("\n".join(back_lines) or "- none") +
            f"\nOutgoing ({len(L['outgoing'])}): " + (", ".join(f"[[{note_name(o)}]]" for o in L["outgoing"]) or "none") +
            f"\nUnresolved: " + (", ".join(f"[[{u}]]" for u in L["unresolved"]) or "none") +
            f"\nTags: " + (" ".join("#" + t for t in L["tags"]) or "none"))


# ---------------------------------------------------------------- memory and skills
async def memory(ctx: ToolContext, action: str = "list", text: str = "", section: str = "notes", **kw):
    m = ctx.jade.memory
    a = str(action or "list").lower()
    t = text or kw.get("query") or kw.get("fact") or kw.get("content") or ""
    if a in ("save", "remember", "add"):
        out = m.save(t, str(section or "notes").lower())
        ctx.jade._memory_changed()
        return out
    if a in ("forget", "remove", "delete"):
        out = m.forget(t)
        ctx.jade._memory_changed()
        return out
    if a in ("search", "recall"):
        return await ctx.jade.memory_search(t)
    if a == "list":
        m.ensure()
        return ctx.vault.read("/Jade/Memory.md")
    raise ToolError(f'unknown action "{a}" (use save, search, forget or list)')


def skill(ctx: ToolContext, name: str, input: str = "", **_):  # noqa: A002
    return ctx.jade.skills.load(name, input)


def find_skill(ctx: ToolContext, query: str, limit: int = 6, **_):
    hits = ctx.jade.skills.find(query, int(limit or 6))
    return "\n".join(f"- {s.name}: {s.description}" for s in hits) or "No skills yet."


# ---------------------------------------------------------------- registration
def register(reg: Registry, specs: dict[str, dict]) -> None:
    def spec(n):
        s = specs[n]
        return s["description"], s["parameters"]

    def add(name, fn, *, risky=False, core=True, hints="", summary=""):
        d, p = spec(name)
        reg.add(Tool(name, d, p, fn, risky=risky, core=core, hints=hints, summary=summary))

    add("read", read, summary="read / write / edit: read, create or change notes and other files (write creates folders)")
    add("write", write, risky=False)
    add("edit", edit)
    add("bash", bash, risky=True, summary="bash: the real shell on this machine, in the vault folder (python3, node, git, sqlite3, curl…)")
    add("tasks", tasks, summary="tasks: maintain a visible task list for multi-step work")
    add("web_search", web_search, summary="web_search / web_fetch: look things up on the web")
    add("web_fetch", web_fetch)
    add("http_request", http_request, risky=True, core=False, hints="http api request post put get rest json endpoint download webhook",
        summary="http_request: call APIs with any method (secrets as {{NAME}})")
    add("notes_search", notes_search, summary="notes_search: find notes by words or #tag; empty query = vault overview")
    add("note_links", note_links, summary="note_links: backlinks, outgoing and unresolved links and tags of one note")
    add("memory", memory, summary="memory: your long-term memory across chats (save / search / forget / list)")
    add("skill", skill, summary="skill: load a skill's instructions by name")
    add("find_skill", find_skill, core=True, summary="")
