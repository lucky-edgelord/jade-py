"""The vault: a folder of notes with [[wiki links]] and #tags, the same layout as the Jade Vault app.

Vault paths are absolute inside the vault ("/projects/Plan.md"); they map onto files under the root folder and can
never leave it. The link and tag index is rebuilt incrementally (a note is re-read only when its size or mtime
changed) and link resolution uses hash lookups, so big vaults stay fast."""
from __future__ import annotations

import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path, PurePosixPath

NOTE_EXT = re.compile(r"\.(md|markdown)$", re.I)
TEXT_EXT = re.compile(r"\.(md|markdown|txt|js|mjs|cjs|ts|json|csv|tsv|html?|css|sh|py|ya?ml|xml|svg|log|ini|toml|sql)$", re.I)
WIKI_RE = re.compile(r"\[\[([^\[\]\n|#]+)(#[^\[\]\n|]*)?(?:\|([^\[\]\n]+))?\]\]")
MDLINK_RE = re.compile(r"\]\(([^)\s]+\.(?:md|markdown))(?:#[^)]*)?\)", re.I)
TAG_RE = re.compile(r"(^|[\s(\[,;])#([A-Za-z][\w/-]*)")
CODE_RE = re.compile(r"^(```|~~~)[\s\S]*?^\1", re.M)
SKIP_DIRS = {".git", "node_modules", ".trash", ".data", ".index", "__pycache__", ".obsidian"}


def strip_code(md: str) -> str:
    md = CODE_RE.sub(" ", md)
    md = re.sub(r"`[^`\n]*`", " ", md)
    md = re.sub(r"\$\$[\s\S]*?\$\$", " ", md)
    return re.sub(r"\$(?!\s)[^$\n]+?\$", " ", md)


def note_name(p: str) -> str:
    return NOTE_EXT.sub("", PurePosixPath(p).name)


