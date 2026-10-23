"""The web dashboard: static pages in jade/web (served at /ui/) and the routes they need beyond the main API.

    GET  /v1/overview                       everything the home page shows at once
    GET  /v1/vault/list?path=/              one folder: [{name, path, dir, size, mtime}]
    GET  /v1/vault/file?path=               a file's text (or that it is binary)    PUT {path, content}   DELETE ?path=
    GET  /v1/vault/raw?path=                the file itself (pictures, downloads)
    POST /v1/vault/move {from, to}   POST /v1/vault/mkdir {path}   GET /v1/vault/search?q=
    GET  /v1/settings                       values (secrets masked) and the schema    PATCH {name: value, …}
    GET  /v1/logs                           the logs in /Jade/Logs    GET /v1/logs/{name}?lines=200
All need the API token, like the rest of the API.
"""
from __future__ import annotations

import mimetypes
import re
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import tomli_w
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import __version__
from .settings import Settings, config_path

if TYPE_CHECKING:
    from .core import Jade

WEB = Path(__file__).parent / "web"
SECRET = re.compile(r"(token|key|pass|secret)$|^secrets$", re.I)
MASK = "••••••"
# changing these needs a new model client
PROVIDER_FIELDS = {"provider", "model", "host", "base_url", "api_key", "fallbacks", "request_timeout", "temperature", "num_ctx", "think", "keep_alive"}
# changing these needs a restart of jade serve
RESTART_FIELDS = {"vault", "data_dir", "api_host", "api_port"}
MAX_EDIT = 2_000_000


def _mask(name: str, v: Any) -> Any:
    if not SECRET.search(name):
        return v
    if isinstance(v, str):
        return (MASK + v[-4:]) if len(v) > 8 else (MASK if v else "")
    if isinstance(v, dict):
        return {k: MASK for k in v}
    return v


