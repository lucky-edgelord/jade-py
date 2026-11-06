# Jade (Python)

Jade, the agent from [Jade Vault](https://github.com/lucky-edgelord/jade-vault), as a Python package. Same vault
layout as the app (notes with `[[links]]` and `#tags`, `/Jade/Memory.md`, `/Jade/SOUL.md`, `/AGENTS.md`, skills in
`/Jade/skills`), so the app and Python can work on one vault. The shell is the real one, and phones and tablets
join through the **Jade Link** app (`android/`), so she can still call, text, see, listen and react to the world.

Use her five ways:

| | |
|---|---|
| **library** | `from jade import Jade` |
| **REPL** | `jade` (or `jade repl`) |
| **HTTP API** | `jade serve` (the app's `/v1` routes, plus POST, streaming and step mode) |
| **step by step** | `session.stepper(...)`, `/step` in the REPL, or `/v1/chats/{id}/step` |
| **dashboard** | `jade serve`, then open `http://<machine>:8760/` (also inside Jade Link on a phone) |

## Install

```sh
pip install jade-vault                 # Python 3.11+
pip install "jade-vault[all]"          # + MCP, SSH, Discord, browser, ONNX / LiteRT, USB
jade init ~/JadeVault --model qwen3:8b # vault with the starter notes and skills, and ~/.config/jade/config.toml
jade                                   # chat
```

Models: Ollama (default, `host`), Ollama Cloud, llama.cpp, LM Studio, vLLM, OpenAI, Groq, OpenRouter, DeepSeek,
Mistral, Gemini, Together, or any OpenAI-compatible URL (`provider = "custom"`, `base_url`). `fallbacks` lists
others to try when the first cannot be reached, e.g. `["groq:llama-3.3-70b-versatile"]`.

In a [Jade Fleet](https://github.com/lucky-edgelord/jade-fleet), point her at the broker instead:
`jade init ~/JadeVault --fleet http://optiplex:8765 --fleet-token TOKEN` (see [Jade Fleet](#jade-fleet)).

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

Plus `POST /v1/chat {message, session, stream, images}` (Server-Sent Events with `stream: true`), `GET/DELETE /v1/chats/{id}`,
step mode (`POST /v1/chats/{id}/step`, `/skip`, `/edit`, `/cancel`, `GET /pending`), and `GET /v1/tools`,
`POST /v1/tools/{name}`, `GET /v1/schedules`, flows (`GET /v1/flows`, `GET /v1/flows/runs`,
`POST /v1/flows/{name}/run {input}`, `POST /v1/flows/runs/{id}/stop`), `GET /v1/events` (notifications, approvals,
Sentry fires as Server-Sent Events), `GET /v1/approvals` and `POST /v1/approvals/{id} {allow}`, phones
(`GET /v1/phones`, `POST /v1/phones/{name}/call {op, args}`, and `/v1/link/*` for Jade Link), `GET /v1/sentry`, the
dashboard's `/v1/overview`, `/v1/vault/*`, `GET/PATCH /v1/settings`, `/v1/logs` and `POST /v1/transcribe`. Docs at
`/docs`. `jade serve` also runs everything `jade daemon` runs.

## Dashboard

`jade serve` also serves a web dashboard at `/` (`/ui/`), in the app's look: chat with streaming, tool cards and
pictures; her avatar (the app's Live2D Jade, gemstone colours and all); **talk mode** (the Talk button: hands-free,
she listens, answers out loud and listens again, with only her avatar on screen and captions; an optional wake
phrase such as `hey jade`; Back, ✕ or a double tap leaves); the vault (browse, search, edit, preview); flows;
schedules; Sentry; phones and tablets; logs; approvals (a tool call waiting for your yes pops up); and the settings.
On a phone it is laid out like the app: one screen at a time, tabs along the bottom, and the drawer (☰) with chats,
status and the other pages.

It signs in with `api_token`. Talk mode hears through the phone's speech recognizer in Jade Link, the browser's in
Chrome or Edge, or jade-py's own transcription model anywhere else (`stt_model`, e.g. `whisper-large-v3-turbo` on
Groq, and "Use jade-py to hear" in the dashboard's settings). Her voice is the device's text-to-speech.

## Phones and tablets (Jade Link)

Install `dist/jade-link-<version>.apk` (built by `android/build-apk.sh`, see `android/README.md`) on each phone or
tablet. Up to four (or more) can be linked at once, and Jade can use one, several or all of them at the same time.
Each one connects whichever way suits it, or both:

- **it calls in** (works anywhere it can reach jade-py, on Wi-Fi or mobile data, e.g. over Tailscale): in Jade Link
  enter jade-py's address (`http://192.168.1.10:8760`) and the API token, and switch on Connect. `jade serve` must be
  running and listening beyond this machine (`api_host = "0.0.0.0"`).
- **jade-py calls it** (same network or a VPN): switch on Listen in Jade Link and add what it shows to the config:

```toml
[[phones]]
name = "tablet"
url = "http://192.168.1.20:8765"
token = "jl_…"
```

Tools (each takes `phone`: a name, several names separated by commas, or `all`, which run at the same time):

- **phone**: alarms, timers, ringing, sounds and radio streams, volume, calls, texts, contacts, call log, clipboard,
  links, apps, brightness, keep awake, radio status, Bluetooth, media keys, notifications, and casting to a
  Chromecast or Google TV. Calls and texts ask first (`phone_confirm`: on the phone, through jade's approver, or not).
- **screen**: drives other apps like a screen reader (once you switch on Jade Link in Android's Accessibility settings).
- **senses**: camera photos (she sees them), the microphone (recordings to the vault, or just how loud), location,
  every sensor, battery, Wi-Fi, the torch, vibration.
- **look**: pictures and video frames from the vault.
- **sentry**: rules that make the devices react on their own (the app's Sentry: sensors, camera and microphone,
  calls, texts, notifications, location, Wi-Fi, battery…), in `/Jade/Config/Sentry.json`. A rule can buzz or notify
  on the device at once and run a flow here. Sentry runs on the devices in its `phones` list (default: all), a rule
  with `phones` only there, and `devices` switches systems per device. Sentry-run flows may text or call only the
  numbers in `sentry_allow`, at most `sentry_limit` an hour.

Jade Link also gives the dashboard its talk-mode voice, asks you before calls and texts, and has **kiosk mode** (as
in the app): it becomes the home screen, full screen and pinned in front, for a tablet on the wall; made device
owner once (`adb shell dpm set-device-owner app.netrunner.jadelink/app.netrunner.jade.AdminReceiver`) it is a real
lock. With "Start talk mode when it opens", the device is simply Jade, listening.

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

Knowledge: **knowledge** (facts like `works_at(alice, acme)`, rules and constraints in `/Jade/Knowledge`, and a
Datalog reasoner, the app's Rust engine as WebAssembly, that answers ✓ proven with its proof, ✗ contradicted or ? unknown.
Jade's facts are proposed until you confirm them. `jade kg verify "works_at(alice, acme)"`, `jade kg check`.)

Autonomy: **sched** (a 3-tier scheduler like an operating system's: tier 1 interrupts, tier 2 a priority queue with
aging and deadlines, tier 3 round robin with 2/4/8-step slices and feedback; tasks are goals, prompts, flows,
playbooks, shell commands or Lisp programs. **Autonomous mode** (`auto_on`, off by default; `jade sched on`) works
through the queue inside `jade serve` / `jade daemon`, within steps an hour, a daily cap and quiet hours; tools in
`auto_deny` wait for your Allow. Log and queue in `/Jade/Autonomy`; `jade sched status|list|add|allow|deny|…` and the
dashboard's Queue page) and **gantt** (Gantt plans for long-term projects in `/Jade/Projects`, with dependencies,
milestones, the critical path and reports; `jade plan PROJECT`, `jade plan-do add project=… name=…`, the Plans page.
With autonomous mode on, Jade's ready plan tasks become tier 3 goals).

Jade Lisp: **lisp** (a small Scheme/Clojure-style Lisp for DSLs and scripts that call any tool: `(tool/NAME :arg v)`,
`jade/ask`, `jade/flow`, files, LiteRT; macros, tail calls, and `(sandbox :pure)` + `eval-in` with a fuel limit for
programs someone else wrote. The same language as the app, checked against the app's own test cases. `jade lisp
FILE.jl` or `jade lisp -e '(+ 1 2)'`; libraries and examples in `/Jade/Lisp`).

Reasoning: **think** (a tree of thoughts where every line is checked by something that can say no: JavaScript run
in a read-only sandbox, a shell command, a file check, a rubric or a judge; the best verified line wins; trees and
episodes in `/Jade/Tot`), **delegate** (a sub-agent with a clean context and read-only tools unless `write: true`,
returning one short report), and **decide** (the local decision model: calibrated probabilities for triage,
scores and yes/no; set `ollaya_model`, and `decide_provider = "ollaya"` with `ollaya_host`, or `"ollama"` for
Ollama 0.35 and newer, which serves the same `/v1/systemone` API, at `decide_ollama_host` or `host`).

When the model asks for several `web_search` / `web_fetch` calls in one round they run at the same time; results
go back in the order it asked (in step mode, a call you edit or skip has its early run dropped).

**Models on this machine**: **onnx** runs any `.onnx` model with ONNX Runtime, on the GPU when there is one (CUDA,
TensorRT, ROCm, DirectML, CoreML; `onnx_providers` sets the order) else the CPU, and **litert** runs `.tflite`
models (LiteRT: `ai-edge-litert`, or `tflite-runtime`). The same tools as the app: `load` from the vault or a URL
(saved to `/models`), `run` with numbers, nested lists, vault `.json` / `.f32` files or pictures (`{image: path}`,
resized and normalised for the model), each output summarised with the top 5 (and labels from a file), big outputs
saved to `/models/out`; quantized `.tflite` outputs come back as real numbers. A model file is checked first: an
error page, a JSON error or a git-LFS pointer saved under a model's name is named for what it is, a download that
comes back as one is not saved, and downloading the same model again reuses the file. `pip install
"jade-vault[ml]"` (on a Jetson, NVIDIA's onnxruntime-gpu wheel for its JetPack).

**USB**: the **usb** tool (PyUSB and libusb, `pip install "jade-vault[usb]"`) talks to devices plugged into this
machine, with the app's actions: list, request, open/close, claim/release, control and bulk/interrupt transfers in
hex or text, saving what comes back. `usb_allow` (vendor:product or vendor, in hex) limits which devices she may
open; she cannot change it, and the tool asks first when `approve = "risky"`. On Linux, a udev rule gives the
user access, e.g. `SUBSYSTEM=="usb", ATTR{idVendor}=="2341", MODE="0660", GROUP="plugdev"`.

**MCP** servers go in `/Jade/mcp.json` (the app's format: `url` + `headers`, or here also `command` + `args` for
a local server). Each tool is `mcp__<server>__<tool>`, exposed `direct` (always offered), `deferred` (loaded by the
tool router when needed), `codemode` (the default: called from JavaScript with `await tools.mcp__s__t(args)` in the
codemode tool) or `hidden`. Headers may use `${SECRET}` or `!command`. The `mcp` tool lists, adds and removes servers.

**SSH** (`ssh_hosts` in the config: name, host, port, user, and a password, `key`, `key_file`, or nothing for the
SSH agent): interactive sessions, one-shot commands and file transfer. Passwords and keys never reach the model,
host keys are pinned on first use, and each command asks first unless the host has `confirm = false`.

**Browser**: Jade's own headless Chromium (Playwright; `playwright install chromium`, or set `browser_path`), with
cookies and logins kept in `<data>/browser`. The same tool as the app (open, read, click, type, select, scroll,
eval, screenshot…), and it runs the `browser.*` steps of playbooks, inside the playbook's `allow_hosts`.

**Vector and Discord**: Jade answers as a bot through the same bridges the app uses (`jade-vector` and
`jade-discord` in the Jade Vault repository, MCP servers with a token). In the config:

```toml
[discord]
on = true
url = "http://127.0.0.1:8791/mcp"
token = "…"
owner = "123456789012345678"     # your user id (an npub for [vector]): you get every tool
admins = []                      # Discord powers, no vault or shell
channels = ["#general"]          # group chats she answers in (when addressed, or trigger = "all")
dm = "anyone"                    # or "known", "owner"
notify = "inbox"                 # notify_owner: "inbox", "urgent" or "now"
```

Each chat gets its own session. People who are not the owner get a short, private prompt (no notes, no memory) and
only the messenger tool, web search and `notify_owner`, which files their message in `/Jade/Vector Inbox.md` or
`/Jade/Discord Inbox.md` (and tells you at once when the setting says so). The bridges are polled while
`jade daemon` or `jade serve` runs.

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

### Trajectories: fine-tuning data with ratings

Every run is logged as the model saw it (system prompt, messages, tool calls and results, tools, token counts) in
`<data>/trajectories`, in the Jade Vault app's format. Rate replies 👍/👎 (dashboard, `/rate up|down` in the REPL,
`POST /v1/chats/{id}/rate`, or `jade.traj.rate`) and get a second answer to the same message with ↻ (`/retry`,
`session.retry()`, `POST /v1/chats/{id}/retry`). Export JSONL:

```sh
jade traj stats
jade traj export -o sft.jsonl                              # finished runs minus 👎, OpenAI chat format
jade traj export --filter prefs --meta -o prefs.jsonl      # 👎 too, with a metadata field: DPO / KTO
jade traj export --format sharegpt                         # LLaMA-Factory
```

`--filter`: `clean` (finished runs minus 👎), `good` (only 👍), `prefs` (finished runs, 👎 too), `all` (errors too).
`--meta` adds `metadata` to each line: `id`, `started`, `model`, `provider`, `source` (chat, heartbeat, schedule,
curiosity), `outcome`, `rating` and per-call `steps`. 👎 a reply, ↻ retry, 👍 the better one: the two lines share
their prompt, which is a preference pair. Defaults come from `traj_format`, `traj_filter`, `traj_thinking` and
`traj_meta`; `traj_on = false` stops logging. The files are the same as the app's, so exports from both train
together (for example with [jade-tune](https://github.com/lucky-edgelord/jade-autotuner)).

### Model router

Before each chat turn the decision model (laya on Ollaya, Ollama 0.35+ or a fleet broker: the same
`decide_provider` / `ollaya_host` the decide tool uses) reads the message and picks which of your models answers it.
Each model gets a note saying what it is good at; laya chooses between the notes.

```toml
router_on = true
router_models = [
  {model = "ollama:qwen3:8b", note = "quick everyday chat, short answers, simple lookups"},
  {model = "groq:openai/gpt-oss-120b", note = "coding, long documents, hard multi-step reasoning"},
]
# router_decider = "laya"         # default: ollaya_model
# router_min_confidence = 0.6     # below this the main model answers
```

`model` is `provider:model` (other providers take their key from the usual environment variable, e.g.
`GROQ_API_KEY`) or a bare model name for the main provider. The pick holds for the whole turn, tool rounds included;
the turn's events start with a `routed` event (provider, model, note, confidence). If laya cannot be reached, is
unsure, or the picked model fails, the main model answers. Only chats are routed; background turns (heartbeat,
curiosity, bridges with other people) keep the main model.

## Jade Fleet

A [jade-fleet](https://github.com/lucky-edgelord/jade-fleet) broker holds a fleet's shared vault, its memory search
and the way to its models (chat on llama-server, embeddings and decisions on Ollama / Ollaya, vision on the DLA
cores). jade-py joins it as one more member:

```sh
jade init ~/JadeVault --fleet http://optiplex:8765 --fleet-token TOKEN --device lab-pc
```

This sets `fleet_url`, `fleet_token` and `fleet_device`, and points chat (`provider = "custom"`, the broker's
`/v1`), embeddings and Ollama (`/ollama`) and decisions (`/v1/systemone`) at the broker. Requests to the broker
carry the token and this machine's name; nothing else is ever sent the token. Two tools come with it:
**vault_search** (meaning-based search over every device's notes and memories, on the broker's index) and
**vision** (`detect` with YOLO, `classify` with MobileNet, on a vault picture). To work on the shared vault
itself, run jade-py on the broker's machine with `vault` set to the broker's vault folder; elsewhere her vault is
her own folder.

## Status

Done: the core agent, tools, memory, skills, the REPL, the API, step mode, search by meaning, the journal, chat
history, schedules, the heartbeat, playbooks, flows, curiosity, backups, think, delegate, decide, MCP, SSH, the
browser, Vector and Discord, and through Jade Link: phone and screen control, the senses, Sentry, talk mode and kiosk
mode, with the dashboard in the app's look; ONNX and LiteRT models and USB devices on the machine jade-py runs on,
decisions from Ollaya or Ollama, and Jade Fleet. Not here: running models on the phone itself (the Jade Vault app
does that), and a vault kept remotely on the fleet broker (run jade-py on the broker's machine to share it).

## Tests

`pip install -e ".[dev]" && pytest` (a fake model server; no model needed). The dashboard tests use Chromium
(Playwright), the Jade Link tests a JDK (its link code runs on a plain JVM against a real `jade serve`), and
`android/test/run.sh` runs its Sentry engine and casting tests.
