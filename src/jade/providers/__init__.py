"""Model providers and the fallback chain."""
from __future__ import annotations

from typing import AsyncIterator

from ..settings import Settings
from .base import Chunk, Provider, ProviderError, ToolCall, Usage, tool_schema
from .ollama import OllamaProvider
from .openai import OpenAIProvider

__all__ = ["Chunk", "Provider", "ProviderError", "ToolCall", "Usage", "tool_schema", "OllamaProvider", "OpenAIProvider", "FallbackProvider", "make_provider"]


class FallbackProvider(Provider):
    """Tries each provider in turn when one cannot be reached (before it has produced anything)."""

    def __init__(self, providers: list[Provider]):
        self.providers = providers
        self.name = providers[0].name
        self.model = providers[0].model
        self.last: Provider = providers[0]

    async def chat(self, messages, tools=None, *, model=None, **opts) -> AsyncIterator[Chunk]:
        errors = []
        for i, p in enumerate(self.providers):
            started = False
            try:
                async for c in p.chat(messages, tools, model=model if i == 0 else None, **opts):
                    started = True
                    self.last = p
                    yield c
                return
            except ProviderError as e:
                if started or not (e.retriable or e.status in (None, 404)):
                    raise
                errors.append(f"{p.name}: {e}")
        raise ProviderError("no model provider answered: " + "; ".join(errors), retriable=True)

    async def models(self):
        return await self.providers[0].models()

    async def aclose(self):
        for p in self.providers:
            await p.aclose()


def _one(settings: Settings, provider: str, model: str) -> Provider:
    if provider in ("ollama", "ollama-cloud"):
        host = "https://ollama.com" if provider == "ollama-cloud" else settings.host
        key = settings.ollama_key or (settings.api_key if provider == settings.provider else "")
        return OllamaProvider(host, model, api_key=key if provider == "ollama-cloud" else "", num_ctx=settings.num_ctx, temperature=settings.temperature,
                              think=settings.think, keep_alive=settings.keep_alive, timeout=settings.request_timeout)
    base, key = settings.openai_base_and_key(provider)
    return OpenAIProvider(base, model, api_key=key, name=provider, temperature=settings.temperature, timeout=settings.request_timeout)


def make_provider(settings: Settings) -> Provider:
    main = _one(settings, settings.provider, settings.model)
    if not settings.fallbacks:
        return main
    chain = [main]
    for spec in settings.fallbacks:
        prov, _, model = spec.partition(":")
        chain.append(_one(settings, prov, model or settings.model))
    return FallbackProvider(chain)