def name_key(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", note_name(s).lower()).strip()


def is_note(p: str) -> bool:
    return bool(NOTE_EXT.search(p))


@dataclass
class _Parsed:
    sig: tuple[int, int]
    wiki: list[str]
    md: list[str]
    tags: set[str]
    words: int


@dataclass
class Index:
    notes: list[str]
    out: dict[str, set[str]] = field(default_factory=dict)
    back: dict[str, set[str]] = field(default_factory=dict)
    tags: dict[str, set[str]] = field(default_factory=dict)
    missing: dict[str, set[str]] = field(default_factory=dict)
    words: int = 0


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, root: str | os.PathLike, *, create: bool = True, seed: bool = True):
        self.root = Path(os.path.expanduser(str(root))).resolve()
        if create:
            fresh = not self.root.exists() or not any(self.root.iterdir())
            self.root.mkdir(parents=True, exist_ok=True)
            if fresh and seed:
                self.seed()
        if not self.root.is_dir():
            raise VaultError(f"{self.root} is not a folder")
        self._parsed: dict[str, _Parsed] = {}
        self._index: Index | None = None
        self._index_key: tuple | None = None
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- paths
    def norm(self, p: str | None, cwd: str = "/") -> str:
        p = "." if p in (None, "") else str(p).replace("\\", "/")
        if p == "~":
            p = "/"
        elif p.startswith("~/"):
            p = "/" + p[2:]
        parts = (p if p.startswith("/") else f"{cwd}/{p}").split("/")
        out: list[str] = []
        for s in parts:
            if not s or s == ".":
                continue
            if s == "..":
                if out:
                    out.pop()
            else:
                out.append(s)
        return "/" + "/".join(out)

    def real(self, p: str) -> Path:
        """The file behind a vault path. Refuses anything that would leave the vault (also through symlinks)."""
        vp = self.norm(p)
        f = self.root.joinpath(*[s for s in vp.split("/") if s])
        try:
            r = f.resolve()
        except OSError:
            r = f
        if r != self.root and self.root not in r.parents:
            raise VaultError(f"outside the vault: {p}")
        return f

    def vpath(self, real: str | os.PathLike) -> str:
        r = Path(real).resolve()
        if r == self.root:
            return "/"
        try:
            return "/" + r.relative_to(self.root).as_posix()
        except ValueError:
            raise VaultError(f"{real} is not in the vault") from None

    # ---------------------------------------------------------------- files
    def exists(self, p: str) -> bool:
        return self.real(p).exists()

    def is_file(self, p: str) -> bool:
        return self.real(p).is_file()

    def is_dir(self, p: str) -> bool:
        return self.real(p).is_dir()

    def read(self, p: str) -> str:
        f = self.real(p)
        if not f.is_file():
            raise VaultError(f"{self.norm(p)}: no such file")
        return f.read_text(encoding="utf-8", errors="replace")

    def read_bytes(self, p: str) -> bytes:
        f = self.real(p)
        if not f.is_file():
            raise VaultError(f"{self.norm(p)}: no such file")
        return f.read_bytes()

    def write(self, p: str, data: str | bytes, *, mkdirs: bool = True) -> str:
        f = self.real(p)
        if f.is_dir():
            raise VaultError(f"{self.norm(p)} is a folder")
        if mkdirs:
            f.parent.mkdir(parents=True, exist_ok=True)
        elif not f.parent.is_dir():
            raise VaultError(f"{self.norm(str(PurePosixPath(p).parent))}: no such folder")
        tmp = f.with_name(f.name + ".jvtmp")
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
        os.replace(tmp, f)
        return self.norm(p)

    def append(self, p: str, text: str) -> None:
        f = self.real(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as h:
            h.write(text)

    def mkdir(self, p: str) -> None:
        self.real(p).mkdir(parents=True, exist_ok=True)

    def remove(self, p: str, recursive: bool = False) -> None:
        if self.norm(p) == "/":
            raise VaultError("refusing to remove the vault root")
        f = self.real(p)
        if f.is_dir():
            if recursive:
                shutil.rmtree(f)
            else:
                f.rmdir()
        elif f.exists():
            f.unlink()

    def move(self, a: str, b: str) -> str:
        fa, fb = self.real(a), self.real(b)
        if fb.is_dir():
            fb = fb / fa.name
        fb.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fa), str(fb))
        return self.vpath(fb)

    def list(self, p: str = "/") -> list[str]:
        f = self.real(p)
        if not f.is_dir():
            raise VaultError(f"{self.norm(p)}: not a folder")
        return sorted(x.name + ("/" if x.is_dir() else "") for x in f.iterdir() if not x.name.endswith(".jvtmp"))

    def walk(self, p: str = "/", *, notes_only: bool = False, hidden: bool = False):
        """Vault paths of the files under p, sorted."""
        base = self.real(p)
        out = []
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if hidden or (x not in SKIP_DIRS and not x.startswith(".")))
            for fn in files:
                if fn.endswith(".jvtmp") or (not hidden and fn.startswith(".")):
                    continue
                if notes_only and not is_note(fn):
                    continue
                out.append(self.vpath(Path(d) / fn))
        return sorted(out, key=str.lower)

    def stat(self, p: str) -> os.stat_result:
        return self.real(p).stat()

    def seed(self) -> int:
        """Copy the starter notes, skills and Jade's files (the same as a new Jade Vault app) into an empty vault."""
        n = 0
        src = resources.files("jade") / "seed"

        def copy(node, rel: str):
            nonlocal n
            for child in node.iterdir():
                r = f"{rel}/{child.name}"
                if child.is_dir():
                    copy(child, r)
                else:
                    dst = self.real(r)
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_bytes(child.read_bytes())
                        n += 1

        copy(src, "")
        return n

    # ---------------------------------------------------------------- notes, links, tags
    def notes(self) -> list[str]:
        return [p for p in self.walk("/", notes_only=True) if not p.lower().startswith("/jade/skills/")]

    def _parse(self, p: str) -> _Parsed:
        st = self.real(p).stat()
        sig = (st.st_size, st.st_mtime_ns)
        cached = self._parsed.get(p)
        if cached and cached.sig == sig:
            return cached
        try:
            text = self.read(p)
        except (OSError, VaultError):
            text = ""
        body = strip_code(text)
        wiki = []
        for m in WIKI_RE.finditer(body):
            t = m.group(1).strip()
            if m.start() > 0 and body[m.start() - 1] == "!" and re.search(r"\.\w{2,5}$", t) and not is_note(t):
                continue  # ![[image.png]] is an embed, not a link
            wiki.append(t)
        md = []
        for m in MDLINK_RE.finditer(body):
            if re.match(r"^[a-z][a-z0-9+.-]*:", m.group(1), re.I):
                continue
            from urllib.parse import unquote
            md.append(unquote(m.group(1)))
        tags = {m.group(2).rstrip("/-").lower() for m in TAG_RE.finditer(body) if m.group(2).rstrip("/-")}
        parsed = _Parsed(sig, wiki, md, tags, len(text.split()))
        self._parsed[p] = parsed
        return parsed

    def index(self) -> Index:
        with self._lock:
            notes = self.notes()
            key = tuple((p, self._sig(p)) for p in notes)
            if self._index is not None and key == self._index_key:
                return self._index
            idx = Index(notes=notes)
            self._lookup = self._make_lookup(notes)
            for p in notes:
                idx.back[p] = set()
            for p in notes:
                e = self._parse(p)
                out, miss = set(), set()
                for t in e.wiki:
                    r = self.resolve(t, p)
                    if r:
                        if r != p:
                            out.add(r)
                    else:
                        miss.add(t)
                for t in e.md:
                    r = self.resolve(t, p)
                    if r and r != p:
                        out.add(r)
                idx.out[p], idx.missing[p] = out, miss
                for r in out:
                    idx.back.setdefault(r, set()).add(p)
                for t in e.tags:
                    idx.tags.setdefault(t, set()).add(p)
                idx.words += e.words
            for k in list(self._parsed):
                if k not in idx.back:
                    del self._parsed[k]
            self._index, self._index_key = idx, key
            return idx

    def _sig(self, p: str):
        try:
            st = self.real(p).stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    @staticmethod
    def _make_lookup(notes: list[str]) -> dict:
        by_base: dict[str, list[str]] = {}
        by_key: dict[str, list[str]] = {}
        for p in notes:
            by_base.setdefault(PurePosixPath(p).name.lower(), []).append(p)
            k = name_key(p)
            if k:
                by_key.setdefault(k, []).append(p)
        return {"base": by_base, "key": by_key}

    def resolve(self, target: str, from_path: str | None = None) -> str | None:
        """The note a [[link]] points to: exact path, then same name (closest folder first), then a loose name match."""
        t = str(target or "").strip().replace("\\", "/")
        if not t:
            return None
        if not is_note(t) and not TEXT_EXT.search(t):
            t += ".md"
        d = str(PurePosixPath(from_path).parent) if from_path else "/"
        for cand in (self.norm(t, d), self.norm(t, "/")):
            try:
                if self.is_file(cand):
                    return cand
            except VaultError:
                pass
        lk = getattr(self, "_lookup", None) or self._make_lookup(self.notes())
        lc = t.lower()
        base = lc.split("/")[-1]
        hits = []
        if "/" in lc:
            tail = "/" + lc.lstrip("/")
            hits = [p for p in lk["base"].get(base, []) if p.lower().endswith(tail)]
        if not hits:
            hits = list(lk["base"].get(base, []))
        if not hits:
            k = name_key(base)
            hits = list(lk["key"].get(k, [])) if k else []
        if not hits:
            return None
        hits.sort(key=lambda p: (str(PurePosixPath(p).parent) != d, len(p), p))
        return hits[0]

    def links(self, p: str) -> dict:
        idx = self.index()
        p = self.resolve(p) or self.norm(p)
        return {
            "path": p,
            "outgoing": sorted(idx.out.get(p, ())),
            "backlinks": sorted(idx.back.get(p, ())),
            "unresolved": sorted(idx.missing.get(p, ())),
            "tags": sorted(t for t, s in idx.tags.items() if p in s),
        }

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Notes containing every word (or one #tag), best first: name matches, word counts, backlinks."""
        q = str(query or "").strip()
        if not q:
            return []
        idx = self.index()
        tag_q = q[1:].lower() if re.fullmatch(r"#[\w/-]+", q) else None
        terms = [] if tag_q else [t for t in q.lower().split() if t]
        hits = None
        if tag_q:
            hits = set()
            for t, s in idx.tags.items():
                if t == tag_q or t.startswith(tag_q + "/"):
                    hits |= s
        res = []
        for p in idx.notes:
            if hits is not None and p not in hits:
                continue
            try:
                text = self.read(p)
            except (OSError, VaultError):
                continue
            lc, name, pl = text.lower(), note_name(p).lower(), p.lower()
            if not tag_q and not all(t in lc or t in pl for t in terms):
                continue
            score, first = 0.0, -1
            for t in terms:
                score += 30 if name == t else 12 if t in name else 0
                c, i = 0, lc.find(t)
                while i >= 0 and c < 60:
                    first = i if first < 0 or i < first else first
                    c += 1
                    i = lc.find(t, i + 1)
                score += c
            if tag_q:
                first, score = lc.find("#" + tag_q), 1.0
            score += min(5, len(idx.back.get(p, ()))) * 0.5
            s = max(0, (first if first > 0 else 0) - 50)
            snippet = ("…" if s else "") + re.sub(r"\s+", " ", text[s:s + 190]).strip() + ("…" if len(text) > s + 190 else "")
            res.append({"path": p, "score": score, "snippet": snippet})
        res.sort(key=lambda r: (-r["score"], r["path"]))
        return res[:limit]

    def overview(self) -> str:
        idx = self.index()
        tags = sorted(idx.tags.items(), key=lambda kv: -len(kv[1]))[:20]
        return f"{len(idx.notes)} notes, {idx.words} words. Tags: " + (" ".join(f"#{t}({len(s)})" for t, s in tags) or "none")

    def daily_path(self, d: date | None = None) -> str:
        return f"/daily/{(d or date.today()).isoformat()}.md"
