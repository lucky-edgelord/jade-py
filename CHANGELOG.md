# Changelog

## 0.10.0

The model router, as in Jade Vault 2.23.

- **Model router.** `router_on` and `router_models` (each `{model = "provider:model", note = "what it is good at"}`): before each chat turn the decision model (laya, through `decide_provider` / `ollaya_host`, so a fleet broker works too) picks which model answers. `router_decider` picks another decision model than `ollaya_model`; below `router_min_confidence` the main model answers. The pick holds for the whole turn; if laya fails or the picked model cannot be reached, the main model answers. Turns report a `routed` event; the REPL and dashboard show which model answered. Settings: Dashboard → Settings → Model router.
- `decide_data(..., model=)` takes the decision model to use.

## 0.9.0

Caught up with Jade Vault 2.18-2.22.

- **Decisions from Ollama too.** `decide_provider`: `ollaya` (default) or `ollama` (0.35 and newer, the same `/v1/systemone` API) at `decide_ollama_host` (empty: `host`). The decide tool, flow Decide steps, `jade.decide` and think's judge use whichever is chosen.
- **Web calls asked for together run at the same time.** Several `web_search` / `web_fetch` calls in one round start together; results are recorded in the order the model asked. In step mode, editing or skipping a call drops its early run.
- **The onnx tool**: ONNX Runtime on this machine's GPU (CUDA, TensorRT, ROCm, DirectML, CoreML; `onnx_providers`) or CPU: status, load (vault path or URL, saved to `/models`), run (numbers, nested lists, `.json` / `.f32` files, pictures), info, unload; outputs summarised with the top 5 and labels, big ones saved to `/models/out`.
- **The litert tool**: `.tflite` models through LiteRT (`ai-edge-litert`, or `tflite-runtime`), quantized models included (outputs dequantized, `raw_output` keeps the integers).
- **Model files are checked before loading**: an XML or HTML error page, a JSON error, a git-LFS pointer or a `.tflite` without its `TFL3` header is named for what it is; a download that comes back as one is not saved; the same model downloaded again reuses the file.
- **The usb tool**: devices plugged into this machine through PyUSB (list, request, open/close, claim/release, control and bulk/interrupt transfers, saving what comes back), limited by `usb_allow`, asking first when `approve = "risky"`.
- **Jade Fleet**: `fleet_url`, `fleet_token`, `fleet_device`; `jade init --fleet URL --fleet-token TOKEN` points chat, embeddings and decisions at a jade-fleet broker, whose requests carry the token (and only those); the **vault_search** and **vision** tools.
- Settings page: the new settings, in "Embeddings and decisions", "On-device models and USB" and "Jade Fleet". The fleet token is kept out of backups.
- Fix: a note that disappears while the vault is being indexed, or cannot be read (a broken link), no longer breaks the link and tag index.
- Fix: after one server stopped in a process (`create_app` used again, as in tests or an embedding app), the next one's event streams (`/v1/events`: approvals, notifications, talk mode) closed at once; sse-starlette's process-wide shutdown flag is now reset when the API starts.
- Fix (dashboard, Sentry page): two quick changes (ticking a device, then switching a system on there) could lose the first, because both read Sentry.json and wrote it back at the same time; changes are now saved one after another.
- Tests wait for the event stream and for saved settings instead of fixed pauses (they failed now and then on slower machines).
- CI (GitHub Actions): Python 3.11, 3.12 and 3.13 with every extra, Chromium and a JDK, flake8, and Jade Link's JVM tests.

## 0.8.0

