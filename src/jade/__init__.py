"""Jade, the agent from Jade Vault, in Python.

    from jade import Jade
    jade = Jade("~/JadeVault")
    print(jade.chat("What's in my inbox?"))
"""
from .core import Jade
from .events import Done, Error, Event, ModelDone, Text, Thinking, ToolEnd, ToolProgress, ToolStart
from .session import Session, Stepper
from .settings import Settings
from .tools import Tool, ToolContext, ToolError
from .vault import Vault

__version__ = "0.3.0"
__all__ = ["Jade", "Session", "Stepper", "Settings", "Vault", "Tool", "ToolContext", "ToolError",
           "Event", "Text", "Thinking", "ModelDone", "ToolStart", "ToolProgress", "ToolEnd", "Done", "Error", "__version__"]
