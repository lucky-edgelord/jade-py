"""Optional parts of Jade register themselves here (tools, prompt lines, hooks). Each loads only if it can."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade")
PARTS: list[str] = ["embeddings", "journal", "scheduler", "playbook", "flows", "curiosity", "backup", "reasoning", "mcpx", "ssh"]


def load(jade: "Jade") -> None:
    import importlib
    for name in PARTS:
        try:
            importlib.import_module(f"jade.{name}").setup(jade)
        except Exception as e:  # an optional part must never stop Jade from starting
            log.warning("jade: %s is unavailable: %s", name, e)