def _masked(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and not SECRET.search(k):
            out[k] = _masked(v)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            out[k] = [_masked(x) for x in v]
        else:
            out[k] = _mask(k, v)
    return out


def _unmask(new: Any, old: Any) -> Any:
    """A value the page sent back still masked means "unchanged"."""
    if isinstance(new, str) and new.startswith(MASK):
        return old
    if isinstance(new, dict) and isinstance(old, dict):
        return {k: _unmask(v, old.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list):
        return [_unmask(n, old[i] if i < len(old) else None) for i, n in enumerate(new)]
    return new


def add_routes(app: FastAPI, jade: "Jade", auth: Callable, err: Callable, state: dict) -> None:
    st = lambda: jade.settings   # noqa: E731 - settings can be replaced by PATCH
    dep = [Depends(auth)]

    # ---------------------------------------------------------------- pages
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/ui/")

    @app.get("/ui", include_in_schema=False)
    async def ui_noslash():
        return RedirectResponse("/ui/")

    @app.get("/ui/{path:path}", include_in_schema=False)
    async def ui(path: str = ""):
        p = (WEB / (path or "index.html")).resolve()
        if WEB.resolve() not in p.parents and p != WEB.resolve() / "index.html" or not p.is_file():
            p = WEB / "index.html"   # the page routes itself (#/chat, #/vault…)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- overview
    @app.get("/v1/overview", dependencies=dep)
    async def overview():
        s = st()
        links = getattr(jade, "phones", None)
        F = getattr(jade, "flows", None)
        sch = getattr(jade, "scheduler", None)
        S = getattr(jade, "sentry", None)
        notes = 0
        try:
            notes = len(jade.vault.notes())
        except Exception:  # noqa: BLE001
            pass
        last_notes = _tail(jade, "/Jade/Logs/notifications.log", 12)
        sentry_on = False
        if S:
            try:
                sentry_on = bool(S.cfg().get("enabled"))
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "app": "jade-py", "version": __version__, "model": s.model, "provider": s.provider, "vault": str(jade.vault.root),
                "uptime_s": int(time.time() - state["started"]), "daemon": jade.daemon_running, "busy": sorted(state["busy"]),
                "notes": notes, "chats": len(jade.chats()), "tools": len(jade.tools.active(jade)),
                "flows": len(F.list()) if F else 0, "flow_runs": (F.recent()[:5] if F else []),
                "schedules": len(sch.list) if sch else 0, "phones": links.list() if links else [],
                "sentry": {"on": sentry_on, "log": S.log[:8] if S else []},
                "heartbeat": {"on": s.hb_on, "every_min": s.hb_every, "status": jade.state.get("hb_status")},
                "curiosity": {"on": s.cu_on}, "backup": {"on": s.ab_on, "last": jade.state.get("ab_last")},
                "approve": s.approve, "notifications": last_notes}

    # ---------------------------------------------------------------- vault
    v = jade.vault

    @app.get("/v1/vault/list", dependencies=dep)
    async def vault_list(path: str = "/"):
        try:
            names = v.list(path)
        except Exception as e:  # noqa: BLE001
            return err(404, "not_found", str(e))
        base = v.norm(path)
        out = []
        for n in names:
            d = n.endswith("/")
            p = (base.rstrip("/") + "/" + n.rstrip("/")) if base != "/" else "/" + n.rstrip("/")
            if n.startswith(".") and n.rstrip("/") not in (".data",):
                continue
            try:
                stt = v.stat(p)
                out.append({"name": n.rstrip("/"), "path": p, "dir": d, "size": 0 if d else stt.st_size, "mtime": stt.st_mtime})
            except OSError:
                continue
        out.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        return {"ok": True, "path": base, "items": out}

    @app.get("/v1/vault/file", dependencies=dep)
    async def vault_file(path: str):
        if not v.is_file(path):
            return err(404, "not_found", f"{path} is not a file in the vault")
        stt = v.stat(path)
        data = v.read_bytes(path)
        mime = mimetypes.guess_type(path)[0] or ""
        if len(data) > MAX_EDIT or b"\0" in data[:4096]:
            return {"ok": True, "path": v.norm(path), "binary": True, "size": stt.st_size, "mtime": stt.st_mtime, "mime": mime}
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": True, "path": v.norm(path), "binary": True, "size": stt.st_size, "mtime": stt.st_mtime, "mime": mime}
        return {"ok": True, "path": v.norm(path), "content": text, "size": stt.st_size, "mtime": stt.st_mtime, "mime": mime}

    @app.get("/v1/vault/raw", dependencies=dep)
    async def vault_raw(path: str, download: bool = False):
        if not v.is_file(path):
            return err(404, "not_found", f"{path} is not a file in the vault")
        real = v.real(path)
        return FileResponse(real, filename=real.name if download else None, media_type=mimetypes.guess_type(real.name)[0] or "application/octet-stream")

    @app.put("/v1/vault/file", dependencies=dep)
    async def vault_write(body: dict):
        p, content = body.get("path"), body.get("content")
        if not p or not isinstance(content, str):
            return err(400, "bad_request", "send {path, content}")
        if body.get("mtime") and v.is_file(p) and abs(v.stat(p).st_mtime - float(body["mtime"])) > 0.001:
            return err(409, "changed", "the file changed since you opened it (Jade or another window wrote it). Reload it first.")
        path = v.write(p, content)
        return {"ok": True, "path": path, "mtime": v.stat(path).st_mtime}

    @app.delete("/v1/vault/file", dependencies=dep)
    async def vault_delete(path: str):
        if v.norm(path) in ("/", "/Jade"):
            return err(400, "refused", "that folder is not deleted from here")
        try:
            v.remove(path, recursive=True)
        except Exception as e:  # noqa: BLE001
            return err(400, "failed", str(e))
        return {"ok": True}

    @app.post("/v1/vault/move", dependencies=dep)
    async def vault_move(body: dict):
        try:
            return {"ok": True, "path": v.move(body["from"], body["to"])}
        except Exception as e:  # noqa: BLE001
            return err(400, "failed", str(e))

    @app.post("/v1/vault/mkdir", dependencies=dep)
    async def vault_mkdir(body: dict):
        v.mkdir(body.get("path") or "")
        return {"ok": True, "path": v.norm(body.get("path"))}

    @app.get("/v1/vault/search", dependencies=dep)
    async def vault_search(q: str = "", limit: int = 30):
        if not q.strip():
            return {"ok": True, "results": [], "overview": v.overview()}
        return {"ok": True, "results": v.search(q, max(1, min(100, limit)))}

    # ---------------------------------------------------------------- settings
    @app.get("/v1/settings", dependencies=dep)
    async def settings_get():
        s = st()
        schema = Settings.model_json_schema()
        return {"ok": True, "values": _masked(s.model_dump(mode="json")), "schema": schema, "config": str(getattr(jade, "config_path", None) or config_path()),
                "restart": sorted(RESTART_FIELDS)}

    @app.patch("/v1/settings", dependencies=dep)
    async def settings_patch(body: dict):
        s = st()
        cur = s.model_dump(mode="json")
        changes = {k: _unmask(val, cur.get(k)) for k, val in body.items() if k in Settings.model_fields}
        unknown = [k for k in body if k not in Settings.model_fields]
        if unknown:
            return err(400, "unknown_setting", "no such setting: " + ", ".join(unknown))
        changes = {k: val for k, val in changes.items() if val != cur.get(k)}
        if not changes:
            return {"ok": True, "changed": []}
        try:
            new = Settings(**{**cur, **changes})
        except Exception as e:  # noqa: BLE001 - pydantic says what is wrong
            return err(400, "invalid", str(e))
        # write only what changed into the config file, keeping the rest of it as the user wrote it
        path = Path(getattr(jade, "config_path", None) or config_path())
        data: dict = {}
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text())
            except (OSError, ValueError) as e:
                return err(500, "config_unreadable", f"{path} could not be read: {e}")
        nd = new.model_dump(mode="json", exclude_none=True)
        for k in changes:
            if k in nd:
                data[k] = nd[k]
            else:
                data.pop(k, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(data))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        for k in changes:   # apply to the running Jade (the same object everything holds)
            if k not in RESTART_FIELDS:
                object.__setattr__(s, k, getattr(new, k))
        notes = []
        if PROVIDER_FIELDS & set(changes):
            from .providers import make_provider
            old = jade.provider
            jade.provider = make_provider(s)
            try:
                await old.aclose()
            except Exception:  # noqa: BLE001
                pass
            notes.append("the model client was rebuilt")
        if "phones" in changes and getattr(jade, "phones", None):
            jade.phones.sync_direct()
        restart = sorted(RESTART_FIELDS & set(changes))
        if restart:
            notes.append("restart jade serve for: " + ", ".join(restart))
        return {"ok": True, "changed": sorted(changes), "notes": notes, "saved": str(path)}

    # ---------------------------------------------------------------- hearing (talk mode on devices without a recognizer)
    @app.post("/v1/transcribe", dependencies=dep)
    async def transcribe(request: Request, language: str = ""):
        s = st()
        if not s.stt_model:
            return err(400, "no_stt", "no transcription model is set (stt_model in the settings)")
        audio = await request.body()
        if not audio or len(audio) > 25 * 1024 * 1024:
            return err(400, "bad_audio", "send a WAV body of up to 25 MB")
        prov = s.stt_provider or s.provider
        if prov in ("ollama", "ollama-cloud"):
            base = (s.host.rstrip("/") if prov == "ollama" else "https://ollama.com") + "/v1"
            key = s.ollama_key if prov == "ollama-cloud" else ""
        else:
            base, key = s.openai_base_and_key(prov)
        import httpx
        files = {"file": ("speech.wav", audio, "audio/wav")}
        data = {"model": s.stt_model, "response_format": "json", "temperature": "0", **({"language": language} if language else {})}
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(base + "/audio/transcriptions", files=files, data=data, headers={"Authorization": f"Bearer {key}"} if key else {})
        except httpx.HTTPError as e:
            return err(502, "stt_unreachable", f"{prov} could not be reached: {e}")
        if r.status_code >= 400:
            return err(502, "stt_failed", f"{prov} could not transcribe with {s.stt_model}: HTTP {r.status_code} {r.text[:200]}")
        try:
            return {"ok": True, "text": str(r.json().get("text") or "").strip()}
        except ValueError:
            return {"ok": True, "text": r.text.strip()}

    # ---------------------------------------------------------------- logs
    @app.get("/v1/logs", dependencies=dep)
    async def logs():
        try:
            items = [{"name": n.removesuffix(".log"), "size": v.stat("/Jade/Logs/" + n).st_size, "mtime": v.stat("/Jade/Logs/" + n).st_mtime}
                     for n in v.list("/Jade/Logs") if n.endswith(".log")]
        except Exception:  # noqa: BLE001
            items = []
        return {"ok": True, "logs": sorted(items, key=lambda x: -x["mtime"])}

    @app.get("/v1/logs/{name}", dependencies=dep)
    async def log(name: str, lines: int = 200):
        if not re.fullmatch(r"[\w.-]{1,60}", name):
            return err(400, "bad_name", "bad log name")
        return {"ok": True, "name": name, "lines": _tail(jade, f"/Jade/Logs/{name}.log", max(1, min(5000, lines)))}

    # keep a Request import used (for type checkers that drop it)
    _ = Request
    _ = Response


def _tail(jade: "Jade", path: str, n: int) -> list[str]:
    try:
        if not jade.vault.is_file(path):
            return []
        real = jade.vault.real(path)
        size = real.stat().st_size
        with open(real, "rb") as f:
            f.seek(max(0, size - 200 * n))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return lines[-n:]
    except OSError:
        return []
