# Jade (Python): the tutorial

A walk through every part of jade-py, from the first chat to phones, flows and fleets. Each section stands alone, so
read in order the first time and jump around after. Every Python example here was run against jade-py 0.11.0.

**Contents**

1. [Install and first run](#1-install-and-first-run)
2. [The vault](#2-the-vault)
3. [Choosing a model](#3-choosing-a-model)
4. [Using Jade from Python](#4-using-jade-from-python)
5. [Your own tools](#5-your-own-tools)
6. [Step mode and approvals](#6-step-mode-and-approvals)
7. [The terminal: REPL and CLI](#7-the-terminal-repl-and-cli)
8. [The HTTP API](#8-the-http-api)
9. [The dashboard and talk mode](#9-the-dashboard-and-talk-mode)
10. [Memory, soul and skills](#10-memory-soul-and-skills)
11. [The built-in tools](#11-the-built-in-tools)
12. [Thinking tools: think, delegate, decide](#12-thinking-tools-think-delegate-decide)
13. [The model router](#13-the-model-router)
    - [Trajectories and ratings](#trajectories-and-ratings-fine-tuning-data)
14. [Automation: schedules, heartbeat, playbooks, flows, curiosity](#14-automation)
15. [Backups](#15-backups)
16. [MCP servers, SSH and the browser](#16-mcp-servers-ssh-and-the-browser)
17. [Models and USB on this machine](#17-models-and-usb-on-this-machine)
18. [Phones and tablets: Jade Link and Sentry](#18-phones-and-tablets-jade-link-and-sentry)
19. [Discord and Vector](#19-discord-and-vector)
20. [Jade Fleet](#20-jade-fleet)
21. [Configuration reference](#21-configuration-reference)
22. [When something goes wrong](#22-when-something-goes-wrong)

---

## 1. Install and first run

You need Python 3.11 or newer and a model: [Ollama](https://ollama.com) on this machine is the default, or any
hosted provider (section 3).

```sh
pip install jade-vault                 # the package is called jade-vault; you import it as `jade`
pip install "jade-vault[all]"          # everything: MCP, SSH, Discord, browser, ONNX / LiteRT, USB
```

Until it is on PyPI, install it from GitHub:

```sh
pip install "jade-vault[all] @ git+https://github.com/lucky-edgelord/jade-py"
```

Pick only the extras you want: `[ml]` (ONNX Runtime), `[litert]`, `[usb]`. The browser tool also needs
`playwright install chromium`.

Make a vault and a config file, pull a model, and talk to her:

```sh
ollama pull qwen3:8b
jade init ~/JadeVault --model qwen3:8b   # starter notes and skills + ~/.config/jade/config.toml
jade                                     # chat (the REPL)
```

Jade has five faces, all on the same vault and the same brain:

| | |
|---|---|
| library | `from jade import Jade` |
| REPL | `jade` |
| HTTP API | `jade serve` |
| step by step | `session.stepper(...)`, `/step`, `/v1/chats/{id}/step` |
| dashboard | `jade serve`, then `http://localhost:8760/` |

## 2. The vault

The vault is a folder of markdown notes, the same layout as the Jade Vault app, so the app and Python can work on
one vault. Inside it Jade keeps her own files under `/Jade`:

| Path | What it is |
|---|---|
| `[[links]]` and `#tags` | the notes' own links and tags, indexed for search and backlinks |
| `/Jade/Memory.md` | her long-term memory (she sees it every chat) |
| `/Jade/SOUL.md` | who she is: tone, values, how she works with you |
| `/AGENTS.md` | standing instructions for this vault |
| `/Jade/skills/<name>/SKILL.md` | instruction packs she can load |
| `/Jade/HEARTBEAT.md` | the checklist she reads on every heartbeat |
| `/Jade/Flows`, `/Jade/Playbooks` | automations |
| `/Jade/mcp.json` | MCP servers |
| `/Jade/Logs` | `cron.log`, `notifications.log` |
| `/Jade/.data` | chats and caches (not notes) |

You can use the vault from Python without any model:

```python
from jade import Jade

jade = Jade("~/JadeVault")
v = jade.vault
v.write("/Notes/Idea.md", "# Idea\nSee [[Welcome]] #spark")
print(v.read("/Notes/Idea.md"))
print(v.search("spark"))            # ranked hits: [{"path", "score", "snippet"}]
print(v.links("/Notes/Idea.md"))    # {"outgoing": […], "backlinks": […], "unresolved": […], "tags": […]}
print(v.list("/Notes"), v.notes()[:5])
v.append("/Inbox.md", "- call the dentist\n")
```

## 3. Choosing a model

Edit `~/.config/jade/config.toml` (or set `JADE_*` environment variables, or pass keywords to `Jade(...)`):

```toml
provider = "ollama"          # ollama, ollama-cloud, llamacpp, lmstudio, vllm, openai, groq, openrouter,
model = "qwen3:8b"          # deepseek, mistral, gemini, together, or custom
host = "http://localhost:11434"
num_ctx = 16384              # the context window she plans for
fallbacks = ["groq:llama-3.3-70b-versatile"]   # tried in order when the first cannot be reached
```

Hosted providers take their key from `api_key` or the usual environment variable (`GROQ_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`…). Any OpenAI-compatible server works:

```toml
provider = "custom"
base_url = "http://myserver:8000/v1"
api_key = "…"
```

From Python, the same settings are keywords: `Jade("~/JadeVault", provider="groq", model="llama-3.3-70b-versatile")`.
Long chats stay inside `num_ctx`: old tool outputs are shortened first, then the oldest part of the chat is
summarised.

## 4. Using Jade from Python

**One turn, synchronously** (scripts, notebooks):

```python
from jade import Jade

jade = Jade("~/JadeVault", model="qwen3:8b")
print(jade.chat("What's in my inbox?"))
```

`jade.chat` starts a new chat each time. Inside async code use `await jade.achat(...)` (calling `chat` in a running
event loop raises an error that says so).

**A chat you keep.** A session holds the messages, tasks and summary, and is saved to `<vault>/Jade/.data/chats`:

```python
s = jade.session()                       # new chat
a = await s.ask("Turn each inbox line into its own note")
b = await s.ask("Now link them from [[Project Neon]]")   # same chat, remembers the first

jade.chats()                             # saved chats: id, title, updated…
s2 = jade.session(s.id)                  # reopen one later
```

**Watching her work.** `run` streams events; every event has a `.type`:

```python
async for ev in s.run("Summarise [[Project Neon]]"):
    if ev.type == "text":        print(ev.text, end="")
    elif ev.type == "thinking":  pass                       # a thinking model's reasoning
    elif ev.type == "tool_start":    print(f"\n> {ev.name} {ev.arguments}")
    elif ev.type == "tool_progress": print("  ", ev.line)
    elif ev.type == "tool_end":      print(f"  {'✗' if ev.error else '✓'} {ev.ms} ms")
    elif ev.type == "routed":    print(f"[model: {ev.provider}:{ev.model}]")   # section 13
    elif ev.type == "done":      print("\nanswer:", ev.text)
```

The events: `text`, `thinking`, `model_done`, `tool_start`, `tool_progress`, `tool_end`, `compacted`, `routed`,
`error`, `done`. `ask` is just `run` that returns the final answer (and raises if there was only an error). Pass
pictures with `images=[base64, …]`.

**Run a tool yourself**, no model involved:

```python
print(await jade.run_tool("notes_search", {"query": "#project"}))
print(await jade.run_tool("memory", {"action": "save", "text": "Prefers tea"}))
```

**Notifications.** Schedules, the heartbeat and tools call `jade.notify(title, body)`. Subscribe to them:

```python
jade.notifiers.append(lambda title, body: print("NOTIFY:", title, body))
```

**Settings** are a typed object: `jade.settings.model`, `jade.settings.approve = "risky"`, and so on.

## 5. Your own tools

Decorate a function. The signature becomes the tool's parameters and the docstring (or `description=`) tells the model
when to use it. Async functions work too:

```python
@jade.tool(description="Current price of a stock.")
def price(symbol: str) -> str:
    return f"{symbol}: 42.0"

@jade.tool()
async def add(a: int, b: int = 1) -> str:
    "Add two numbers."
    return str(a + b)

await jade.session().ask("What is ACME trading at?")   # she calls price(symbol="ACME")
```

A tool returns text (that is what the model reads). Raise an exception to report an error. Tools you add are always
available; the built-in optional ones are loaded by the tool router when a chat needs them.

## 6. Step mode and approvals

**Approvals.** `approve` decides when a human is asked:

| `approve` | |
|---|---|
| `"auto"` (default) | every tool runs |
| `"risky"` | ask before the shell, HTTP calls, SSH, USB and other risky tools |
| `"all"` | ask before every call |

Give Jade an approver: a function `(call, tool) -> bool` (or an async one). With none set, asked-for calls are
refused.

```python
jade.settings.approve = "risky"
jade.approver = lambda call, tool: input(f"Run {call.name} {call.arguments}? [y/N] ").lower() == "y"
```

The REPL asks you itself; over HTTP and in the dashboard, pending approvals pop up (section 8).

**Step mode** runs one move at a time so you can look at, change or skip each tool call:

```python
st = jade.session().stepper("Clean up my daily notes")
await st.step()                          # one model call; her tool requests are now in st.pending
print([c.name for c in st.pending])
st.edit(st.pending[0].id, {"command": "ls /daily"})   # change a call's arguments…
st.skip(reason="not that one")           # …or skip the next one
while not st.done:
    await st.step()                      # runs the next tool call, or the next model call
print(st.final)
```

`step()` returns that move's events; `stream()` yields them as they happen.

## 7. The terminal: REPL and CLI

`jade` (or `jade repl`) opens a chat with streaming replies and live tool output. Useful commands inside:

| | |
|---|---|
| `/help` | all commands |
| `/new`, `/chats`, `/open <id>` | start, list, switch chats |
| `/step` | toggle step mode |
| `/approve` | change who approves what |
| `/model` | see or change the model |
| `/tools`, `/tool <name> {json}` | list tools, run one |
| `/memory`, `/context`, `/system` | see her memory, what fills the context, the system prompt |
| `/skill:<name>` | run a skill |

One-shot commands from your shell:

```sh
jade ask "Summarise my week"              # one turn; -q prints only the answer, --json prints events
echo "tidy my inbox" | jade ask -         # read the message from stdin
jade ask -c <chat-id> "and the next week"  # continue a saved chat
jade tools                                # list tools
jade tool notes_search query=inbox        # run a tool yourself
jade chats                                # saved chats
jade flow list                            # also: jade flow run <name> [json], jade flow runs
jade curiosity [--now]                    # status, or wake her now
jade backup now | list | save <file> | restore <file>
jade serve | jade daemon | jade backup-server
```

Global options go before the command: `jade -v ~/OtherVault -m llama3.2 ask "hi"`.

## 8. The HTTP API

```sh
jade serve                  # 127.0.0.1:8760
```

On this machine no token is needed. To listen on the network set `api_host = "0.0.0.0"`; a token is then required
(`api_token` in the config, or a fresh one is printed for the run). Send it as `Authorization: Bearer <token>`.
Interactive docs live at `/docs`.

```sh
TOKEN=jv_…
# one turn (the Jade Vault app's route)
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8760/v1/chat?message=What%27s%20in%20my%20inbox&session=new"

# a streaming turn (Server-Sent Events)
curl -N -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message": "Plan my day", "session": "new", "stream": true}' http://localhost:8760/v1/chat

# run a tool
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"query": "inbox"}' http://localhost:8760/v1/tools/notes_search
```

Routes at a glance:

| | |
|---|---|
| chat | `GET/POST /v1/chat`, `GET /v1/chats`, `GET/DELETE /v1/chats/{id}`, `GET /v1/history` |
| step mode | `POST /v1/chats/{id}/step`, `/skip`, `/edit`, `/cancel`; `GET /v1/chats/{id}/pending` |
| tools | `GET /v1/tools`, `POST /v1/tools/{name}` |
| approvals | `GET /v1/approvals`, `POST /v1/approvals/{id} {"allow": true}` |
| events | `GET /v1/events` (notifications, approvals, Sentry fires as Server-Sent Events) |
| automation | `GET /v1/schedules`, `GET /v1/flows`, `POST /v1/flows/{name}/run {"input": …}`, `GET /v1/flows/runs`, `POST /v1/flows/runs/{id}/stop` |
| phones | `GET /v1/phones`, `POST /v1/phones/{name}/call {"op": …, "args": …}`, `/v1/link/*`, `GET /v1/sentry` |
| the dashboard | `/v1/overview`, `/v1/vault/*`, `GET/PATCH /v1/settings`, `/v1/logs`, `POST /v1/transcribe` |

`jade serve` also runs everything `jade daemon` runs (schedules, heartbeat, curiosity, backups, the bridges).

## 9. The dashboard and talk mode

Open `http://<machine>:8760/` after `jade serve` and sign in with the API token. You get: chat with streaming and tool
cards, her animated avatar, the vault (browse, search, edit, preview), flows, schedules, Sentry, phones, logs,
approvals (a call waiting for your yes pops up), and every setting grouped and editable. On a phone it is laid out
like the app: one screen at a time, tabs along the bottom.

**Talk mode** (the Talk button) is hands-free: she listens, answers out loud, and listens again, with only her avatar
and captions on screen. An optional wake phrase (such as "hey jade") keeps her quiet until addressed. Back, ✕ or a
double tap leaves it. Hearing comes from the phone's recognizer (Jade Link), the browser's (Chrome or Edge), or
jade-py's own transcription model anywhere else:

```toml
stt_provider = "groq"
stt_model = "whisper-large-v3-turbo"
```

then switch on "Use jade-py to hear" in the dashboard's settings. Her voice is the device's text-to-speech.

## 10. Memory, soul and skills

**Memory.** `/Jade/Memory.md` is her long-term memory and is in her instructions every chat. She saves to it with the
`memory` tool when you tell her something worth keeping ("remember that I prefer tea"), and can search it and her
journal of past chats. Edit the note by hand any time; it is just markdown. `memory_lines` limits how much goes in
the prompt. With `journal = true` (default) she writes a journal entry when a chat closes, so she can recall it later.

**Soul.** `/Jade/SOUL.md` is her character and house rules; `/AGENTS.md` is standing instructions for the vault (naming
conventions, folders). Change either to change how she behaves; they apply from the next chat.

**Skills.** A skill is a folder with a `SKILL.md`: a name and description in front matter, then numbered steps.

```markdown
---
name: weekly-review
description: Run the weekly review. Use when the user asks for a weekly review or "what happened this week".
---
# Weekly review
1. Read the daily notes of the last 7 days in /daily.
2. List what was finished, what slipped, and what is still open.
3. Write the result to /reviews/YYYY-WW.md and link it from [[Inbox]].
```

Save it as `/Jade/skills/weekly-review/SKILL.md`. Her instructions list every skill's description; she loads one with
the `skill` tool when it fits (`find_skill` searches when there are many). You can also run one yourself with
`/skill:weekly-review` in the REPL. The starter vault ships `atomic-notes`, `daily-review`, `literature-note`,
`math-notes` and `skill-creator` (ask her to create new skills for you).

## 11. The built-in tools

Always available: `read`, `write`, `edit` (notes and files), `bash` (the real shell, in the vault folder),
`tasks` (a to-do list for the current job), `web_search`, `web_fetch`, `http_request`, `notes_search`, `note_links`,
`memory`, `skill`, `find_skill`, `more_tools`.

The tool router sends the optional tools (`browser`, `schedule`, `flow`, `playbook`, `think`, `delegate`, `decide`,
`mcp`, `onnx`, `litert`, `usb`, `phone`, `screen`, `senses`, `look`, `sentry`, `backup`, `curiosity`, `chat_history`)
only when a chat needs them, and keeps them once used so the model's prompt cache keeps working. `tool_router = "off"`
sends everything every time.

Things worth knowing:

- **`web_search`** uses the search provider in your settings (`search`, `searx_url`, `ollama_key`). Several
  `web_search`/`web_fetch` calls in one round run at the same time.
- **`http_request`** can use secrets without the model seeing them: put `[secrets] WEATHER_KEY = "…"` in the config
  and write `{{WEATHER_KEY}}` in the request.
- **`bash`** is your real shell (`shell`, `bash_timeout` in the config). Use `approve = "risky"` if you want to
  see each command first.
- Tool output is cut at `max_tool_output` characters.

List them all with `jade tools`; run any with `jade tool <name> key=value`.

## 12. Thinking tools: think, delegate, decide

**think** is a tree of thoughts where every line of reasoning is checked by something that can say no: JavaScript run
in a read-only sandbox, a shell command, a file check, a rubric, or a judge. The best verified line wins. Trees and
episodes are kept in `/Jade/Tot`. Ask her to "think this through carefully and check it" for problems that have a
checkable answer. Turn it off with `think_on = false`.

**delegate** starts a sub-agent with a clean context and read-only tools (unless `write: true`) and gets back one
short report. It keeps big research out of the main chat. Turn it off with `delegate_on = false`.

**decide** asks a small local *decision model* for calibrated probabilities instead of prose: triage, scores, yes/no.
Run [Ollaya](https://ollaya.dev) (`ollaya serve`, `ollaya pull laya`) or Ollama 0.35+, then:

```toml
ollaya_model = "laya"
decide_provider = "ollaya"            # or "ollama"
ollaya_host = "http://localhost:11435"   # for "ollama": decide_ollama_host, else host
```

```python
await jade.run_tool("decide", {"state": "Server is on fire", "question": "Is this urgent?", "type": "yesno"})
await jade.run_tool("decide", {"state": "…", "question": "Which folder?", "type": "choice", "options": ["Work", "Home"]})
await jade.run_tool("decide", {"state": "…", "question": "How polite is this?", "type": "score",
                               "options": ["rude", "neutral", "very polite"]})   # lowest first
```

You get the answer, a confidence, and the probability of every option. Flows and scripts use the same call.

## 13. The model router

Different messages need different models: a small fast one for "what's the weather", a strong one for a refactor.
With the router on, the decision model (laya) reads each message you send and picks which of your listed models
answers it:

```toml
router_on = true
router_models = [
  {model = "ollama:qwen3:8b",          note = "quick everyday chat, short answers, simple lookups"},
  {model = "groq:openai/gpt-oss-120b", note = "coding, long documents, hard multi-step reasoning"},
]
# router_decider = "laya"        # default: ollaya_model
# router_min_confidence = 0.6    # less sure than this: the main model answers
```

- `model` is `provider:model` (the provider's key comes from its usual environment variable) or a bare name for the
  main provider.
- The pick holds for the whole turn, tool rounds included. The turn's first event is `routed` (provider, model, note,
  confidence). The REPL and dashboard show which model answered.
- If laya cannot be reached, is unsure, or the picked model fails, the main model answers.
- Only chats you start are routed. The heartbeat, schedules, flows, curiosity and messenger bridges keep the main
  model (`session.route = False` does the same for your own sessions).

It uses the same decision server as `decide`, so a Jade Fleet broker works too.

### Trajectories and ratings (fine-tuning data)

Every run is logged exactly as the model saw it, in the Jade Vault app's format, so you can fine-tune a model on
your own conversations. Rate replies to say which ones were good:

| Where | 👍 / 👎 | New answer to the same message |
|---|---|---|
| dashboard | the buttons under a reply | ↻ |
| REPL | `/rate up`, `/rate down`, `/rate clear` | `/retry` |
| Python | `traj.rate_message(s, None, 1)` | `async for ev in s.retry(): …` |
| HTTP | `POST /v1/chats/{id}/rate {"rating": 1}` | `POST /v1/chats/{id}/retry` |

```python
from jade import traj

s = jade.session()
await s.ask("Write a haiku about rain")
traj.rate_message(s, None, -1)              # 👎 the last reply
async for ev in s.retry(): pass             # a new answer to the same message
traj.rate_message(s, None, 1)               # 👍 the better one: a preference pair

text = jade.traj.export(format="openai", filter="prefs", meta=True)   # JSONL
print(jade.traj.stats())                    # runs, steps, good, bad, errors, bytes
```

From the shell:

```sh
jade traj stats
jade traj export -o sft.jsonl                            # supervised fine-tuning: finished runs minus 👎
jade traj export --filter prefs --meta -o prefs.jsonl    # DPO / KTO: 👎 runs too, with ratings
jade traj export --format sharegpt --thinking            # LLaMA-Factory, with the model's thinking
```

Filters: `clean` (finished runs minus 👎, the default), `good` (only 👍), `prefs` (finished runs, 👎 too), `all`
(errors too). `--meta` adds a `metadata` field to each line: `id`, `started`, `model`, `provider`, `source` (chat,
heartbeat, schedule, curiosity), `outcome`, `rating` and per-call token counts in `steps`. The settings
`traj_format`, `traj_filter`, `traj_thinking` and `traj_meta` set the defaults (also on the dashboard's Settings →
Trajectory log, which has an Export button); `traj_on = false` stops logging. Runs are kept in
`<data>/trajectories`, one JSON file each, and never leave this machine unless you export them.

### The knowledge graph (verification with proofs)

The `knowledge` tool keeps facts and rules in the vault and reasons over them with the same Rust engine as the app.

```text
# /Jade/Knowledge/rules.dl
above(X, Y) :- reports_to(X, Y).
above(X, Z) :- reports_to(X, Y), above(Y, Z).
! one_employer: works_at(P, A), works_at(P, B), A != B.
```

```bash
jade kg add "reports_to(bob, alice)"
jade kg add "reports_to(carol, bob)"
jade kg verify "above(carol, alice)"      # ✓ PROVEN, with the proof; exit code 0
jade kg verify "above(alice, carol)"      # ? UNKNOWN: missing is not false; exit code 2
jade kg query "above(carol, X)"           # X = bob, X = alice
jade kg check                             # every constraint the facts break
```

From Python, `await jade.run_tool("knowledge", {"action": "verify", "claim": "above(carol, alice)"})`; over HTTP,
`POST /v1/knowledge/verify {"claim": ...}`. When Jade adds facts (from a note, or something you said) they are
*proposed*: confirm them with `jade kg confirm --all` or in the dashboard (Settings → Knowledge graph). A proof that
rests on unconfirmed facts says so. Turn on `kg_verify` and Jade checks her factual claims against the graph and
marks them ✓, ✗ or ?.

## 14. Automation

Run `jade daemon` (a foreground process, systemd-friendly) or `jade serve` (which includes it) to let these run.

**Schedules.** Ask her ("every weekday at 9am, summarise my inbox") and she uses the `schedule` tool; or use it
yourself. A schedule sends a prompt to her, runs a shell command (output in `/Jade/Logs/cron.log`), a playbook or a
flow. Times are plain English ("every weekday at 9am", "in 20 minutes") or cron. They live in
`<data>/schedules.json`; pause, resume or delete them through the tool or the dashboard.

**Heartbeat.** Switch on `hb_on = true` and every `hb_every` minutes inside `hb_start`–`hb_end` she reads
`/Jade/HEARTBEAT.md` and acts on it. If nothing needs attention she answers `HEARTBEAT_OK` and you hear nothing;
otherwise you get a notification and the details land in the "♥ Heartbeat" chat. While the file has only comments and
empty boxes, heartbeats are skipped for free. Example contents:

```markdown
- If /Inbox.md has more than 5 items, remind me to sort it.
- After 17:00, list unchecked tasks in today's daily note.
- Notes tagged #waiting older than a week: tell me about them.
```

**Playbooks** (`/Jade/Playbooks/*.json`) are deterministic automation shaped like Ansible, with no model involved:
approved once, then they run from schedules and flows. They also run `browser.*` steps inside the playbook's
`allow_hosts`. Ask her to write one (the `playbook` tool's `help` action prints the format); the dashboard has them too.

**Flows** (`/Jade/Flows/*.flow.json`) are n8n-style: steps run in order. Step types include her tools, JavaScript, shell
commands, decisions, AI answers, a full Jade chat turn, notes, playbooks, `if` / `switch` / `each`, wait, notify, and
other flows. A flow starts by hand, on a schedule, or when a note or folder changes. Expressions and JavaScript steps
run in real Node.js (`node` must be installed); `fs` is rooted at the vault, and `jade.tool(...)`, `jade.ask(...)`,
`jade.flow(...)` call back into Python.

```sh
jade flow list
jade flow run triage '{"text": "invoice overdue"}'
jade flow runs
```

The easiest way to make one is to describe it to her; she reads the `flow` tool's `help` for the exact format first.
The dashboard's Flows page lists them and their runs. Over HTTP: `POST /v1/flows/{name}/run`.

**Curiosity.** With `cu_on = true` she wakes at random times inside `cu_from`–`cu_to` (at most `cu_cap` a day), picks
something to do by *learning progress*, and measures her reward from what changed in the vault. `cu_tools` limits her
to `notes`, `web` (notes and web) or `all` tools. `/Jade/CURIOSITY.md` is where you tell her what to look into.
`jade curiosity` shows the status; `jade curiosity --now` wakes her now.

## 15. Backups

The app's backup format (gzip, optionally AES-GCM encrypted, compatible with the app) to a Jade backup server:

```sh
jade backup-server --init       # once: makes a token
jade backup-server              # run the server
```

```toml
ab_on = true
ab_url = "http://backuphost:8800"
ab_token = "…"
ab_hours = 6
ab_encrypt = true
ab_pass = "a long passphrase"
```

`jade backup now | list | save <file> | restore <file or name>`. She can check and start backups; only you restore.
API keys and secrets (`api_key`, `ab_token`, `fleet_token`, `secrets`, `ssh_hosts`, messenger tokens…) are left out of
backups. `ab_keys = true` includes them, and then the backup is always encrypted. `ab_kind = "config"` backs up only
her soul, memory, skills and settings instead of the whole vault.

## 16. MCP servers, SSH and the browser

**MCP.** Put servers in `/Jade/mcp.json` (the app's format, `url` + `headers`; here also `command` + `args` for a local
server) or let her add them with the `mcp` tool:

```json
{ "mcpServers": {
    "files": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"] },
    "notes": { "url": "https://example.com/mcp", "headers": { "Authorization": "Bearer ${NOTES_TOKEN}" } } } }
```

Each tool appears as `mcp__<server>__<tool>`. How it is offered is per server: `direct` (always), `deferred` (loaded by
the tool router when needed), `codemode` (the default: called from JavaScript with
`await tools.mcp__files__read(args)` in the codemode tool, which keeps the prompt small) or `hidden`. Headers can use
`${SECRET}` or `!command`.

**SSH.** List machines in the config:

```toml
[[ssh_hosts]]
name = "nas"
host = "192.168.1.5"
user = "me"
key_file = "~/.ssh/id_ed25519"      # or password = "…", or nothing for the SSH agent
confirm = true                      # ask before each command (default)
```

She gets interactive sessions, one-shot commands and file transfer. Passwords and keys never reach the model, host
keys are pinned the first time, and every command asks first unless the host has `confirm = false`.

**Browser.** Her own headless Chromium (Playwright: `playwright install chromium`, or `browser_path`). The tool opens
pages, reads them, clicks, types, selects, scrolls, evaluates JavaScript and takes screenshots. Cookies and logins are
kept in `<data>/browser`, so sign in once (`browser_headless = false` to see it) and she stays signed in.

## 17. Models and USB on this machine

**ONNX and LiteRT.** The `onnx` tool runs any `.onnx` model with ONNX Runtime, on the GPU when there is one (CUDA,
TensorRT, ROCm, DirectML, CoreML; `onnx_providers` sets the order) else the CPU. The `litert` tool runs `.tflite`
models. Install with `pip install "jade-vault[ml]"` (and `[litert]`). Both work the same way:

- `load` a model from the vault or a URL (saved to `/models`; downloading the same model again reuses the file);
- `run` it with numbers, nested lists, vault `.json`/`.f32` files, or pictures (`{image: "photos/cat.jpg"}`, resized
  and normalised for the model);
- each output comes back summarised with the top 5 (and labels from a file); big outputs are saved to `/models/out`.

A model file is checked first: an error page, a JSON error or a git-LFS pointer saved under a model's name is named
for what it is, and a download that comes back as one is not saved.

**USB.** The `usb` tool (PyUSB and libusb: `pip install "jade-vault[usb]"`) talks to devices plugged into this
machine: list, open/close, claim/release, control and bulk/interrupt transfers in hex or text, saving what comes back.
`usb_allow = ["2341:0043", "2341"]` (vendor:product or vendor, in hex) limits which devices she may open; she cannot
change it. On Linux give your user access with a udev rule such as
`SUBSYSTEM=="usb", ATTR{idVendor}=="2341", MODE="0660", GROUP="plugdev"`.

## 18. Phones and tablets: Jade Link and Sentry

Install **Jade Link** (`dist/jade-link-<version>.apk`, built by `android/build-apk.sh`; see `android/README.md`) on each
phone or tablet. Several can be linked at once and she can use one, several or all of them together. Each connects
whichever way suits it:

- **It calls in** (works anywhere it can reach jade-py, e.g. over Tailscale): in Jade Link enter jade-py's address
  (`http://192.168.1.10:8760`) and the API token, and switch on Connect. `jade serve` must listen beyond this machine
  (`api_host = "0.0.0.0"`).
- **jade-py calls it** (same network or VPN): switch on Listen in Jade Link and add what it shows to the config:

```toml
[[phones]]
name = "tablet"
url = "http://192.168.1.20:8765"
token = "jl_…"
```

The tools each take `phone`: a name, several names separated by commas, or `all` (which run at the same time):

| Tool | What it does |
|---|---|
| `phone` | alarms, timers, ringing, sounds and radio streams, volume, calls, texts, contacts, call log, clipboard, links, apps, brightness, keep awake, Bluetooth, media keys, notifications, casting to a Chromecast or Google TV |
| `screen` | drives other apps like a screen reader (switch on Jade Link in Android's Accessibility settings) |
| `senses` | camera photos (she sees them), the microphone (recordings, or just how loud), location, sensors, battery, Wi-Fi, torch, vibration |
| `look` | pictures and video frames from the vault |
| `sentry` | rules that make devices react on their own |

Calls and texts always ask first: `phone_confirm = "phone"` (the person holding the phone), `"jade"` (jade-py's approver:
dashboard or REPL) or `"off"`.

**Sentry** is the app's rule engine, kept in `/Jade/Config/Sentry.json` (edit it on the dashboard's Sentry page or ask
her): rules on sensors, camera and microphone, calls, texts, notifications, location, Wi-Fi, battery… A rule can buzz
or notify on the device at once and run a flow here. Sentry runs on the devices in its `phones` list (default: all);
a rule can name its own `phones`, and `devices` switches systems per device. Flows that Sentry runs may text or call
only numbers in `sentry_allow`, at most `sentry_limit` an hour; she cannot change that list.

**Kiosk mode** (in Jade Link) makes the device's home screen full screen and pinned in front, for a tablet on the
wall. Made device owner once (`adb shell dpm set-device-owner app.netrunner.jadelink/app.netrunner.jade.AdminReceiver`)
it is a real lock. With "Start talk mode when it opens", the device is simply Jade, listening.

## 19. Discord and Vector

Jade can answer as a bot through the bridges the app uses (`jade-discord` and `jade-vector` in the Jade Vault
repository; each is an MCP server with a token):

```toml
[discord]
on = true
url = "http://127.0.0.1:8791/mcp"
token = "…"
owner = "123456789012345678"      # your user id (an npub for [vector]): you get every tool
admins = []                       # Discord powers, no vault or shell
channels = ["#general"]           # group chats she answers in (when addressed, or trigger = "all")
dm = "anyone"                     # or "known", "owner"
notify = "inbox"                  # notify_owner: "inbox", "urgent" or "now"
```

Each chat gets its own session. People who are not the owner get a short, private prompt (no notes, no memory) and
only the messenger tool, web search and `notify_owner`, which files their message in `/Jade/Discord Inbox.md` (or
`/Jade/Vector Inbox.md`) and, per `notify`, tells you at once. The bridges are polled while `jade daemon` or
`jade serve` is running.

## 20. Jade Fleet

A [jade-fleet](https://github.com/lucky-edgelord/jade-fleet) broker holds a household's shared vault, its meaning-based
memory search and the way to its models (chat on llama-server, embeddings and decisions on Ollama / Ollaya, vision on
DLA cores). jade-py joins it as another member:

```sh
jade init ~/JadeVault --fleet http://optiplex:8765 --fleet-token TOKEN --device lab-pc
```

This sets `fleet_url`, `fleet_token`, `fleet_device`, and points chat, embeddings and decisions at the broker. The
token goes only to the broker. Two tools come with it:

- **`vault_search`**: meaning-based search over every device's notes and memories, using the broker's index.
- **`vision`**: `detect` (YOLO) or `classify` (MobileNet) on a picture in the vault.

To work on the *shared* vault itself, run jade-py on the broker's machine with `vault` set to the broker's vault
folder; elsewhere her vault is her own folder.

## 21. Configuration reference

Order of precedence: keywords to `Jade(...)` > `JADE_*` environment variables > `~/.config/jade/config.toml`
(or `$JADE_CONFIG`) > defaults. Nested settings use `__` in the environment (`JADE_DISCORD__ON=true`). Everything is
in `jade/settings.py`; the dashboard's Settings page edits it live. Highlights:

| Group | Settings |
|---|---|
| Model | `provider`, `model`, `host`, `base_url`, `api_key`, `fallbacks`, `temperature`, `num_ctx`, `think`, `keep_alive`, `request_timeout` |
| Agent | `approve`, `max_steps`, `tool_router`, `bash_timeout`, `shell`, `max_tool_output`, `memory_lines`, `journal`, `think_on`, `delegate_on`, `timezone` |
| Router | `router_on`, `router_models`, `router_decider`, `router_min_confidence` |
| Decisions | `decide_provider`, `ollaya_host`, `ollaya_model`, `ollaya_key`, `decide_ollama_host` |
| Embeddings | `embed_on`, `embed_model`, `embed_host` (search by meaning) |
| Heartbeat, curiosity | `hb_on`, `hb_every`, `hb_start`, `hb_end`, `hb_prompt`; `cu_on`, `cu_from`, `cu_to`, `cu_cap`, `cu_tools` |
| Backups | `ab_on`, `ab_url`, `ab_token`, `ab_hours`, `ab_kind`, `ab_keys`, `ab_encrypt`, `ab_pass` |
| Web | `search`, `searx_url`, `ollama_key`, `user_agent`, `browser_on`, `browser_path`, `browser_headless` |
| On-device | `onnx_on`, `onnx_providers`, `litert_on`, `usb_on`, `usb_allow` |
| Phones | `phones`, `link_on`, `phone_default`, `phone_confirm`, `sentry_allow`, `sentry_limit`, `sentry_phone`, `look_images` |
| Speech | `stt_provider`, `stt_model` |
| Fleet | `fleet_url`, `fleet_token`, `fleet_device` |
| Messengers, machines | `vector`, `discord`, `ssh_hosts`, `secrets` |
| Server | `vault`, `data_dir`, `api_host`, `api_port`, `api_token` |

A complete small config:

```toml
vault = "/home/me/JadeVault"
provider = "ollama"
model = "qwen3:8b"
num_ctx = 16384
approve = "risky"
fallbacks = ["groq:llama-3.3-70b-versatile"]

hb_on = true
hb_every = 30

ollaya_model = "laya"
router_on = true
router_models = [
  {model = "qwen3:8b", note = "quick everyday chat"},
  {model = "groq:openai/gpt-oss-120b", note = "coding and hard reasoning"},
]

[secrets]
WEATHER_KEY = "…"
```

The config file is written with mode 0600 because it holds keys.

## 22. When something goes wrong

| Symptom | Try |
|---|---|
| "no model provider answered" | Is Ollama running (`ollama list`)? Is `host` right? Add a `fallbacks` entry. |
| The model "does not support tools" | Use a tool-capable model (qwen3, gpt-oss, llama3.1, mistral-small). |
| `decide` says it could not reach Ollaya | Start `ollaya serve`; for Ollama you need 0.35+. Set `ollaya_model`. |
| Router shows "main model (…)" | The reason is in the tag: laya unreachable, unsure (`router_min_confidence`), or fewer than two `router_models`. |
| Flows with JavaScript fail | `node` must be installed and on `PATH`. |
| Browser tool fails | `playwright install chromium`, or set `browser_path`. |
| A phone cannot connect | `api_host = "0.0.0.0"`, the right token, same network or Tailscale; check `GET /v1/phones`. |
| A tool call never runs | `approve` is `risky`/`all` and nothing is approving: set `jade.approver`, or answer in the REPL/dashboard. |
| Too much text in the prompt | `/context` in the REPL shows what fills it; lower `memory_lines`, or raise `num_ctx`. |
| Logs | `/Jade/Logs/cron.log`, `/Jade/Logs/notifications.log`, the dashboard's Logs page. |

To run the test suite: `pip install -e ".[dev,all]" && pytest` (a fake model server; no model needed).
