"""The model router: before each chat turn, the decision model (laya, on Ollaya, Ollama 0.35+ or the fleet broker's
/v1/systemone) reads the message and picks which of your models should answer it. Each model in router_models has a
short note saying what it is good at; laya chooses between the notes and returns calibrated probabilities.

    router_on = true
    router_models = [
      {model = "ollama:qwen3:8b",              note = "quick everyday chat, short answers, simple lookups"},
      {model = "openrouter:anthropic/claude-sonnet-4.5", note = "coding, long documents, hard multi-step reasoning"},
    ]

model is "provider:model" (a provider name from the presets, then the model), or just a model name for the main
provider. If laya is unreachable, unsure (below router_min_confidence) or no models are listed, the main model answers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Jade
    from .settings import Settings

QUESTION = ("Which model should answer the user's latest message? Each option describes what a model is good at. "
            "Pick the cheapest option that can answer this message well; pick a stronger one only when the message needs it.")


@dataclass
class Route:
    provider: str
    model: str
    note: str
    confidence: float | None
    ms: int
    error: str = ""


def parse_spec(settings: "Settings", spec: str) -> tuple[str, str]:
    """"provider:model" -> (provider, model); a bare model name uses the main provider. Ollama tags keep their colon
    ("ollama:qwen3:8b" -> ollama, qwen3:8b; "qwen3:8b" alone -> main provider, qwen3:8b)."""
    from .settings import OPENAI_PRESETS
    spec = (spec or "").strip()
    head, sep, rest = spec.partition(":")
    if sep and (head in OPENAI_PRESETS or head in ("ollama", "ollama-cloud")):
        return head, rest or settings.model
    return settings.provider, spec or settings.model


def options(settings: "Settings") -> list[dict]:
    out = []
    for m in settings.router_models or []:
        if isinstance(m, str):
            m = {"model": m}
        spec = str(m.get("model") or "").strip()
        if spec:
            out.append({"model": spec, "note": str(m.get("note") or "").strip() or spec})
    return out


def router_ready(settings: "Settings") -> bool:
    return bool(settings.router_on and len(options(settings)) >= 2 and (settings.router_decider or settings.ollaya_model))


def state_for(messages: list[dict], limit: int = 4000) -> str:
    """What laya reads: the latest message, with the previous exchange for context (a follow-up like "and in Rust?"
    needs it)."""
    users = [m for m in messages if m.get("role") == "user" and not m.get("auto")]
    if not users:
        return ""
    last = str(users[-1].get("display") or users[-1].get("content") or "")
    prev = ""
    i = messages.index(users[-1])
    for m in reversed(messages[:i]):
        if m.get("role") == "assistant" and m.get("content"):
            prev = str(m["content"])[-600:]
            break
    s = f"Previous reply (end): {prev}\n\nLatest message: {last}" if prev else f"Latest message: {last}"
    return s[-limit:]


async def pick(jade: "Jade", messages: list[dict]) -> Route | None:
    """Ask laya which listed model should take this turn. None when the router is off; a Route with error set (and
    the main model) when laya could not decide."""
    from .reasoning import decide_data
    st = jade.settings
    if not router_ready(st):
        return None
    opts = options(st)
    t0 = time.monotonic()
    main = Route(st.provider, st.model, "main model", None, 0)
    try:
        d = await decide_data(jade, state_for(messages), QUESTION, "choice", [o["note"] for o in opts], model=st.router_decider)
    except Exception as e:
        main.error, main.ms = f"router: {e}", int((time.monotonic() - t0) * 1000)
        return main
    ms = int((time.monotonic() - t0) * 1000)
    conf = d.get("confidence")
    idx = _index(d.get("choice"), opts)
    if idx is None:
        main.error, main.ms = f"router: laya answered {d.get('choice')!r}", ms
        return main
    if conf is not None and conf < st.router_min_confidence:
        main.error, main.ms, main.confidence = f"router: laya was unsure ({conf:.2f})", ms, conf
        return main
    prov, model = parse_spec(st, opts[idx]["model"])
    return Route(prov, model, opts[idx]["note"], conf, ms)


def _index(choice, opts: list[dict]) -> int | None:
    if isinstance(choice, int) or (isinstance(choice, str) and choice.isdigit()):
        i = int(choice)
        return i if 0 <= i < len(opts) else None
    for i, o in enumerate(opts):
        if choice == o["note"]:
            return i
    return None
