"""What a turn reports while it runs. Every event has .type; iterate a turn to watch it, or ignore them."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class Event:
    """Base of all events (each subclass is a dataclass with its fields, then type)."""
    type: str = "event"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


@dataclass
class Text(Event):
    """A piece of the reply as it streams."""
    text: str = ""
    type: str = field(default="text", init=False)


@dataclass
class Thinking(Event):
    text: str = ""
    type: str = field(default="thinking", init=False)


@dataclass
class ModelDone(Event):
    """One model call finished: its text and the tool calls it asked for (step mode stops here)."""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    type: str = field(default="model_done", init=False)


@dataclass
class ToolStart(Event):
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)
    type: str = field(default="tool_start", init=False)


@dataclass
class ToolProgress(Event):
    id: str = ""
    name: str = ""
    line: str = ""
    type: str = field(default="tool_progress", init=False)


@dataclass
class ToolEnd(Event):
    id: str = ""
    name: str = ""
    output: str = ""
    error: bool = False
    ms: int = 0
    skipped: bool = False
    type: str = field(default="tool_end", init=False)


@dataclass
class Compacted(Event):
    dropped: int = 0
    type: str = field(default="compacted", init=False)


@dataclass
class Done(Event):
    """The turn is over: Jade's final answer."""
    text: str = ""
    steps: int = 0
    type: str = field(default="done", init=False)


@dataclass
class Error(Event):
    message: str = ""
    type: str = field(default="error", init=False)
