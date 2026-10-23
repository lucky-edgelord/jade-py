"""Phones running the Jade Link app, reached either way:

  dial-in   the phone keeps a connection open to `jade serve` (POST /v1/link/poll, long-poll). Works on mobile data and
            behind routers: only jade-py needs to be reachable. The phone signs in with the API token.
  direct    the phone listens (Jade Link's "Let Jade connect to this phone") and jade-py calls it:
            phones = [{name = "pixel", url = "http://192.168.1.20:8765", token = "…"}] in the config.

Both carry the same messages. jade-py sends {id, op, args}; the phone answers with any JSON (an object with "error" is a
failure). The phone sends events {type, data, at}: Sentry fires, mainly. Ops: info, phone, screen, senses, ask, notify,
perms, sentry.configure / status / pending / ack / emit / set / systems.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

import httpx

from .tools.registry import ToolError

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade.phone")
OFFLINE_AFTER = 75          # seconds without a poll before a dial-in phone counts as offline
POLL_WAIT = 25

# how long an op may take before jade-py gives up waiting (seconds)
def op_timeout(op: str, args: dict | None) -> float:
    a = args or {}
    act = str(a.get("action") or "")
    if op == "ask":
        return float(a.get("timeout") or 600) + 10
    if op == "phone" and act in ("bt_scan", "bt_connect", "bt_disconnect") or act.startswith("cast_"):
        return 75
    if op == "senses" and act in ("mic_record", "mic_level", "read", "camera_watch", "location"):
        return min(180, float(a.get("seconds") or a.get("timeout") or 10) * (int(a.get("frames") or 1) if act == "camera_watch" else 1) + 30)
    if op == "screen" and act == "wait":
        return float(a.get("seconds") or 5) + 30
    if op == "perms":
        return 150
    return 45


class Phone:
    kind = "?"

    def __init__(self, links: "Links", name: str):
        self.links = links
        self.name = name
        self.info: dict = {}
        self.last_seen = 0.0
        self.error = ""

    @property
    def online(self) -> bool:
        return time.time() - self.last_seen < OFFLINE_AFTER

    async def request(self, op: str, args: dict | None = None, timeout: float | None = None) -> Any:
        raise NotImplementedError

    async def call(self, op: str, args: dict | None = None, timeout: float | None = None) -> Any:
        """Run an op on the phone. Returns its answer; raises ToolError if the phone reports an error or cannot be reached."""
        r = await self.request(op, args or {}, timeout or op_timeout(op, args))
        if isinstance(r, dict) and r.get("error") and r.get("ok") is not True:
            raise ToolError(str(r["error"]))
        return r

    def describe(self) -> dict:
        return {"name": self.name, "via": self.kind, "online": self.online, "last_seen": int(self.last_seen) or None,
                "model": self.info.get("model"), "android": self.info.get("android"), "app": self.info.get("version"),
                **({"error": self.error} if self.error and not self.online else {})}


class DialInPhone(Phone):
    """A phone that polls us. Requests wait in a queue until its next poll; answers come back by id."""
    kind = "dial-in"

    def __init__(self, links: "Links", name: str, device_id: str):
        super().__init__(links, name)
        self.device_id = device_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.waiting: dict[str, asyncio.Future] = {}

    async def request(self, op: str, args: dict | None = None, timeout: float | None = None) -> Any:
        if not self.online:
            raise ToolError(f"the phone {self.name} is offline (last heard from {_ago(self.last_seen)}). Is Jade Link running and connected?")
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        self.queue.put_nowait({"id": rid, "op": op, "args": args or {}})
        try:
            return await asyncio.wait_for(fut, timeout or 45)
        except asyncio.TimeoutError:
            raise ToolError(f"the phone {self.name} did not answer {op} in time") from None
        finally:
            self.waiting.pop(rid, None)

    async def take(self, wait: float) -> list[dict]:
        """For the poll endpoint: requests waiting now, or the first one to arrive within `wait` seconds."""
        out: list[dict] = []
        try:
            out.append(await asyncio.wait_for(self.queue.get(), wait) if self.queue.empty() else self.queue.get_nowait())
        except asyncio.TimeoutError:
            return out
        while not self.queue.empty() and len(out) < 20:
            out.append(self.queue.get_nowait())
        return [r for r in out if r["id"] in self.waiting]   # skip ones whose caller gave up

    def answer(self, rid: str, result: Any) -> bool:
        fut = self.waiting.get(rid)
        if fut and not fut.done():
            fut.set_result(result)
            return True
        return False


class DirectPhone(Phone):
    """A phone that listens: we call it over HTTP and long-poll its events."""
    kind = "direct"

    def __init__(self, links: "Links", name: str, url: str, token: str):
        super().__init__(links, name)
        self.url = url.rstrip("/")
        self.token = token
        self.seq = -1
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}, timeout=httpx.Timeout(60, connect=8))
        return self._client

    async def request(self, op: str, args: dict | None = None, timeout: float | None = None) -> Any:
        try:
            r = await self.client().post(self.url + "/v1/call", json={"op": op, "args": args or {}}, timeout=httpx.Timeout(timeout or 45, connect=8))
        except httpx.TimeoutException:
            raise ToolError(f"the phone {self.name} did not answer {op} in time") from None
        except httpx.HTTPError as e:
            self.error = str(e)
            raise ToolError(f"cannot reach the phone {self.name} at {self.url} ({e.__class__.__name__}). Is Jade Link listening, and is the phone on the same network?") from None
        if r.status_code == 401:
            raise ToolError(f"the phone {self.name} refused the token (check phones[].token against Jade Link's listening token)")
        try:
            j = r.json()
        except ValueError:
            raise ToolError(f"the phone {self.name} sent something that is not JSON (HTTP {r.status_code})") from None
        self.last_seen = time.time()
        if isinstance(j, dict) and "result" in j:
            return j["result"]
        if isinstance(j, dict) and j.get("error"):
            return {"error": j["error"] if isinstance(j["error"], str) else json.dumps(j["error"])}
        return j

    async def events_loop(self, stopping: asyncio.Event) -> None:
        """Long-poll the phone's events while the daemon runs; reconnect with backoff."""
        delay = 2
        while not stopping.is_set():
            was = self.online
            try:
                r = await self.client().get(self.url + "/v1/events", params={"since": self.seq, "wait": POLL_WAIT}, timeout=httpx.Timeout(POLL_WAIT + 15, connect=8))
                if r.status_code == 401:
                    self.error = "the phone refused the token"
                    raise RuntimeError(self.error)
                j = r.json()
                self.last_seen = time.time()
                self.error = ""
                delay = 2
                if j.get("info"):
                    self.info = j["info"]
                if not was:
                    await self.links._online(self)
                evs = j.get("events") or []
                if evs:
                    self.seq = max(int(e.get("seq", self.seq)) for e in evs)
                elif "seq" in j and self.seq < 0:
                    self.seq = int(j["seq"])
                for e in evs:
                    await self.links._event(self, e)
            except Exception as e:  # noqa: BLE001 - keep trying
                self.error = self.error or str(e)
                try:
                    await asyncio.wait_for(stopping.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(60, delay * 2)

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def _ago(t: float) -> str:
    if not t:
        return "never"
    s = int(time.time() - t)
    return f"{s}s ago" if s < 120 else f"{s // 60} min ago" if s < 7200 else f"{s // 3600} h ago"


class Links:
    """All phones, by name. jade.phones."""

    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.phones: dict[str, Phone] = {}
        self.on_event: list[Callable[[Phone, dict], Any]] = []
        self.on_online: list[Callable[[Phone], Any]] = []
        self.sync_direct()

    # ---- configuration
    def sync_direct(self) -> None:
        want = {}
        for p in self.jade.settings.phones or []:
            if isinstance(p, dict) and p.get("url"):
                want[_clean_name(p.get("name") or p["url"])] = p
        for n, ph in list(self.phones.items()):
            if isinstance(ph, DirectPhone) and n not in want:
                del self.phones[n]
        for n, p in want.items():
            cur = self.phones.get(n)
            if not isinstance(cur, DirectPhone) or cur.url != p["url"].rstrip("/") or cur.token != p.get("token", ""):
                self.phones[n] = DirectPhone(self, n, p["url"], p.get("token", ""))

    def get(self, name: str | None = None) -> Phone:
        """The named phone, or the default one (settings.phone_default, else the only/first online phone)."""
        if name:
            ph = self.phones.get(name) or next((p for n, p in self.phones.items() if n.lower() == str(name).lower()), None)
            if not ph:
                raise ToolError(f"no phone named {name}. Phones: {', '.join(self.phones) or 'none yet (install Jade Link and connect it)'}")
            return ph
        d = self.jade.settings.phone_default
        if d and d in self.phones:
            return self.phones[d]
        if not self.phones:
            raise ToolError("no phone is linked. Install the Jade Link app on the phone and connect it to this jade-py (see `jade phone setup`).")
        online = [p for p in self.phones.values() if p.online]
        return (online or list(self.phones.values()))[0]

    def pick(self, want) -> list[Phone]:
        """The phones a tool call names: none (the main one), a name, a list or comma-separated names, or "all" (every online one)."""
        if want is None or want == "" or want == []:
            return [self.get()]
        names = want if isinstance(want, list) else [x.strip() for x in str(want).split(",") if x.strip()]
        if any(str(n).lower() in ("all", "*", "every", "everyone") for n in names):
            on = [p for p in self.phones.values() if p.online]
            if not on:
                raise ToolError("no phone is online right now. Phones: " + (", ".join(self.phones) or "none linked"))
            return on
        out: list[Phone] = []
        for n in names:
            p = self.get(str(n))
            if p not in out:
                out.append(p)
        return out

    def list(self) -> list[dict]:
        return [p.describe() for p in self.phones.values()]

    # ---- dial-in (called by the API)
    def dial_in(self, device: dict) -> DialInPhone:
        did = re.sub(r"[^\w.-]", "", str(device.get("id") or ""))[:64] or "phone"
        for p in self.phones.values():
            if isinstance(p, DialInPhone) and p.device_id == did:
                break
        else:
            name = base = _clean_name(device.get("name") or device.get("model") or "phone")
            i = 2
            while name in self.phones:
                name = f"{base}-{i}"
                i += 1
            p = DialInPhone(self, name, did)
            self.phones[name] = p
        want = _clean_name(device.get("name") or "")
        if want and want != p.name and want not in self.phones:   # renamed on the phone
            self.phones.pop(p.name, None)
            p.name = want
            self.phones[want] = p
        p.info = {k: v for k, v in device.items() if k != "id"}
        return p

    async def poll(self, device: dict, wait: float = POLL_WAIT) -> list[dict]:
        p = self.dial_in(device)
        was = p.online
        p.last_seen = time.time()
        if not was:
            asyncio.get_running_loop().create_task(self._online(p))
        reqs = await p.take(max(0.0, min(float(wait), 55)))
        p.last_seen = time.time()
        return reqs

    def find_device(self, device_id: str) -> DialInPhone | None:
        return next((p for p in self.phones.values() if isinstance(p, DialInPhone) and p.device_id == device_id), None)

    async def result(self, device_id: str, rid: str, result: Any) -> bool:
        p = self.find_device(device_id)
        if not p:
            return False
        p.last_seen = time.time()
        return p.answer(rid, result)

    async def events(self, device_id: str, events: list[dict]) -> int:
        p = self.find_device(device_id)
        if not p:
            return 0
        p.last_seen = time.time()
        for e in events:
            await self._event(p, e)
        return len(events)

    # ---- hooks
    async def _event(self, p: Phone, e: dict) -> None:
        for f in list(self.on_event):
            try:
                r = f(p, e)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as ex:  # noqa: BLE001
                log.warning("phone event %s: %s", e.get("type"), ex)

    async def _online(self, p: Phone) -> None:
        log.info("phone %s is online (%s)", p.name, p.kind)
        if not p.info:
            try:
                info = await p.request("info", {}, 20)
                if isinstance(info, dict) and not info.get("error"):
                    p.info = {**p.info, **info}
            except Exception:  # noqa: BLE001
                pass
        for f in list(self.on_online):
            try:
                r = f(p)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as ex:  # noqa: BLE001
                log.warning("phone online hook: %s", ex)

    async def loop(self, daemon) -> None:
        """Daemon loop: long-poll the events of phones that listen."""
        tasks: dict[int, tuple[Phone, asyncio.Task]] = {}
        while not daemon.stopping.is_set():
            self.sync_direct()
            live = {id(p): p for p in self.phones.values() if isinstance(p, DirectPhone)}
            for k in list(tasks):
                if k not in live:   # removed, or replaced by a changed url or token
                    tasks.pop(k)[1].cancel()
            for k, p in live.items():
                if k not in tasks or tasks[k][1].done():
                    tasks[k] = (p, asyncio.create_task(p.events_loop(daemon.stopping)))
            if await daemon._sleep(5):
                break
        for _, t in tasks.values():
            t.cancel()

    async def aclose(self) -> None:
        for p in self.phones.values():
            if isinstance(p, DirectPhone):
                await p.aclose()

    # ---- asking the person on the phone
    async def ask(self, title: str, detail: str, phone: Phone | None = None, timeout: int = 600) -> bool:
        """A yes/no on the phone's screen (a notification with Allow / Don't). False if nobody answers."""
        ph = phone or self.get()
        r = await ph.call("ask", {"title": title, "detail": detail, "timeout": timeout})
        return bool(isinstance(r, dict) and r.get("allowed"))


def _clean_name(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", str(s).strip()).strip("-").lower()
    return s[:40] or "phone"


def setup(jade: "Jade") -> None:
    jade.phones = Links(jade)
    jade.daemon_loops.append(lambda daemon: jade.phones.loop(daemon))
    jade.close_hooks.append(jade.phones.aclose)
