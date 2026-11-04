"""Jade Fleet: jade-py as one more member of a fleet run by a jade-fleet broker
(https://github.com/lucky-edgelord/jade-fleet). The broker holds the fleet's shared vault and memory search and
passes model requests on to the machine that runs the models (chat, embeddings, decisions, vision).

With settings.fleet_url and fleet_token set:
- vault_search: meaning-based search over the whole fleet's notes and memories (the broker's index);
- vision: object detection (YOLO) and classification (MobileNet) on the fleet's vision service;
- requests to the broker carry the fleet token and this machine's name; nothing else ever gets the token.

`jade init --fleet URL --fleet-token TOKEN` (or fleet_preset) points chat, embeddings and decisions at the broker.
To share the vault itself, run jade-py on the broker's machine with vault = the broker's vault folder."""
from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING

import httpx

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade
    from .settings import Settings


def fleet_preset(st: "Settings", url: str, token: str, device: str = "") -> "Settings":
    """Point every model setting at the broker (it passes them on to the model machine)."""
    base = url.rstrip("/")
    st.fleet_url, st.fleet_token = base, token
    if device:
        st.fleet_device = device
    st.provider, st.base_url, st.api_key = "custom", base + "/v1", token   # llama-server through the broker (OpenAI-compatible)
    st.host = base + "/ollama"            # Ollama through the broker
    st.embed_host = base + "/ollama"
    st.ollaya_host = base                 # /v1/systemone: the broker's decision server (Ollaya or Ollama)
    st.decide_provider = "ollaya"
    return st


async def api(jade: "Jade", method: str, path: str, **kw) -> dict:
    st = jade.settings
    if not st.fleet_url or not st.fleet_token:
        raise ToolError("not in a fleet: set fleet_url and fleet_token (jade init --fleet URL --fleet-token TOKEN)")
    url = st.fleet_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.request(method, url, headers={**kw.pop("headers", {}), **st.fleet_headers(url)}, **kw)
    except httpx.HTTPError as e:
        raise ToolError(f"could not reach the fleet broker at {st.fleet_url}: {e}") from None
    try:
        j = r.json()
    except ValueError:
        j = {"error": r.text[:300]}
    if r.status_code >= 400 or (isinstance(j, dict) and j.get("ok") is False):
        msg = j.get("error") if isinstance(j, dict) else r.text
        if r.status_code == 401:
            msg = "the broker refused the fleet token (fleet_token)"
        raise ToolError(f"fleet broker: {msg} (HTTP {r.status_code})")
    return j


async def vault_search(ctx, query: str = "", k: int = 6, kind: str = "all", **_) -> str:
    if not str(query).strip():
        raise ToolError("query is required")
    k = min(20, max(1, int(k or 6)))
    j = await api(ctx.jade, "GET", "/v1/search", params={"q": str(query), "k": k, "kind": kind if kind in ("all", "note", "memory") else "all"})
    hits = j.get("hits") or []
    ctx.say(f"{len(hits)} result{'' if len(hits) == 1 else 's'}")
    if not hits:
        return "Nothing found. (The fleet indexes notes in the background; a note written moments ago may not be searchable yet.)"
    return "\n\n".join((f"[note] {h['source']} (distance {h['distance']:.3f})\n{h['content']}" if h.get("kind") == "note"
                        else f"[memory · {h.get('source')}] (distance {h['distance']:.3f})\n{h['content']}") for h in hits)


async def vision(ctx, action: str = "detect", path: str = "", conf: float = 0.25, top: int = 5, **_) -> str:
    action = "classify" if action == "classify" else "detect"
    v = ctx.jade.vault
    p = v.norm(str(path or ""))
    if not v.is_file(p):
        raise ToolError(f"{path}: no such image in the vault")
    ctype = mimetypes.guess_type(p)[0] or "image/jpeg"
    params = {"conf": conf} if action == "detect" else {"top": top}
    j = await api(ctx.jade, "POST", "/v1/vision/" + action, params=params, content=v.read_bytes(p), headers={"Content-Type": ctype})
    ctx.say(f"{j.get('device', '')} · {(j.get('ms') or {}).get('total', '?')} ms")
    size = f"{j.get('width')}×{j.get('height')}"
    if action == "classify":
        return f"{p} ({size}), {j.get('model')} on {j.get('device')}:\n" + "\n".join(f"{t['label']}: {t['prob'] * 100:.1f}%" for t in j.get("top") or [])
    dets = j.get("detections") or []
    if not dets:
        return f"{p} ({size}): nothing found at confidence {conf} or more."
    return (f"{p} ({size}), {j.get('model')} on {j.get('device')}, {len(dets)} object{'' if len(dets) == 1 else 's'}:\n"
            + "\n".join(f"{d['label']} {d['confidence'] * 100:.0f}% at [{', '.join(str(round(x)) for x in d['box'])}]" for d in dets))


SPECS = {
    "vault_search": {"description": "Search the whole fleet's shared vault and long-term memories by meaning (vector search on the fleet server), not just by words. Use it to find notes about a topic "
                                    "even when they use other words, or to recall what any phone or machine saved. Returns the nearest note passages (with their paths) and memories.",
                     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "What to look for, in plain words"},
                                                                     "k": {"type": "number", "description": "How many results (default 6, max 20)"},
                                                                     "kind": {"type": "string", "enum": ["all", "note", "memory"], "description": "notes, memories or both (default)"}}, "required": ["query"]}},
    "vision": {"description": "Look at an image in the vault with the fleet's vision models: detect finds objects (YOLO, 80 everyday kinds such as person, car, dog, cup) with boxes in the "
                              "image's pixels; classify names what the picture shows (MobileNet, 1000 ImageNet classes).",
               "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["detect", "classify"]}, "path": {"type": "string", "description": "Image file in the vault (jpg, png, webp, bmp)"},
                                                               "conf": {"type": "number", "description": "detect: lowest confidence to report (default 0.25)"},
                                                               "top": {"type": "number", "description": "classify: how many guesses (default 5)"}}, "required": ["action", "path"]}},
}


def setup(jade: "Jade") -> None:
    on = lambda j: bool(j.settings.fleet_url and j.settings.fleet_token)   # noqa: E731
    jade.tools.add(Tool("vault_search", SPECS["vault_search"]["description"], SPECS["vault_search"]["parameters"], vault_search, core=False, enabled=on,
                        hints="fleet search meaning shared vault memories recall semantic", summary="vault_search: search the whole fleet's notes and memories by meaning"))
    jade.tools.add(Tool("vision", SPECS["vision"]["description"], SPECS["vision"]["parameters"], vision, core=False, enabled=on,
                        hints="vision detect objects classify image picture photo yolo mobilenet what is in", summary="vision: detect objects in or classify a vault image (the fleet's vision models)"))

