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
`POST /v1/tools/{name}`, `GET /v1/schedules`, flows (`GET /v1/flows`, `GET /v1/flows/runs`,
`POST /v1/flows/{name}/run {input}`, `POST /v1/flows/runs/{id}/stop`) and `GET /v1/events` (notifications as
Server-Sent Events). Docs at `/docs`. `jade serve` also runs everything `jade daemon` runs.

## Automation

`jade daemon` (or `jade serve`) runs, in the foreground and systemd-friendly:

- **Schedules** (the `schedule` tool; `<data>/schedules.json`): a prompt, a shell command, a playbook or a flow, at
  times in plain English ("every weekday at 9am", "in 20 minutes") or cron. Output goes to `/Jade/Logs/cron.log`.
- **Heartbeat** (`hb_on`): every `hb_every` minutes inside `hb_start`-`hb_end`, Jade reads `/Jade/HEARTBEAT.md` and
  acts on it; `HEARTBEAT_OK` replies stay silent.
- **Playbooks** (`/Jade/Playbooks/*.json`, the app's format): deterministic automation shaped like Ansible, with no
  model. Approved once, then they run from schedules and flows.
- **Flows** (`/Jade/Flows/*.flow.json`, the app's format): n8n-style steps (tools, JavaScript, shell, AI answers,
  Jade agent turns, notes, playbooks, if / switch / each, wait, notify, other flows), started by hand, on a schedule
  or when a note or folder changes. Expressions and JavaScript steps run in real Node.js (`node` must be installed):
  `fs` is rooted at the vault and `jade.tool(...)`, `jade.ask(...)`, `jade.flow(...)` call back into Python.
  `jade flow list | run <name> [json] | runs`.
- **Curiosity** (`cu_on`): Jade wakes at random times inside `cu_from`-`cu_to` (at most `cu_cap` a day) and picks
  what to do by learning progress; the reward is measured from what changed in the vault. `cu_tools` limits her to
  notes, notes and web, or all tools. `/Jade/CURIOSITY.md` is where you tell her what to look into.
  `jade curiosity [--now]`.
- **Backups** (`ab_on`, `ab_url`, `ab_token`, every `ab_hours`): the app's backup format, gzip, optionally encrypted
  (`ab_encrypt`, `ab_pass`; AES-GCM, compatible with the app), to the Jade backup server. `jade backup-server --init`
  then `jade backup-server` runs that server; `jade backup now | list | save <file> | restore <file or name>`.
  Jade can check and start backups; only you restore.

Notifications go to every `jade.notifiers` callback (the API streams them on `/v1/events`, the daemon prints them)
and to `/Jade/Logs/notifications.log`.

## Tools

read, write, edit, bash (the real shell, in the vault folder), tasks, web_search, web_fetch, http_request (secrets
as `{{NAME}}` from `secrets` in the config), notes_search, note_links, memory, skill, find_skill, and more_tools
(the tool router sends optional tools only when a chat needs them, and keeps them once used so the model's prompt
cache keeps working).

Reasoning: **think** (a tree of thoughts where every line is checked by something that can say no: JavaScript run
in a read-only sandbox, a shell command, a file check, a rubric or a judge; the best verified line wins; trees and
episodes in `/Jade/Tot`), **delegate** (a sub-agent with a clean context and read-only tools unless `write: true`,
returning one short report), and **decide** (the local Ollaya decision model: calibrated probabilities for triage,
scores and yes/no; set `ollaya_host` and `ollaya_model`).

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

Done: the core agent, tools, memory, skills, the REPL, the API, step mode, search by meaning, the journal, chat
history, schedules, the heartbeat, playbooks, flows, curiosity, backups, think, delegate and decide. Coming next:
MCP, SSH, a headless browser (with playbook browser steps), Vector and Discord.

## Tests

`pip install -e ".[dev]" && pytest` (a fake model server; no model needed).
