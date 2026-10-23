"""What every model provider gives the agent: a stream of chunks (text, thinking, finished tool calls, usage)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Chunk:
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    usage: Usage | None = None


class ProviderError(Exception):
    def __init__(self, msg: str, status: int | None = None, retriable: bool = False):
        super().__init__(msg)
        self.status = status
        self.retriable = retriable


class Provider:
    """Messages are Jade's own format: {role, content, tool_calls: [{id, name, arguments}], tool_call_id, name, images}."""
    name = "provider"
    model: str = ""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, *, model: str | None = None, **opts) -> AsyncIterator[Chunk]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def models(self) -> list[str]:
        return []

    async def complete(self, messages: list[dict], **opts) -> str:
        """One answer, no tools."""
        out = []
        async for c in self.chat(messages, None, **opts):
            out.append(c.text)
        return "".join(out)

    async def aclose(self) -> None:
        pass


def tool_schema(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}
