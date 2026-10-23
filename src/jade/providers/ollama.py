"""Ollama (/api/chat), local or Ollama Cloud (ollama.com with an API key)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import Chunk, Provider, ProviderError, ToolCall, Usage


def to_ollama(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        r = m.get("role")
        if r == "tool":
            out.append({"role": "tool", "content": str(m.get("content") or ""), "tool_name": m.get("name") or ""})
            continue
        o = {"role": r, "content": str(m.get("content") or "")}
        if m.get("images"):
            o["images"] = list(m["images"])
        if r == "assistant" and m.get("tool_calls"):
            o["tool_calls"] = [{"function": {"name": c["name"], "arguments": c.get("arguments") or {}}} for c in m["tool_calls"]]
        if r == "assistant" and m.get("thinking"):
            o["thinking"] = m["thinking"]
        out.append(o)
    return out


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", model: str = "", *, api_key: str = "", num_ctx: int | None = None,
                 temperature: float | None = None, think: bool | None = None, keep_alive: str | None = None, timeout: float = 600.0,
                 client: httpx.AsyncClient | None = None):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx, self.temperature, self.think, self.keep_alive = num_ctx, temperature, think, keep_alive
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))
        if "ollama.com" in self.host:
            self.name = "ollama-cloud"

    async def models(self) -> list[str]:
        try:
            r = await self._client.get(self.host + "/api/tags", headers=self.headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"cannot reach Ollama at {self.host}: {e}", retriable=True) from e
        if r.status_code != 200:
            raise ProviderError(f"Ollama: HTTP {r.status_code}", r.status_code)
        return [m.get("name") or m.get("model") for m in r.json().get("models", [])]

    async def chat(self, messages, tools=None, *, model=None, **opts) -> AsyncIterator[Chunk]:
        body = {"model": model or self.model, "messages": to_ollama(messages), "stream": True}
        if tools:
            body["tools"] = tools
        options = {}
        t = opts.get("temperature", self.temperature)
        if t is not None:
            options["temperature"] = t
        n = opts.get("num_ctx", self.num_ctx)
        if n:
            options["num_ctx"] = n
        if opts.get("options"):
            options.update(opts["options"])
        if options:
            body["options"] = options
        think = opts.get("think", self.think)
        if think is not None:
            body["think"] = think
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        if opts.get("format"):
            body["format"] = opts["format"]
        try:
            async with self._client.stream("POST", self.host + "/api/chat", json=body, headers=self.headers) as r:
                if r.status_code != 200:
                    raw = (await r.aread()).decode(errors="replace")
                    try:
                        msg = json.loads(raw).get("error") or raw
                    except ValueError:
                        msg = raw or f"HTTP {r.status_code}"
                    if r.status_code == 401:
                        msg = "the API key was rejected (401)"
                    raise ProviderError(f"Ollama: {msg}", r.status_code, retriable=r.status_code >= 500 or r.status_code == 429)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    j = json.loads(line)
                    if j.get("error"):
                        raise ProviderError(f"Ollama: {j['error']}")
                    m = j.get("message") or {}
                    c = Chunk(text=m.get("content") or "", thinking=m.get("thinking") or "")
                    for tc in m.get("tool_calls") or []:
                        f = tc.get("function") or {}
                        args = f.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {"_raw": args}
                        c.tool_calls.append(ToolCall(name=f.get("name") or "", arguments=args))
                    if j.get("done"):
                        c.done = True
                        c.usage = Usage(j.get("prompt_eval_count") or 0, j.get("eval_count") or 0)
                    yield c
        except httpx.HTTPError as e:
            raise ProviderError(f"cannot reach Ollama at {self.host}: {e}", retriable=True) from e

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        r = await self._client.post(self.host + "/api/embed", json={"model": model, "input": texts}, headers=self.headers)
        if r.status_code != 200:
            raise ProviderError(f"embeddings: HTTP {r.status_code} {r.text[:200]}", r.status_code)
        return r.json().get("embeddings") or []

    async def aclose(self):
        await self._client.aclose()
