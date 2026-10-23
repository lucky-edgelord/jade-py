"""Skills: instruction packs in /Jade/skills/<name>/SKILL.md (frontmatter name + description, then steps),
the same format as the Jade Vault app (and Claude's skills)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .memory import BM25, tokens

if TYPE_CHECKING:
    from .vault import Vault

SKILLS_DIR = "/Jade/skills"


@dataclass
class Skill:
    name: str
    description: str
    path: str


def _front(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n([\s\S]*?)\n---\s*\n?", text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).split("\n"):
        k, _, v = line.partition(":")
        if k.strip():
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


class Skills:
    def __init__(self, vault: "Vault"):
        self.vault = vault
        self._cache: tuple | None = None

    def list(self) -> list[Skill]:
        if not self.vault.is_dir(SKILLS_DIR):
            return []
        files = [p for p in self.vault.walk(SKILLS_DIR) if p.endswith("/SKILL.md")]
        key = tuple((p, self.vault.stat(p).st_mtime_ns) for p in files)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        out = []
        for p in files:
            meta, _ = _front(self.vault.read(p))
            name = meta.get("name") or p.split("/")[-2]
            out.append(Skill(name, meta.get("description", ""), p))
        out.sort(key=lambda s: s.name)
        self._cache = (key, out)
        return out

    def get(self, name: str) -> Skill | None:
        n = str(name or "").strip().lower().replace(" ", "-")
        for s in self.list():
            if s.name.lower() == n or s.path.split("/")[-2].lower() == n:
                return s
        return None

    def load(self, name: str, inp: str = "") -> str:
        s = self.get(name)
        if not s:
            raise ValueError(f'no skill called "{name}". Skills: {", ".join(x.name for x in self.list()) or "none yet"}')
        _, body = _front(self.vault.read(s.path))
        folder = s.path.rsplit("/", 1)[0]
        extra = [p for p in self.vault.walk(folder) if p != s.path]
        out = f"# Skill: {s.name}\n{body.strip()}"
        if extra:
            out += "\n\nFiles in this skill (read them when the steps say so):\n" + "\n".join("- " + p for p in extra)
        if inp:
            out += f"\n\nApply it to: {inp}"
        return out

    def find(self, query: str, limit: int = 6) -> list[Skill]:
        sk = self.list()
        if not sk:
            return []
        bm = BM25([tokens(f"{s.name.replace('-', ' ')} {s.description}") for s in sk])
        sc = bm.scores(tokens(query))
        order = sorted(range(len(sk)), key=lambda i: -sc[i])
        return [sk[i] for i in order[:limit] if sc[i] > 0] or sk[:limit]

    def for_prompt(self, query: str = "", max_listed: int = 15) -> str:
        sk = self.list()
        if not sk:
            return ""
        shown = sk if len(sk) <= max_listed else self.find(query, max_listed) if query else sk[:max_listed]
        lines = [f"- {s.name}: {s.description[:160]}" for s in shown]
        more = f"\n({len(sk) - len(shown)} more skills: use find_skill to search them.)" if len(shown) < len(sk) else ""
        return "\n".join(lines) + more
