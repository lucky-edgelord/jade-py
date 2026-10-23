"""Long-term memory, the same files as the Jade Vault app:
  /Jade/Memory.md       durable facts, one bullet per line, under sections (in every chat's instructions)
  /Jade/Journal/<day>.md  episodic memory: a short entry per finished chat
Search is keyword (BM25) here; with embeddings on it also searches by meaning (see embeddings.py)."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vault import Vault

MEM_PATH = "/Jade/Memory.md"
JOURNAL_DIR = "/Jade/Journal"
SECTIONS = {"about": "About you", "preferences": "Preferences", "projects": "Projects & people", "notes": "Notes to self", "learned": "Learned from chats"}
TEMPLATE = """# Memory

Jade reads this note at the start of every chat. Edit it freely: one fact per line.
She adds to it when you tell her something worth keeping (or say "remember…"), and her
chat journal in [[Journal]] adds facts it learns. #memory

## About you

## Preferences

## Projects & people

## Notes to self

## Learned from chats
"""
JOURNAL_NOTE = "# Journal\n\nJade's diary of past chats, one note per day in the Journal folder. She searches it with the memory tool to recall earlier conversations. #memory\n"


def mem_key(s: str) -> str:
    s = re.sub(r"\(\d{4}-\d{2}-\d{2}\)", "", str(s).lower())
    return re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).replace("_", " ").strip()


STOP = set("a an and are as at be but by for from had has have he her his i if in into is it its me my of on or our she so that the their them then there they this to was we were what when where which who will with you your do does did can could would should about just not no yes".split())


def tokens(text: str) -> list[str]:
    out = []
    for w in re.split(r"[^\w]+", re.sub(r"\(\d{4}-\d{2}-\d{2}\)", " ", str(text).lower())):
        if len(w) < 2 or w in STOP or w == "_":
            continue
        if len(w) > 4 and w[-1].isalpha():
            w = re.sub(r"(ing|ed|es|s)$", "", w)
        out.append(w)
    return out


class BM25:
    """Okapi BM25 with rank_bm25's idf floor for very common words."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self.k1, self.b, self.N = k1, b, len(corpus)
        self.tf = [Counter(d) for d in corpus]
        self.len = [len(d) for d in corpus]
        self.avgdl = (sum(self.len) / self.N) if self.N else 0
        nd: Counter = Counter()
        for m in self.tf:
            nd.update(m.keys())
        self.idf, neg, total = {}, [], 0.0
        for t, n in nd.items():
            v = math.log((self.N - n + 0.5) / (n + 0.5))
            self.idf[t] = v
            total += v
            if v < 0:
                neg.append(t)
        floor = max(epsilon * (total / len(nd) if nd else 0), 0.01)
        for t in neg:
            self.idf[t] = floor

    def scores(self, query: list[str]) -> list[float]:
        sc = [0.0] * self.N
        for q in set(query):
            idf = self.idf.get(q)
            if idf is None:
                continue
            for i in range(self.N):
                f = self.tf[i].get(q)
                if f:
                    sc[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avgdl or 1)))
        return sc


