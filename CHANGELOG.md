# Changelog

## 0.2.0

- Search by meaning: sqlite-vec + FTS5 hybrid index in /Jade/.index/vectors.db (the same file, schema and chunk hashes as the app), the embeddings tool, memory search by meaning.
- The journal: an entry per chat (when a chat is left or compacted) in /Jade/Journal, and facts it learns go to Memory.md.
- chat_history: list, read and search past chats (keywords + meaning).

## 0.1.0

- First release: the core agent (Ollama and OpenAI-compatible providers with fallbacks, streaming, tool calls, context fitting and compaction, the tool router), the vault (same layout as the Jade Vault app: links, tags, backlinks, search), memory, skills, built-in tools (files, the real shell, tasks, web, HTTP, notes, memory, skills), the library API, the REPL, the HTTP API (compatible with the app's /v1 routes) and step mode.
