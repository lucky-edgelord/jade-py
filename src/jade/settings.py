"""Settings: defaults, then ~/.config/jade/config.toml (or $JADE_CONFIG), then JADE_* environment variables,
then whatever the caller passes. The names follow the Jade Vault app where there is one."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_PATH = Path(os.environ.get("JADE_CONFIG", Path.home() / ".config" / "jade" / "config.toml"))

ProviderName = Literal["ollama", "ollama-cloud", "llamacpp", "openai", "groq", "openrouter", "lmstudio", "vllm", "deepseek", "mistral", "gemini", "together", "custom"]

# OpenAI-compatible endpoints by name (key comes from settings.api_key or the env var named here)
OPENAI_PRESETS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "llamacpp": ("http://localhost:8080/v1", ""),
    "lmstudio": ("http://localhost:1234/v1", ""),
    "vllm": ("http://localhost:8000/v1", ""),
    "custom": ("", "OPENAI_API_KEY"),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JADE_", extra="ignore", validate_assignment=True)

    # where things live
    vault: Path = Field(default=Path.home() / "JadeVault", description="the vault folder (same layout as the Jade Vault app)")
    data_dir: Path | None = Field(default=None, description="chats and caches; default <vault>/Jade/.data")

    # the model
    provider: ProviderName = "ollama"
    model: str = "qwen3:8b"
    host: str = Field(default="http://localhost:11434", description="Ollama address")
    base_url: str = Field(default="", description="OpenAI-compatible base URL (overrides the preset)")
    api_key: str = ""
    fallbacks: list[str] = Field(default_factory=list, description='other providers to try, e.g. ["groq:llama-3.3-70b-versatile"]')
    temperature: float = 0.2
    num_ctx: int = Field(default=16384, description="context window to plan for (and to ask Ollama for)")
    think: bool | None = Field(default=None, description="ask thinking models to think (None: model default)")
    keep_alive: str | None = None
    request_timeout: float = 600.0

    # the agent
    max_steps: int = Field(default=40, description="model calls per turn before Jade stops")
    tool_router: Literal["auto", "off"] = "auto"
    approve: Literal["auto", "risky", "all"] = Field(default="auto", description="ask before tools run: never, risky ones, all")
    bash_timeout: int = 60
    shell: str = "/bin/bash"
    max_tool_output: int = 50_000
    memory_lines: int = 60
    journal: bool = True

    # heartbeat: every hb_every minutes inside hb_start-hb_end (HH:MM, empty = always) Jade checks /Jade/HEARTBEAT.md
    hb_on: bool = False
    hb_every: int = 30
    hb_start: str = ""
    hb_end: str = ""
    hb_prompt: str = ""

    # curiosity: self-directed wake-ups at random times inside cu_from-cu_to (while `jade daemon` / `jade serve` runs)
    cu_on: bool = False
    cu_min: int = 45
    cu_max: int = 180
    cu_from: str = "08:00"
    cu_to: str = "22:00"
    cu_cap: int = 5
    cu_tools: Literal["notes", "web", "all"] = "web"
    cu_acts_off: list[str] = Field(default_factory=list)

    # scheduled backup to a Jade backup server (server/jade-backup-server.py in the app repo)
    ab_on: bool = False
    ab_url: str = ""
    ab_token: str = ""
    ab_hours: int = 24
    ab_kind: Literal["full", "config"] = "full"
    ab_keys: bool = False
    ab_encrypt: bool = False
    ab_pass: str = ""

    # web
    search: Literal["auto", "ollama", "searxng", "ddg", "wikipedia"] = "auto"
    searx_url: str = ""
    ollama_key: str = Field(default="", description="ollama.com API key (Ollama Cloud, web search and fetch)")
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) JadeVault/1.0 (+https://github.com/lucky-edgelord/jade-py)"

    # embeddings (memory and vault search by meaning)
    embed_on: bool = True
    embed_model: str = "nomic-embed-text"
    embed_host: str = ""

    # decisions (Ollaya)
    ollaya_host: str = "http://localhost:11435"
    ollaya_model: str = ""
    ollaya_key: str = ""

    # reasoning extras
    think_on: bool = True          # the think tool (tree of thoughts with verification)
    delegate_on: bool = True       # the delegate tool (a sub-agent with a clean context)

    # API server
    api_host: str = "127.0.0.1"
    api_port: int = 8760
    api_token: str = ""

    timezone: str = Field(default="", description="IANA name; default: the system's")
    secrets: dict[str, str] = Field(default_factory=dict, description="named secrets for http_request ({{NAME}}) and scripts (env)")

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None, **overrides: Any) -> "Settings":
        p = Path(path) if path else CONFIG_PATH
        data: dict[str, Any] = {}
        if p.is_file():
            data = tomllib.loads(p.read_text())
        env = cls()  # environment only
        merged = {**data, **{k: getattr(env, k) for k in env.model_fields_set}, **overrides}
        s = cls(**merged)
        s.vault = Path(os.path.expanduser(str(s.vault))).resolve()
        return s

    def save(self, path: Path | str | None = None) -> Path:
        p = Path(path) if path else CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        d = self.model_dump(mode="json", exclude_none=True)
        p.write_text(tomli_w.dumps(d))
        os.chmod(p, 0o600)
        return p

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else self.vault / "Jade" / ".data"

    def openai_base_and_key(self, provider: str | None = None) -> tuple[str, str]:
        name = provider or self.provider
        base, env = OPENAI_PRESETS.get(name, ("", "OPENAI_API_KEY"))
        if self.base_url and (provider is None or provider == self.provider):
            base = self.base_url
        key = self.api_key if (provider is None or provider == self.provider) and self.api_key else (os.environ.get(env, "") if env else "")
        return base.rstrip("/"), key
