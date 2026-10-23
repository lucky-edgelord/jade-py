# Jade (Python)

Jade, the agent from [Jade Vault](https://github.com/lucky-edgelord/jade-vault), as a Python package. Same vault
layout as the app (notes with `[[links]]` and `#tags`, `/Jade/Memory.md`, `/Jade/SOUL.md`, `/AGENTS.md`, skills in
`/Jade/skills`), so the app and Python can work on one vault. Nothing Android-specific: the shell is the real one.

Use her four ways:

| | |
|---|---|
| **library** | `from jade import Jade` |
| **REPL** | `jade` (or `jade repl`) |
| **HTTP API** | `jade serve` (the app's `/v1` routes, plus POST, streaming and step mode) |
| **step by step** | `session.stepper(...)`, `/step` in the REPL, or `/v1/chats/{id}/step` |

## Install

```sh
pip install jade-vault                 # Python 3.11+
pip install "jade-vault[all]"          # + MCP, SSH, Discord, browser
jade init ~/JadeVault --model qwen3:8b # vault with the starter notes and skills, and ~/.config/jade/config.toml
jade                                   # chat
```

Models: Ollama (default, `host`), Ollama Cloud, llama.cpp, LM Studio, vLLM, OpenAI, Groq, OpenRouter, DeepSeek,
Mistral, Gemini, Together, or any OpenAI-compatible URL (`provider = "custom"`, `base_url`). `fallbacks` lists
others to try when the first cannot be reached, e.g. `["groq:llama-3.3-70b-versatile"]`.

## Library

```python
from jade import Jade

jade = Jade("~/JadeVault", model="qwen3:8b")
print(jade.chat("What's in my inbox?"))          # one turn, synchronous

s = jade.session()                               # a chat you keep (saved in <vault>/Jade/.data/chats)
answer = await s.ask("Turn each inbox line into its own note")

async for ev in s.run("Summarise [[Project Neon]]"):   # watch it work
    if ev.type == "text": print(ev.text, end="")
    elif ev.type == "tool_start": print(f"\n> {ev.name} {ev.arguments}")

@jade.tool(description="Current price of a stock.")
def price(symbol: str) -> str:
    return lookup(symbol)
```

### Step by step

```python
st = s.stepper("Clean up my daily notes")
evs = await st.step()            # a model call: what it said, and the tool calls it wants (st.pending)
st.edit(st.pending[0].id, {...}) # change a call's arguments
st.skip(reason="not that one")   # or skip it
await st.step()                  # run the next tool call; repeat until st.done; st.final is the answer
```

### Approvals

`approve = "auto"` (default) runs every tool; `"risky"` asks before the shell, HTTP calls and other risky tools;
`"all"` asks before every call. Give `jade.approver` a function `(call, tool) -> bool` (the REPL asks you).

## REPL

`jade` opens a chat with streaming replies and live tool output. `/help` lists the commands: `/new`, `/chats`,
`/open <id>`, `/step`, `/approve`, `/model`, `/tools`, `/tool <name> {json}`, `/memory`, `/context`, `/system`,
`/skill:<name>`. `jade ask "…"` runs one turn from the shell (`-q` prints only the answer, `--json` prints events).
`jade tool notes_search query=inbox` runs a tool yourself.

## HTTP API

`jade serve` listens on `127.0.0.1:8760`. Listening beyond this machine needs `api_token` (one is made per run if
missing). The app's routes work as they do in the app:

- `GET /v1/chat?message=…&session=<id>|new` → `{ok, reply, session, tool_calls, …}`
- `GET /v1/status`, `GET /v1/history?session=`, `GET /v1/chats`

Plus `POST /v1/chat {message, session, stream}` (Server-Sent Events with `stream: true`), `GET/DELETE /v1/chats/{id}`,
step mode (`POST /v1/chats/{id}/step`, `/skip`, `/edit`, `/cancel`, `GET /pending`), and `GET /v1/tools`,
`POST /v1/tools/{name}`. Docs at `/docs`.

## Tools

read, write, edit, bash (the real shell, in the vault folder), tasks, web_search, web_fetch, http_request (secrets
as `{{NAME}}` from `secrets` in the config), notes_search, note_links, memory, skill, find_skill, and more_tools
(the tool router sends optional tools only when a chat needs them, and keeps them once used so the model's prompt
cache keeps working).

Long chats are kept inside `num_ctx`: old tool outputs are shortened first, then the oldest part of the chat is
summarised.

## Configuration

`~/.config/jade/config.toml` (or `$JADE_CONFIG`), overridden by `JADE_*` environment variables and by keyword
arguments to `Jade(...)`. See `jade/settings.py` for every setting.

```toml
vault = "/home/me/JadeVault"
provider = "ollama"
model = "qwen3:8b"
host = "http://localhost:11434"
num_ctx = 16384
approve = "risky"
[secrets]
WEATHER_KEY = "…"
```

## Status

This is the first release: the core agent, tools, memory, skills, the REPL, the API and step mode. Coming next, in
this order: embeddings search (sqlite-vec), the journal, schedules, playbooks, flows, heartbeat and curiosity,
backups, think (tree of thoughts), delegate, decide (Ollaya), chat history search, then MCP, SSH, a headless
browser, Vector and Discord.

## Tests

`pip install -e ".[dev]" && pytest` (a fake model server; no model needed).
