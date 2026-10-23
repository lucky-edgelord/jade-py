"""HTTP API (FastAPI). `jade serve` runs it.

Compatible with the Jade Vault app's API (GET, JSON): /v1/chat?message=…&session=<id>|new, /v1/status, /v1/history,
/v1/chats. Plus:
  POST /v1/chat {message, session?, stream?}       stream=true: Server-Sent Events, one per event (text, tool_start…)
  GET  /v1/chats/{id}  DELETE /v1/chats/{id}
  POST /v1/chats/{id}/step {message?}             step mode: run the next move (a model call or one tool call)
  GET  /v1/chats/{id}/pending                     the tool calls waiting in step mode
  POST /v1/chats/{id}/skip {id?, reason?}  /edit {id?, arguments}  /cancel
  GET  /v1/tools   POST /v1/tools/{name} {…arguments}
Auth: Authorization: Bearer <settings.api_token> (or ?token=). A token is required unless the server listens on
loopback only."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from . import __version__
from . import events as E
from .core import Jade
from .session import Session, Stepper, clean_reply

API = 1
MAX_MESSAGE = 20000


def create_app(jade: Jade, daemon: bool = False) -> FastAPI:
    """daemon=True also runs schedules, the heartbeat and curiosity while the server is up (jade serve does)."""
    from contextlib import asynccontextmanager

    listeners: set[asyncio.Queue] = set()

    def broadcast(event: str, data: dict) -> None:
        for q in list(listeners):
            q.put_nowait({"event": event, "data": data})

    def on_notify(title: str, body: str) -> None:
        broadcast("notify", {"title": title, "body": body, "ts": time.time()})

    # ---- approvals: a tool call that needs the user's yes goes to the dashboard (if one is open), else to the phone
    approvals: dict[str, dict] = {}

    async def approver(call, tool) -> bool:
        aid = secrets.token_hex(6)
        args = {k: (v[:500] + "…" if isinstance(v, str) and len(v) > 500 else v) for k, v in (call.arguments or {}).items()}
        item = {"id": aid, "tool": call.name, "arguments": args, "created": time.time(), "risky": bool(tool and tool.risky)}
        if listeners:
            fut = asyncio.get_running_loop().create_future()
            approvals[aid] = {**item, "future": fut}
            broadcast("approval", item)
            try:
                return bool(await asyncio.wait_for(fut, 600))
            except asyncio.TimeoutError:
                return False
            finally:
                approvals.pop(aid, None)
                broadcast("approval_done", {"id": aid})
        links = getattr(jade, "phones", None)
        if links and any(p.online for p in links.phones.values()):
            try:
                return await links.ask(f"Jade wants to use {call.name}", json.dumps(args, ensure_ascii=False, indent=1)[:900])
            except Exception:  # noqa: BLE001
                return False
        return False

    @asynccontextmanager
    async def lifespan(_app):
        jade.notifiers.append(on_notify)
        own_approver = jade.approver is None
        if own_approver:
            jade.approver = approver
        task = None
        if daemon:
            from .scheduler import Daemon
            d = Daemon(jade)
            task = asyncio.create_task(d.run())
        try:
            yield
        finally:
            if task:
                d.stop()
                task.cancel()
            if on_notify in jade.notifiers:
                jade.notifiers.remove(on_notify)
            if own_approver and jade.approver is approver:
                jade.approver = None

    app = FastAPI(title="Jade", version=__version__, description="Jade, the agent from Jade Vault, over HTTP", lifespan=lifespan)
    st = jade.settings
    state: dict[str, Any] = {"busy": set(), "steppers": {}, "running": {}, "current": None, "started": time.time()}
    lock = asyncio.Lock()

    def auth(request: Request):
        tok = st.api_token
        if not tok:
            return
        got = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if not secrets.compare_digest(got.encode(), tok.encode()):
            raise HTTPException(401, {"ok": False, "api": API, "error": {"code": "unauthorized", "message": "send the API token: Authorization: Bearer <token>"}})

    def err(status: int, code: str, message: str, **extra):
        return JSONResponse({"ok": False, "api": API, "error": {"code": code, "message": message}, **extra}, status_code=status)

    def info(s: Session) -> dict:
        return {"id": s.id, "title": s.title, "messages": sum(1 for m in s.messages if m.get("role") in ("user", "assistant")), "updated": s.updated}

    def get_session(want: str | None, new: bool = False) -> Session:
        if new or want == "new":
            s = jade.session()
        elif want:
            try:
                s = jade.session(want)
            except (KeyError, ValueError):
                raise HTTPException(404, {"ok": False, "api": API, "error": {"code": "unknown_session", "message": f"no chat with id {want}. List them with /v1/chats"}})
        elif state["current"]:
            s = jade.session(state["current"]) if state["current"] in {c["id"] for c in jade.chats()} else jade.session()
        else:
            s = jade.session()
        state["current"] = s.id
        return s

    async def run_turn(s: Session, message: str, include_tools: bool = True) -> dict:
        t0 = time.time()
        calls, error = [], None
        reply = ""
        async for ev in s.run(message):
            if isinstance(ev, E.ToolEnd):
                calls.append({"name": ev.name, "ok": not ev.error, "result": ev.output[:300]})
            elif isinstance(ev, E.ToolStart):
                calls.append({"name": ev.name, "arguments": {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in ev.arguments.items()}})
            elif isinstance(ev, E.Error):
                error = ev.message
            elif isinstance(ev, E.Done):
                reply = ev.text
        merged = []
        for c in calls:
            if "arguments" in c:
                merged.append(c)
            elif merged:
                merged[-1].update(c)
        out = {"ok": not (error and not reply), "api": API, "session": info(s), "reply": reply,
               "error": {"code": "agent_error", "message": error} if error else None, "model": st.model, "elapsed_ms": int((time.time() - t0) * 1000)}
        if include_tools:
            out["tool_calls"] = merged
        return out

    def truthy(v) -> bool:
        return v is True or str(v).lower() in ("1", "true", "yes", "on")

    async def chat_common(q: dict):
        message = str(q.get("message") or q.get("q") or q.get("text") or q.get("prompt") or "").strip()
        if not message:
            return err(400, "missing_message", "send the text in the message parameter, e.g. /v1/chat?message=hello")
        if len(message) > MAX_MESSAGE:
            return err(413, "message_too_long", f"messages are limited to {MAX_MESSAGE} characters")
        s = get_session(q.get("session"), truthy(q.get("new")))
        if s.id in state["busy"]:
            return err(409, "busy", "Jade is already working in this chat. Try again when she is done (see /v1/status).", busy=True)
        state["busy"].add(s.id)
        state["running"][s.id] = s
        if truthy(q.get("stream")):
            async def gen():
                try:
                    async for ev in s.run(message, q.get("images") if isinstance(q.get("images"), list) else None):
                        yield {"event": ev.type, "data": json.dumps({**ev.to_dict(), "session": s.id}, ensure_ascii=False)}
                finally:
                    state["busy"].discard(s.id)
                    state["running"].pop(s.id, None)
            return EventSourceResponse(gen())
        if q.get("wait") is not None and not truthy(q.get("wait")):
            async def bg():
                try:
                    await run_turn(s, message)
                finally:
                    state["busy"].discard(s.id)
                    state["running"].pop(s.id, None)
            asyncio.create_task(bg())
            return JSONResponse({"ok": True, "api": API, "accepted": True, "session": info(s), "message": "Jade is working on it. Poll /v1/status, then read /v1/history."}, status_code=202)
        try:
            out = await run_turn(s, message, truthy(q.get("tools", "1")))
        finally:
            state["busy"].discard(s.id)
            state["running"].pop(s.id, None)
        return JSONResponse(out, status_code=200 if out["ok"] else 502)

    @app.get("/v1/chat", dependencies=[Depends(auth)])
    async def chat_get(request: Request):
        q = dict(request.query_params)
        if q.get("json"):
            try:
                extra = json.loads(q.pop("json"))
                q = {**extra, **q}
            except ValueError as e:
                return err(400, "bad_json", f"the json parameter is not valid JSON: {e}")
        return await chat_common(q)

    @app.post("/v1/chat", dependencies=[Depends(auth)])
    async def chat_post(body: dict):
        return await chat_common(body)

    @app.get("/v1/status", dependencies=[Depends(auth)])
    async def status():
        return {"ok": True, "api": API, "app": "jade-py", "version": __version__, "busy": bool(state["busy"]), "model": st.model, "provider": st.provider,
                "session": state["current"], "vault": str(jade.vault.root), "uptime_s": int(time.time() - state["started"])}

    @app.get("/v1/chats", dependencies=[Depends(auth)])
    async def chats():
        return {"ok": True, "api": API, "chats": [{**c, "current": c["id"] == state["current"]} for c in jade.chats()[:100]]}

    @app.get("/v1/history", dependencies=[Depends(auth)])
    async def history(session: str | None = None, limit: int = 20):
        sid = session or state["current"]
        if not sid:
            return {"ok": True, "api": API, "session": None, "messages": []}
        try:
            s = jade.session(sid)
        except (KeyError, ValueError):
            return err(404, "unknown_session", f"no chat with id {sid}. List them with /v1/chats")
        msgs = [{"role": m["role"], "content": clean_reply(m.get("display") or m.get("content")), "ts": m.get("ts")} for m in s.messages
                if m.get("role") in ("user", "assistant") and not m.get("auto") and clean_reply(m.get("display") or m.get("content"))][-max(1, min(200, limit)):]
        return {"ok": True, "api": API, "session": info(s), "messages": msgs}

    @app.get("/v1/chats/{sid}", dependencies=[Depends(auth)])
    async def chat_full(sid: str):
        try:
            return {"ok": True, "api": API, "chat": jade.session(sid).to_dict()}
        except (KeyError, ValueError):
            return err(404, "unknown_session", f"no chat with id {sid}")

    @app.delete("/v1/chats/{sid}", dependencies=[Depends(auth)])
    async def chat_delete(sid: str):
        jade.sessions.delete(sid)
        return {"ok": True, "api": API}

    # ---- step mode
    def stepper_for(sid: str) -> tuple[Session, Stepper | None]:
        if sid in state["steppers"]:
            return state["steppers"][sid]
        s = get_session(sid)
        return s, None

    @app.post("/v1/chats/{sid}/step", dependencies=[Depends(auth)])
    async def step(sid: str, body: dict | None = None):
        body = body or {}
        async with lock:
            s, stp = stepper_for(sid) if sid != "new" else (jade.session(), None)
            if stp is None or stp.done:
                msg = body.get("message")
                if not msg:
                    return err(400, "missing_message", "start a step-mode turn with {\"message\": …}")
                stp = s.stepper(msg)
                state["steppers"][s.id] = (s, stp)
            evs = await stp.step()
            if stp.done:
                state["steppers"].pop(s.id, None)
            return {"ok": True, "api": API, "session": info(s), "events": [e.to_dict() for e in evs], "done": stp.done,
                    "pending": [c.to_dict() for c in stp.pending], "reply": stp.final if stp.done else None}

    @app.get("/v1/chats/{sid}/pending", dependencies=[Depends(auth)])
    async def pending(sid: str):
        s, stp = state["steppers"].get(sid, (None, None))
        return {"ok": True, "api": API, "pending": [c.to_dict() for c in stp.pending] if stp else [], "active": bool(stp)}

    @app.post("/v1/chats/{sid}/skip", dependencies=[Depends(auth)])
    async def skip(sid: str, body: dict | None = None):
        s, stp = state["steppers"].get(sid, (None, None))
        if not stp or not stp.pending:
            return err(409, "nothing_pending", "no tool call is waiting")
        stp.skip((body or {}).get("id"), (body or {}).get("reason") or "skipped by the user")
        return {"ok": True, "api": API, "pending": [c.to_dict() for c in stp.pending]}

    @app.post("/v1/chats/{sid}/edit", dependencies=[Depends(auth)])
    async def edit(sid: str, body: dict):
        s, stp = state["steppers"].get(sid, (None, None))
        if not stp or not stp.pending:
            return err(409, "nothing_pending", "no tool call is waiting")
        stp.edit(body.get("id"), body.get("arguments") or {})
        return {"ok": True, "api": API, "pending": [c.to_dict() for c in stp.pending]}

    @app.post("/v1/chats/{sid}/cancel", dependencies=[Depends(auth)])
    async def cancel(sid: str):
        s, stp = state["steppers"].pop(sid, (None, None))
        if s:
            s.cancel()
        r = state["running"].get(sid)
        if r:
            r.cancel()
        return {"ok": True, "api": API, "stopped": bool(s or r)}

    # ---- tools
    @app.get("/v1/tools", dependencies=[Depends(auth)])
    async def tools():
        return {"ok": True, "api": API, "tools": [{"name": t.name, "description": t.describe(jade), "parameters": t.parameters, "core": t.core} for t in jade.tools.active(jade)]}

    @app.post("/v1/tools/{name}", dependencies=[Depends(auth)])
    async def tool_call(name: str, body: dict | None = None):
        if not jade.tools.get(name):
            return err(404, "unknown_tool", f"no tool named {name}")
        from .tools.registry import ToolContext
        r = await jade.tools.call(name, body or {}, ToolContext(jade))
        return JSONResponse({"ok": not r.error, "api": API, "result": r.output, "ms": r.ms}, status_code=200 if not r.error else 422)

    @app.get("/v1/events", dependencies=[Depends(auth)])
    async def events_stream():
        """Server-Sent Events: notify (schedules, the heartbeat…), approval / approval_done (a tool call waiting for a yes), sentry, phone."""
        q: asyncio.Queue = asyncio.Queue()
        listeners.add(q)

        async def gen():
            try:
                for a in approvals.values():   # ones already waiting
                    yield {"event": "approval", "data": json.dumps({k: v for k, v in a.items() if k != "future"}, ensure_ascii=False)}
                while True:
                    try:
                        m = await asyncio.wait_for(q.get(), 20)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {"event": m["event"], "data": json.dumps(m["data"], ensure_ascii=False, default=str)}
            finally:
                listeners.discard(q)
        return EventSourceResponse(gen())

    @app.get("/v1/schedules", dependencies=[Depends(auth)])
    async def schedules():
        sch = getattr(jade, "scheduler", None)
        out = []
        for x in (sch.list if sch else []):
            try:
                out.append({**x, "when_text": sch.when(x)})
            except Exception:  # noqa: BLE001
                out.append(x)
        return {"ok": True, "api": API, "schedules": out, "daemon": jade.daemon_running}

    # ---- flows
    @app.get("/v1/flows", dependencies=[Depends(auth)])
    async def flows_list():
        F = getattr(jade, "flows", None)
        if not F:
            return err(404, "no_flows", "flows are not available")
        out = [{"name": f["name"], "path": f["_path"], "description": f.get("description", ""), "enabled": f.get("enabled") is not False,
                "trigger": f.get("trigger") or {"type": "manual"}, "steps": len(f.get("steps") or []), "invalid": f.get("_bad")} for f in F.list()]
        return {"ok": True, "api": API, "flows": out, "active": list(F.active.values())}

    @app.get("/v1/flows/runs", dependencies=[Depends(auth)])
    async def flow_runs(limit: int = 20):
        F = getattr(jade, "flows", None)
        return {"ok": True, "api": API, "runs": F.recent()[: max(1, min(100, limit))] if F else []}

    @app.post("/v1/flows/{name}/run", dependencies=[Depends(auth)])
    async def flow_run(name: str, body: dict | None = None):
        F = getattr(jade, "flows", None)
        f = F.find(name) if F else None
        if not f:
            return err(404, "unknown_flow", f"no flow named {name}")
        run = await F.run(f, (body or {}).get("input"), via="api")
        return JSONResponse({"ok": run["status"] == "ok", "api": API, "run": run}, status_code=200 if run["status"] == "ok" else 422)

    @app.post("/v1/flows/runs/{run_id}/stop", dependencies=[Depends(auth)])
    async def flow_stop(run_id: str):
        F = getattr(jade, "flows", None)
        return {"ok": bool(F and F.stop(run_id)), "api": API}

    @app.get("/v1/approvals", dependencies=[Depends(auth)])
    async def approvals_list():
        return {"ok": True, "api": API, "approvals": [{k: v for k, v in a.items() if k != "future"} for a in approvals.values()]}

    @app.post("/v1/approvals/{aid}", dependencies=[Depends(auth)])
    async def approvals_answer(aid: str, body: dict | None = None):
        a = approvals.get(aid)
        if not a:
            return err(404, "unknown_approval", "nothing is waiting with that id (it may have timed out)")
        if not a["future"].done():
            a["future"].set_result(bool((body or {}).get("allow")))
        return {"ok": True, "api": API}

    # ---- phones (Jade Link)
    def links():
        L = getattr(jade, "phones", None)
        if L is None:
            raise HTTPException(404, {"ok": False, "api": API, "error": {"code": "no_phones", "message": "phone support is not available"}})
        return L

    def link_on():
        if not st.link_on:
            raise HTTPException(403, {"ok": False, "api": API, "error": {"code": "link_off", "message": "this jade-py does not accept phones (link_on = false)"}})

    @app.post("/v1/link/poll", dependencies=[Depends(auth), Depends(link_on)])
    async def link_poll(body: dict):
        dev = body.get("device") if isinstance(body.get("device"), dict) else None
        if not dev or not dev.get("id"):
            return err(400, "missing_device", "send device: {id, name, model, …}")
        L = links()
        known = L.find_device(str(dev["id"]))
        if not known or not known.online:
            broadcast("phone", {"name": dev.get("name") or dev.get("model"), "online": True})
        reqs = await L.poll(dev, float(body.get("wait") or 25))
        p = L.find_device(str(dev["id"]))
        return {"ok": True, "api": API, "name": p.name if p else None, "requests": reqs}

    @app.post("/v1/link/result", dependencies=[Depends(auth), Depends(link_on)])
    async def link_result(body: dict):
        ok = await links().result(str(body.get("device_id") or ""), str(body.get("id") or ""), body.get("result"))
        return {"ok": True, "api": API, "delivered": ok}

    @app.post("/v1/link/events", dependencies=[Depends(auth), Depends(link_on)])
    async def link_events(body: dict):
        evs = [e for e in body.get("events") or [] if isinstance(e, dict)][:100]
        n = await links().events(str(body.get("device_id") or ""), evs)
        for e in evs:
            if e.get("type") == "sentry_fire":
                broadcast("sentry", {"phone": body.get("device_id"), **{k: v for k, v in (e.get("data") or {}).items() if k != "snapshot_b64"}})
        return {"ok": True, "api": API, "accepted": n}

    @app.get("/v1/phones", dependencies=[Depends(auth)])
    async def phones_list():
        L = links()
        L.sync_direct()
        return {"ok": True, "api": API, "phones": L.list(), "default": st.phone_default or None, "link_on": st.link_on}

    @app.post("/v1/phones/{name}/call", dependencies=[Depends(auth)])
    async def phone_call(name: str, body: dict):
        from .tools.registry import ToolError
        try:
            r = await links().get(name).call(str(body.get("op") or "info"), body.get("args") or {})
        except ToolError as e:
            return err(502, "phone_error", str(e))
        return {"ok": True, "api": API, "result": r}

    @app.get("/v1/sentry", dependencies=[Depends(auth)])
    async def sentry_status():
        S = getattr(jade, "sentry", None)
        if not S:
            return err(404, "no_sentry", "Sentry is not available")
        await S.watch()
        return {"ok": True, "api": API, **(await S.listing()), "log": S.log[:50], "rules": S.cfg()}

    @app.get("/health")
    async def health():
        return {"ok": True}

    from .dashboard import add_routes
    add_routes(app, jade, auth, err, state)
    return app


def serve(jade: Jade, host: str | None = None, port: int | None = None) -> None:
    import uvicorn
    st = jade.settings
    host = host or st.api_host
    port = port or st.api_port
    if host not in ("127.0.0.1", "localhost", "::1") and not st.api_token:
        st.api_token = "jv_" + secrets.token_hex(18)
        print(f"Listening beyond this machine needs a token. For this run it is: {st.api_token}\n(set api_token in the config to keep one)")
    uvicorn.run(create_app(jade, daemon=True), host=host, port=port, log_level="info")
