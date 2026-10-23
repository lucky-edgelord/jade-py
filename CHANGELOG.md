# Changelog

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
