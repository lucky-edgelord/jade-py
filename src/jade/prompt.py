"""Jade's instructions for a turn, built like the Jade Vault app's (vault conventions, memory, persona, standing
instructions, skills), with the environment described as what it is here: a real machine."""
from __future__ import annotations

import platform
import re
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from .vault import note_name

if TYPE_CHECKING:
    from .core import Jade
    from .session import Session

SOUL_PATH, AGENTS_PATHS, SYSTEM_PATH = "/Jade/SOUL.md", ("/AGENTS.md", "/Jade/AGENTS.md"), "/Jade/SYSTEM.md"

INTRO = ("You are Jade, the keeper of the user's personal knowledge vault: a folder of markdown notes linked together like Obsidian. "
         "Your main job is helping the user capture, find, organise, connect and think with their notes. You run on the user's own "
         "machine and can also read, write, edit and run code there.")

CONVENTIONS = """Vault conventions:
- Every note is a .md file. Folders are optional. Link notes with [[Note Name]] (no .md and no folder needed), [[Note Name|label]] or [[Note Name#Heading]]. Tags are #words.
- New notes start with "# Title". Add [[links]] to related notes, and add a link back where it helps. Search first so you do not create duplicates.
- Images: ![[photo.png]] shows a vault image; ![[Other Note]] embeds a note. Download pictures with http_request (binary responses are saved to the vault).
- Math: write $inline$ or $$display$$ LaTeX (never inside backticks).
- Your heartbeat checklist is /Jade/HEARTBEAT.md: when the user wants you to keep an eye on something regularly, add a short line under its Check heading (use schedule instead for exact times).
- Daily notes live at /daily/YYYY-MM-DD.md. Quick captures go in /Inbox.md. Tasks are "- [ ] item" lines.
- When you mention a note in a reply, write it as [[Note Name]]."""

GUIDELINES = """Guidelines:
- Look before you change things: notes_search or read the note first. Use edit for small changes (oldText must match exactly), write for new notes or full rewrites.
- Keep the user's words. When reorganising, move or link content rather than deleting it.
- Never create a second note with the same name as an existing one; edit the existing note. Paths may omit .md.
- Memory: when the user tells you something lasting about themselves, a preference, a project or person, or says "remember", call memory save with one short fact. When they mention an earlier conversation or ask what you know, use memory search. Memory is for facts about the user; notes are for their content.
- For work with three or more steps, add tasks first, set each to in_progress when you start it and done when finished.
- Be concise. When finished, say which notes changed, as [[links]]."""


def strip_comments(s: str) -> str:
    s = re.sub(r"<!--[\s\S]*?-->", "", s)
    s = re.sub(r"^---\s*\n[\s\S]*?\n---\s*\n?", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _read(jade: "Jade", p: str) -> str:
    try:
        return strip_comments(jade.vault.read(p)) if jade.vault.is_file(p) else ""
    except Exception:
        return ""


def environment(jade: "Jade") -> str:
    have = [x for x in ("python3", "node", "git", "sqlite3", "curl", "jq", "pip") if shutil.which(x)]
    now = jade.now()
    tz = now.tzname() or ""
    return "\n".join([
        "Environment:",
        f"- The vault is the folder {jade.vault.root}. Vault paths (/Inbox.md) are relative to it; the read/write/edit tools take vault paths.",
        f"- bash is the real shell ({jade.settings.shell}) on {platform.system()} {platform.machine()}, started in the vault folder each call. "
        f"Installed: {', '.join(have) or 'the basics'}. Write longer scripts to a file and run them. Network access is whatever this machine has.",
        "- python scripts can call you back: import jade; jade.tool('notes_search', {'query': 'x'}) works in the shell's python when the jade package is installed.",
        f"- Today is {now.strftime('%A, %B %d, %Y')} (time zone {tz}). Each user message starts with the time it was sent, like [Sun 2026-10-11 20:18 EDT]. "
        "Use it for now, today, deadlines and how much time has passed, and never copy it into your replies.",
    ])


def build(jade: "Jade", session: "Session", tools: list, query: str = "") -> str:
    if getattr(session, "prompt_override", None):
        out = session.prompt_override(jade, session)
        return out + (f"\n\nEarlier in this chat (summarised to save room):\n{session.summary}" if session.summary else "")
    v = jade.vault
    idx = v.index()
    custom = _read(jade, SYSTEM_PATH)
    parts = [custom or INTRO, CONVENTIONS]
    tags = sorted(idx.tags.items(), key=lambda kv: -len(kv[1]))[:12]
    vault_now = f"- Vault now: {len(idx.notes)} notes" + (", tags: " + " ".join("#" + t for t, _ in tags) if tags else "") + "."
    if len(idx.notes) <= 60:
        vault_now += "\nNotes: " + ", ".join(idx.notes)
    parts[-1] += "\n" + vault_now
    mem, shown, total = jade.memory.for_prompt(jade.settings.memory_lines, query)
    parts.append(f"Long-term memory (from the note [[Memory]], the {shown} {'best matching' if query and total > shown else 'newest'} of {total} lines; use memory search for anything else, and keep it current with the memory tool):\n{mem}")
    parts.append(environment(jade))
    lines = []
    for t in tools:
        if t.summary:
            lines.append("- " + t.summary)
    if lines:
        parts.append("Tools:\n" + "\n".join(dict.fromkeys(lines)))
    if not custom:
        parts.append(GUIDELINES)
    soul = _read(jade, SOUL_PATH)
    if soul:
        parts.append(f"Your persona (from {SOUL_PATH}; this is who you are, keep this voice in chat):\n{soul}")
    for ap in AGENTS_PATHS:
        a = _read(jade, ap)
        if a:
            a = re.sub(r"^#\s*AGENTS\s*\n", "", a).strip()
            parts.append(f"Standing instructions from the user ({ap}; always follow them):\n{a}")
    sk = jade.skills.for_prompt(query)
    if sk:
        parts.append("Skills (instruction packs in /Jade/skills). When a request matches a skill's description, FIRST call the skill tool with its name, then follow the instructions it returns step by step:\n"
                     + sk + "\nTo create a skill, write /Jade/skills/<kebab-name>/SKILL.md beginning with frontmatter (\"---\", \"name: …\", \"description: … Use when …\", \"---\") followed by numbered steps.")
    if session.summary:
        parts.append(f"Earlier in this chat (summarised to save room):\n{session.summary}")
    if session.tasks:
        mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
        parts.append("Your task list for this chat:\n" + "\n".join(f"{mark.get(t['status'], '[ ]')} #{t['id']} {t['title']}" for t in session.tasks))
    for extra in jade.prompt_extras:
        try:
            s = extra(jade, session) if callable(extra) else str(extra)
            if s:
                parts.append(s)
        except Exception:
            pass
    return "\n\n".join(p for p in parts if p)


def stamp(dt: datetime) -> str:
    return dt.strftime("[%a %Y-%m-%d %H:%M ") + (dt.tzname() or "") + "]"


def title_from(text: str) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"^\[[^\]]+\]\s*", "", text)).strip()
    return (t[:60] + "…") if len(t) > 60 else (t or "New chat")


__all__ = ["build", "stamp", "title_from", "note_name"]