class Memory:
    def __init__(self, vault: "Vault"):
        self.vault = vault

    def ensure(self) -> None:
        if not self.vault.is_file(MEM_PATH):
            self.vault.write(MEM_PATH, TEMPLATE)
        if not self.vault.is_file("/Jade/Journal.md"):
            self.vault.write("/Jade/Journal.md", JOURNAL_NOTE)

    def lines(self) -> list[dict]:
        if not self.vault.is_file(MEM_PATH):
            return []
        sec, out = "", []
        for line in self.vault.read(MEM_PATH).split("\n"):
            h = re.match(r"^##\s+(.+)", line)
            if h:
                sec = h.group(1).strip()
                continue
            b = re.match(r"^\s*[-*]\s+(.+)", line)
            if b and b.group(1).strip():
                out.append({"sec": sec, "text": b.group(1).strip(), "line": line})
        return out

    def save(self, text: str, section: str = "notes") -> str:
        self.ensure()
        text = re.sub(r"^[-*]\s+", "", re.sub(r"\s+", " ", str(text or ""))).strip()
        if not text:
            raise ValueError("save needs text")
        k = mem_key(text)
        for m in self.lines():
            mk = mem_key(m["text"])
            if mk == k or (len(k) > 12 and (k in mk or (mk in k and len(mk) > 12))):
                return f'Already remembered: "{m["text"]}"'
        head = SECTIONS.get(section) or (section if section in SECTIONS.values() else SECTIONS["notes"])
        lines = self.vault.read(MEM_PATH).split("\n")
        i = next((n for n, l in enumerate(lines) if re.match(r"^##\s+" + re.escape(head) + r"\s*$", l, re.I)), -1)
        if i < 0:
            lines += ["", "## " + head]
            i = len(lines) - 1
        j = i + 1
        while j < len(lines) and not re.match(r"^##\s", lines[j]):
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        lines.insert(j, f"- {text} ({date.today().isoformat()})")
        self.vault.write(MEM_PATH, re.sub(r"\n{3,}", "\n\n", "\n".join(lines)))
        return f'Saved to memory under "{head}": {text}'

    def forget(self, text: str) -> str:
        k = mem_key(text)
        if not k:
            raise ValueError("forget needs the text (or part of it) to remove")
        hits = [m for m in self.lines() if k in mem_key(m["text"])]
        if not hits:
            raise ValueError(f'Nothing in memory matches "{text}". Use action "list" to see what is stored.')
        if len(hits) > 3:
            raise ValueError(f'{len(hits)} memories match "{text}"; be more specific:\n' + "\n".join("- " + h["text"] for h in hits))
        drop = {h["line"] for h in hits}
        self.vault.write(MEM_PATH, "\n".join(l for l in self.vault.read(MEM_PATH).split("\n") if l not in drop))
        return "Forgot " + str(len(hits)) + ": " + ", ".join(f'"{h["text"]}"' for h in hits)

    def search(self, query: str, limit: int = 15) -> list[dict]:
        """Memory lines and journal lines, best BM25 match first (memory slightly ahead)."""
        docs = []
        files = [MEM_PATH] + sorted((p for p in self.vault.walk(JOURNAL_DIR, notes_only=True)) if self.vault.is_dir(JOURNAL_DIR) else [], reverse=True)
        for f in files:
            if not self.vault.is_file(f):
                continue
            head = ""
            for line in self.vault.read(f).split("\n"):
                if re.match(r"^#{1,3}\s", line):
                    head = re.sub(r"^#+\s*", "", line)
                    continue
                if line.strip():
                    docs.append({"f": f, "head": head, "line": line.strip()})
        if not docs:
            return []
        q = tokens(query)
        if not q:
            return docs[:limit]
        bm = BM25([tokens(d["line"]) for d in docs])
        sc = bm.scores(q)
        ranked = sorted(((s + (0.5 if d["f"] == MEM_PATH else 0), d) for s, d in zip(sc, docs) if s > 0), key=lambda x: -x[0])
        return [d for _, d in ranked[:limit]]

    def search_text(self, query: str, limit: int = 15) -> str:
        r = self.search(query, limit)
        if not r:
            return "Nothing found in memory or the journal. Try notes_search for the rest of the vault."
        from .vault import note_name
        return "\n".join(f"{'memory' if x['f'] == MEM_PATH else note_name(x['f'])}{' · ' + x['head'] if x['head'] and x['f'] != MEM_PATH else ''}: {x['line'][:300]}" for x in r)

    def for_prompt(self, max_lines: int = 60, query: str = "") -> tuple[str, int, int]:
        """(text, shown, total): the newest lines, or with a query the best matching ones, for the system prompt."""
        ls = self.lines()
        if not ls:
            return "(empty so far)", 0, 0
        if query and len(ls) > max_lines:
            bm = BM25([tokens(m["text"]) for m in ls])
            sc = bm.scores(tokens(query))
            keep = sorted(range(len(ls)), key=lambda i: -sc[i])[:max_lines]
            pick = [ls[i] for i in sorted(keep)]
        else:
            pick = ls[-max_lines:]
        out, sec = [], None
        for m in pick:
            if m["sec"] != sec:
                sec = m["sec"]
                out.append(f"## {sec}" if sec else "")
            out.append(f"- {m['text']}")
        return "\n".join(x for x in out if x is not None).strip(), len(pick), len(ls)

    def journal_add(self, title: str, entry: str, facts: list[str] | None = None) -> str:
        p = f"{JOURNAL_DIR}/{date.today().isoformat()}.md"
        if not self.vault.is_file(p):
            self.vault.write(p, f"# Journal {date.today().isoformat()}\n")
        self.vault.append(p, f"\n## {title}\n{entry.strip()}\n")
        for f in facts or []:
            try:
                self.save(f, "learned")
            except ValueError:
                pass
        return p
