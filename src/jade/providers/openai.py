"""Any OpenAI-compatible chat completions endpoint: llama.cpp, LM Studio, vLLM, OpenAI, Groq, OpenRouter, Gemini…"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import Chunk, Provider, ProviderError, ToolCall, Usage

SKIP_MODELS = ("whisper", "tts", "-guard", "guard-", "embed", "moderation", "dall-e", "image", "audio", "transcri")


def to_openai(messages: list[dict]) -> list[dict]:
    out = []
    known: set[str] = set()
    for m in messages:
        r = m.get("role")
        if r == "tool":
            cid = m.get("tool_call_id")
            if cid and cid in known:
                out.append({"role": "tool", "tool_call_id": cid, "content": str(m.get("content") or "")})
            else:
                out.append({"role": "user", "content": f"[Result of {m.get('name') or 'a tool'}]\n{m.get('content') or ''}"})
            continue
        if r == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                known.add(c["id"])
                calls.append({"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})}})
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif r == "user" and m.get("images"):
            parts = [{"type": "text", "text": str(m.get("content") or "")}]
            parts += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}} for b in m["images"]]
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": r, "content": str(m.get("content") or "")})
    return out


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, base_url: str, model: str = "", *, api_key: str = "", name: str = "openai", temperature: float | None = None,
                 timeout: float = 600.0, headers: dict | None = None, client: httpx.AsyncClient | None = None):
        if not base_url:
            raise ProviderError("no base URL for the OpenAI-compatible provider (settings.base_url)")
        self.base = base_url.rstrip("/")
        self.model, self.name, self.temperature = model, name, temperature
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def _err(self, r: httpx.Response) -> ProviderError:
        raw = (await r.aread()).decode(errors="replace")
        msg = f"HTTP {r.status_code}"
        try:
            j = json.loads(raw)
            e = j.get("error")
            msg = (e.get("message") if isinstance(e, dict) else e) or j.get("message") or msg
        except ValueError:
            msg = raw[:300] or msg
        if r.status_code == 401:
            msg = "the API key was rejected (401)"
        return ProviderError(f"{self.name}: {msg}", r.status_code, retriable=r.status_code >= 500 or r.status_code == 429)

    async def models(self) -> list[str]:
        try:
            r = await self._client.get(self.base + "/models", headers=self.headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"cannot reach {self.base}: {e}", retriable=True) from e
        if r.status_code != 200:
            raise await self._err(r)
        j = r.json()
        ids = [m.get("id") or m.get("name") for m in (j.get("data") or j.get("models") or [])]
        return [i for i in ids if i and not any(s in i.lower() for s in SKIP_MODELS)]

    async def chat(self, messages, tools=None, *, model=None, **opts) -> AsyncIterator[Chunk]:
        body = {"model": model or self.model, "messages": to_openai(messages), "stream": True}
        t = opts.get("temperature", self.temperature)
        if t is not None:
            body["temperature"] = t
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        if self.name != "groq":
            body["stream_options"] = {"include_usage": True}
        if opts.get("format") == "json":
            body["response_format"] = {"type": "json_object"}
        calls: dict[int, dict] = {}
        usage = None
        try:
            async with self._client.stream("POST", self.base + "/chat/completions", json=body, headers=self.headers) as r:
                if r.status_code != 200:
                    raise await self._err(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    j = json.loads(data)
                    if j.get("error"):
                        e = j["error"]
                        raise ProviderError(f"{self.name}: {e.get('message') if isinstance(e, dict) else e}")
                    if j.get("usage"):
                        u = j["usage"]
                        usage = Usage(u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0)
                    for ch in j.get("choices") or []:
                        d = ch.get("delta") or {}
                        c = Chunk(text=d.get("content") or "", thinking=d.get("reasoning_content") or d.get("reasoning") or "")
                        for tc in d.get("tool_calls") or []:
                            k = tc.get("index", 0)
                            slot = calls.setdefault(k, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            f = tc.get("function") or {}
                            if f.get("name"):
                                slot["name"] += f["name"]
                            if f.get("arguments"):
                                slot["args"] += f["arguments"]
                        if c.text or c.thinking:
                            yield c
        except httpx.HTTPError as e:
            raise ProviderError(f"cannot reach {self.base}: {e}", retriable=True) from e
        done = Chunk(done=True, usage=usage)
        for k in sorted(calls):
            s = calls[k]
            try:
                args = json.loads(s["args"]) if s["args"].strip() else {}
            except ValueError:
                args = {"_raw": s["args"]}
            done.tool_calls.append(ToolCall(name=s["name"], arguments=args if isinstance(args, dict) else {"value": args}, **({"id": s["id"]} if s["id"] else {})))
        yield done

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        r = await self._client.post(self.base + "/embeddings", json={"model": model, "input": texts}, headers=self.headers)
        if r.status_code != 200:
            raise await self._err(r)
        return [d["embedding"] for d in sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))]

    async def aclose(self):
        await self._client.aclose()