- Phones and tablets, through the new **Jade Link** Android app (android/, built without the Android SDK like the app): it calls in to `jade serve` (long-poll, works on mobile data), or listens and jade-py calls it, or both. Several at once (tested with four): tools take `phone` = a name, several, or `all`, and run on each at the same time.
- Tools back from the app: phone (alarms, calls, texts, contacts, sounds, volume, Bluetooth, media, casting…), screen (drive other apps through accessibility), senses (camera, microphone, location, sensors, torch, vibration), look (vault pictures and video frames), sentry.
- Sentry: the app's rules in /Jade/Config/Sentry.json, run by each device's engine; fires run flows here and are acknowledged; per-device rules (`phones`) and systems (`devices`); the allow list (sentry_allow, sentry_limit) for Sentry-run calls and texts.
- Pictures tools take (camera, screenshots, look) reach the model after the tool calls (look_images).
- The web dashboard at / (/ui/): the app's look and its phone layout (bottom tabs, the drawer with chats and status), chat with streaming, the avatar (the app's Live2D Jade, themes and colours, taken from the app by scripts/extract_avatar.py), talk mode (hands-free, wake phrase, avatar-only screen with captions), vault, flows, schedules, Sentry, phones, logs, settings, approvals.
- Jade Link: talk mode's voice for the dashboard (Android TTS and speech recognition), questions on the phone (Allow / Don't), kiosk mode (home screen, pinned, device owner lock), start talk mode on open.
- API: approvals (a dashboard can answer, otherwise the phone is asked), /v1/link/*, /v1/phones, /v1/sentry, /v1/overview, /v1/vault/*, /v1/settings (masked secrets; only changed keys are written to the config file), /v1/logs, /v1/transcribe (stt_provider, stt_model), images in POST /v1/chat, stopping a streamed turn.
- Fix: the config path follows $JADE_CONFIG when it is used, not when jade is imported.

## 0.7.0

- Vector and Discord: Jade as a bot through the app's bridges, with the app's rules (owner / admins / everyone, channel whitelist, trigger words, DM policy), one session per chat, a private prompt and a small tool set for people who are not the owner, typing indicators, NO_REPLY, images, invites (Vector), Mini Apps (Vector), and notify_owner with the inbox notes.
- Settings: [vector] and [discord] sections (also JADE_VECTOR__URL and the like).
- Sessions can carry a prompt override (used for bridge chats with people who are not the owner).

## 0.6.0

- The browser: headless Chromium through Playwright with a kept profile, the app's page agent and actions, and the browser.* playbook steps (goto, click, type, select, check, press, scroll, wait_for, exists, url, title, extract, assert, eval, screenshot), limited to allow_hosts.
- Packaging: tools/specs.json and the page scripts are included in the wheel.

## 0.5.0

- MCP: the app's /Jade/mcp.json (streamable HTTP, plus local stdio servers), exposures (direct, deferred, codemode, hidden), the codemode JavaScript tool, resources, and an mcp tool to manage servers.
- SSH: saved hosts, interactive sessions (send keys, read until a pattern), one-shot commands, SFTP put/get, host keys pinned on first use, approval before each command.
- Tools can be hidden (callable from scripts and codemode, never offered to the model); Jade.prepare_hooks and close_hooks.

## 0.4.0

- think: the app's tree-of-thoughts engine (beam, best-first and PUCT search, tiered verifiers with vetoes, hard budgets, GAE logging to /Jade/Tot), with code checks run by Node in a read-only sandbox.
- delegate: a sub-agent with a clean context, read-only unless allowed to write, with step and time limits.
- decide: the Ollaya decision model (also for flow decide steps, jade.decide in scripts and think verifiers).
- Runs on Python 3.11 again (f-strings that needed 3.12 were rewritten).

## 0.3.0

- Automation, run by `jade daemon` and `jade serve`:
  - schedules (prompt, shell command, playbook or flow; plain English or cron; catch-up after downtime);
  - the heartbeat (HEARTBEAT.md, active hours, silent HEARTBEAT_OK);
  - playbooks (the app's engine and format, approvals, legacy browser scripts converted);
  - flows (the app's format and every step type, triggers on a schedule or when a note or folder changes, run records);
  - curiosity (the app's learning-progress engine, CURIOSITY.md, tool limits per wake-up);
  - backups (the app's backup format and encryption, the backup server included as `jade backup-server`).
- Real Node.js for flow expressions and JavaScript steps, with `fs` rooted at the vault and `jade.*` calling back into Python.
- CLI: `jade flow`, `jade backup`, `jade backup-server`, `jade curiosity`, `jade daemon`.
- API: `/v1/flows` routes, `/v1/schedules`, `/v1/events`.
- Chats can be limited to some tools and a lower step limit (`Session.allow_tools`, `Session.max_steps`).

## 0.2.0

- Search by meaning: sqlite-vec + FTS5 hybrid index in /Jade/.index/vectors.db (the same file, schema and chunk hashes as the app), the embeddings tool, memory search by meaning.
- The journal: an entry per chat (when a chat is left or compacted) in /Jade/Journal, and facts it learns go to Memory.md.
- chat_history: list, read and search past chats (keywords + meaning).

## 0.1.0

- First release: the core agent (Ollama and OpenAI-compatible providers with fallbacks, streaming, tool calls, context fitting and compaction, the tool router), the vault (same layout as the Jade Vault app: links, tags, backlinks, search), memory, skills, built-in tools (files, the real shell, tasks, web, HTTP, notes, memory, skills), the library API, the REPL, the HTTP API (compatible with the app's /v1 routes) and step mode.
