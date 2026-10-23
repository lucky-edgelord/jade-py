"""Search by meaning: embeddings stored with sqlite-vec, searched together with SQLite FTS5, fused with reciprocal
rank fusion (k = 60). The same file and schema as the Jade Vault app: /Jade/.index/vectors.db (a cache; delete it and
it is rebuilt). Collections: memory and journal (kept in step with the notes), notes (after index), chats (raw
conversations, for chat_history), skills, and any name Jade or you pick."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import struct
import time
from typing import TYPE_CHECKING

import httpx

from .memory import JOURNAL_DIR, MEM_PATH, tokens
from .tools.registry import Tool, ToolError
from .vault import is_note, note_name

if TYPE_CHECKING:
    from .core import Jade

DB_PATH = "/Jade/.index/vectors.db"
AUTO = ("memory", "journal")
RRF = 60


def _squash(s) -> str:
    return re.sub(r"\s+", " ", s)


def _imul(a: int, b: int) -> int:
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


def vhash(s: str) -> str:
    """The app's chunk hash (cyrb53 over UTF-16 code units, base 36), so both build and reuse the same index."""
    def b36(n: int) -> str:
        d = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while True:
            n, r = divmod(n, 36)
            out = d[r] + out
            if not n:
                return out
    h1, h2 = 0xDEADBEEF, 0x41C6CE57
    units = struct.unpack(f"<{len(s.encode('utf-16-le')) // 2}H", s.encode("utf-16-le"))
    for c in units:
        h1 = _imul(h1 ^ c, 2654435761)
        h2 = _imul(h2 ^ c, 1597334677)
    h1 = _imul(h1 ^ (h1 >> 16), 2246822507) ^ _imul(h2 ^ (h2 >> 13), 3266489909)
    h2 = _imul(h2 ^ (h2 >> 16), 2246822507) ^ _imul(h1 ^ (h1 >> 13), 3266489909)
    return b36(4294967296 * (2097151 & h2) + (h1 & 0xFFFFFFFF)) + b36(len(units))


def _blob(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unit(v: list[float]) -> list[float]:
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class Embeddings:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.db: sqlite3.Connection | None = None
        self.dim = 0
        self.model = ""
        self.error = ""
        self.dirty = True
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- the database
    def open(self) -> sqlite3.Connection:
        if self.db:
            return self.db
        import sqlite_vec
        f = self.jade.vault.real(DB_PATH)
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            db = self._connect(f, sqlite_vec)
        except sqlite3.DatabaseError:   # a damaged cache is simply rebuilt
            f.unlink(missing_ok=True)
            db = self._connect(f, sqlite_vec)
        m = dict(db.execute("SELECT k, v FROM meta").fetchall())
        self.dim, self.model = int(m.get("dim") or 0), m.get("model") or ""
        self.db = db
        return db

    @staticmethod
    def _connect(f, sqlite_vec) -> sqlite3.Connection:
        db = sqlite3.connect(str(f), check_same_thread=False, isolation_level=None)
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, coll TEXT NOT NULL, src TEXT NOT NULL DEFAULT '', head TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, hash TEXT NOT NULL, ts INTEGER NOT NULL DEFAULT 0, emb INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS chunks_src ON chunks(coll, src);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, tokenize='porter unicode61');
        """)
        db.execute("SELECT 1 FROM chunks LIMIT 1").fetchall()
        return db

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def mark_dirty(self):
        self.dirty = True

    # ---------------------------------------------------------------- embedding
    @property
    def model_name(self) -> str:
        return self.jade.settings.embed_model or "nomic-embed-text"

    async def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        st = self.jade.settings
        pre = ("search_query: " if kind == "query" else "search_document: ") if "nomic-embed" in self.model_name else ""
        inp = [pre + str(t)[:6000] for t in texts]
        ollama = st.provider in ("ollama", "ollama-cloud") or st.embed_host
        async with httpx.AsyncClient(timeout=120) as c:
            if ollama:
                base = (st.embed_host or st.host).rstrip("/")
                try:
                    r = await c.post(base + "/api/embed", json={"model": self.model_name, "input": inp, "truncate": True})
                except httpx.HTTPError as e:
                    raise ToolError(f"could not reach Ollama at {base} for embeddings: {e}") from e
                if r.status_code == 404 and "not found" in r.text:
                    raise ToolError(f'the embedding model "{self.model_name}" is not installed on {base}. Run: ollama pull {self.model_name}')
                if r.status_code != 200:
                    raise ToolError(f"embeddings failed (HTTP {r.status_code}: {r.text[:200]})")
                vecs = r.json().get("embeddings") or []
            else:
                base, key = st.openai_base_and_key()
                r = await c.post(base + "/embeddings", json={"model": self.model_name, "input": inp}, headers={"Authorization": f"Bearer {key}"} if key else {})
                if r.status_code != 200:
                    raise ToolError(f"embeddings failed (HTTP {r.status_code}: {r.text[:200]})")
                vecs = [d["embedding"] for d in sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))]
        if len(vecs) != len(texts) or not all(vecs):
            raise ToolError(f'no embeddings came back (is "{self.model_name}" an embedding model?)')
        return [_unit(v) for v in vecs]

    def _ensure_dim(self, dim: int):
        if self.dim == dim and self.model == self.model_name:
            return
        db = self.open()
        db.execute("BEGIN")
        db.execute("DROP TABLE IF EXISTS vec")
        db.execute(f"CREATE VIRTUAL TABLE vec USING vec0(coll text partition key, embedding float[{int(dim)}] distance_metric=cosine)")
        db.execute("UPDATE chunks SET emb = 0")
        db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('dim', ?), ('model', ?)", (str(dim), self.model_name))
        db.execute("COMMIT")
        self.dim, self.model = dim, self.model_name

    async def embed_pending(self) -> int:
        db, done = self.open(), 0
        while True:
            rows = db.execute("SELECT id, coll, text FROM chunks WHERE emb = 0 ORDER BY id LIMIT 16").fetchall()
            if not rows:
                return done
            vs = await self.embed([r[2] for r in rows])
            self._ensure_dim(len(vs[0]))
            db.execute("BEGIN")
            for (i, coll, _), v in zip(rows, vs):
                db.execute("DELETE FROM vec WHERE rowid = ?", (i,))
                db.execute("INSERT INTO vec(rowid, coll, embedding) VALUES (?, ?, ?)", (i, coll, _blob(v)))
                db.execute("UPDATE chunks SET emb = 1 WHERE id = ?", (i,))
            db.execute("COMMIT")
            done += len(rows)

    # ---------------------------------------------------------------- what to index
    def memory_chunks(self) -> list[dict]:
        return [{"coll": "memory", "src": MEM_PATH, "head": m["sec"] or "", "text": m["text"]} for m in self.jade.memory.lines()]

    def journal_chunks(self) -> list[dict]:
        v, out = self.jade.vault, []
        if not v.is_dir(JOURNAL_DIR):
            return out
        for f in v.walk(JOURNAL_DIR, notes_only=True):
            day = (re.search(r"(\d{4}-\d{2}-\d{2})", f) or [None, ""])[1]
            for part in re.split(r"^(?=##\s)", v.read(f), flags=re.M):
                m = re.match(r"^##\s+(.*)\n?([\s\S]*)$", part)
                if not m:
                    continue
                body = m.group(2).replace("#journal", "").strip()
                if len(body) < 8:
                    continue
                out.append({"coll": "journal", "src": f, "head": f"{day} {m.group(1).strip()}".strip(), "text": f"{day} · {m.group(1).strip()}\n{body}"[:2500]})
        return out

    def note_chunks(self, prefix: str = "") -> list[dict]:
        v, out = self.jade.vault, []
        for p in v.walk(prefix or "/", notes_only=True):
            if p.startswith(JOURNAL_DIR + "/") or p == MEM_PATH or p.startswith("/Jade/Flows/") or p.startswith("/Jade/.index/"):
                continue
            text = v.read(p)
            if len(text) > 300000:
                continue
            name, head = note_name(p), ""
            for part in re.split(r"^(?=#{1,3}\s)", text, flags=re.M):
                h = re.match(r"^#{1,3}\s+(.*)", part)
                if h:
                    head = h.group(1).strip()
                body = re.sub(r"^#{1,3}\s+.*\n?", "", part).strip()
                if len(body) < 20:
                    continue
                buf = ""
                label = f"{name}{' › ' + head if head else ''}"

                def flush():
                    nonlocal buf
                    if len(buf.strip()) >= 20:
                        out.append({"coll": "notes", "src": p, "head": head, "text": f"{label}\n{buf.strip()}"})
                    buf = ""
                for para in re.split(r"\n{2,}", body):
                    if len(buf) + len(para) > 900 and buf:
                        flush()
                    buf += ("\n\n" if buf else "") + para
                    while len(buf) > 1400:
                        out.append({"coll": "notes", "src": p, "head": head, "text": f"{label}\n{buf[:1400]}"})
                        buf = buf[1400:]
                flush()
            if len(out) > 20000:
                break
        return out

    def skill_chunks(self) -> list[dict]:
        return [{"coll": "skills", "src": s.path, "head": s.name, "text": f"{s.name}: {s.description}"} for s in self.jade.skills.list()]

    def reconcile(self, colls: list[str], want: list[dict], prefix: str = "") -> tuple[int, int]:
        db = self.open()
        q = ",".join("?" * len(colls))
        have = db.execute(f"SELECT id, coll, src, hash FROM chunks WHERE coll IN ({q})", colls).fetchall()
        rows = [{**w, "hash": vhash(w["head"] + "\u0001" + w["text"])} for w in want]
        want_keys = {}
        for w in rows:
            k = (w["coll"], w["src"], w["hash"])
            want_keys[k] = want_keys.get(k, 0) + 1
        have_keys = {}
        for i, c, s, h in have:
            have_keys.setdefault((c, s, h), []).append(i)
        added = removed = 0
        db.execute("BEGIN")
        for i, c, s, h in have:
            if prefix and not (s == prefix or s.startswith(prefix.rstrip("/") + "/")):
                continue
            if (c, s, h) not in want_keys:
                db.execute("DELETE FROM fts WHERE rowid = ?", (i,))
                db.execute("DELETE FROM chunks WHERE id = ?", (i,))
                if self.dim:
                    db.execute("DELETE FROM vec WHERE rowid = ?", (i,))
                removed += 1
        now = int(time.time() * 1000)
        for w in rows:
            k = (w["coll"], w["src"], w["hash"])
            if k in have_keys:
                continue
            cur = db.execute("INSERT INTO chunks(coll, src, head, text, hash, ts) VALUES (?, ?, ?, ?, ?, ?)", (w["coll"], w["src"], w["head"], w["text"], w["hash"], now))
            db.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, w["text"]))
            have_keys[k] = [cur.lastrowid]
            added += 1
        db.execute("COMMIT")
        return added, removed

    async def sync(self, notes: bool = False, notes_path: str = "") -> dict:
        async with self._lock:
            self.error = ""
            r = [self.reconcile(["memory"], self.memory_chunks()), self.reconcile(["journal"], self.journal_chunks()), self.reconcile(["skills"], self.skill_chunks())]
            if notes:
                r.append(self.reconcile(["notes"], self.note_chunks(notes_path), notes_path))
            embedded = 0
            try:
                embedded = await self.embed_pending()
            except Exception as e:
                self.error = str(e)
            self.dirty = False
            return {"added": sum(x[0] for x in r), "removed": sum(x[1] for x in r), "embedded": embedded, "error": self.error}

    # ---------------------------------------------------------------- search
    @staticmethod
    def fts_query(q: str) -> str:
        t = list(dict.fromkeys(tokens(q)))[:24]
        return " OR ".join('"' + x.replace('"', "") + '"*' for x in t)

    async def search(self, query: str, colls: list[str] | None = ("memory", "journal"), k: int = 8, mode: str = "hybrid", sync: bool = True) -> tuple[list[dict], str]:
        db = self.open()
        if colls is None:
            colls = [r[0] for r in db.execute("SELECT DISTINCT coll FROM chunks").fetchall() if r[0] not in ("skills", "chats")]
        colls = list(colls)
        if not colls:
            return [], "the index is empty"
        if sync and self.dirty:
            await self.sync()
        query = str(query or "").strip()
        if not query:
            return [], "empty query"
        n, ins = max(30, k * 6), ",".join("?" * len(colls))
        note, fr, vr, dist = "", {}, {}, {}
        if mode != "semantic":
            fq = self.fts_query(query)
            if fq:
                rows = db.execute(f"SELECT fts.rowid FROM fts JOIN chunks c ON c.id = fts.rowid WHERE fts MATCH ? AND c.coll IN ({ins}) ORDER BY fts.rank LIMIT ?", [fq, *colls, n]).fetchall()
                fr = {r[0]: i + 1 for i, r in enumerate(rows)}
        if mode != "keyword" and self.dim:
            try:
                (qv,) = await self.embed([query], "query")
                if len(qv) != self.dim:
                    raise ToolError(f"the embedding model now returns {len(qv)} numbers per text, the index has {self.dim}: rebuild the index")
                for c in colls:
                    for rid, d in db.execute("SELECT rowid, distance FROM vec WHERE embedding MATCH ? AND k = ? AND coll = ?", (_blob(qv), n, c)).fetchall():
                        dist[rid] = d
                vr = {rid: i + 1 for i, (rid, _) in enumerate(sorted(dist.items(), key=lambda x: x[1]))}
            except Exception as e:
                note = f"keyword search only ({e})"
                if mode == "semantic":
                    raise
        elif mode != "keyword" and not self.dim:
            note = "keyword search only (nothing is embedded yet" + (f": {self.error}" if self.error else "") + ")"
        score: dict[int, float] = {}
        for d in (fr, vr):
            for rid, r in d.items():
                score[rid] = score.get(rid, 0) + 1 / (RRF + r)
        ids = [i for i, _ in sorted(score.items(), key=lambda x: -x[1])[:k]]
        if not ids:
            return [], note
        rows = {r[0]: r for r in db.execute(f"SELECT id, coll, src, head, text FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()}
        hits = [{"id": i, "coll": rows[i][1], "src": rows[i][2], "head": rows[i][3], "text": rows[i][4], "score": score[i], "distance": dist.get(i), "kw": fr.get(i, 0), "sem": vr.get(i, 0)} for i in ids if i in rows]
        return hits, note

    async def memory_search(self, query: str, limit: int = 10) -> str:
        hits, note = await self.search(query, ["memory", "journal"], limit)
        if not hits:
            return ""
        txt = "\n".join(f"{'memory' if h['coll'] == 'memory' else 'journal'}{' · ' + h['head'] if h['head'] else ''}: {_squash(h['text'])[:300]}" for h in hits)
        return (f"[{note}]\n" if note else "") + txt

    def add(self, coll: str, text: str, src: str = "", head: str = "") -> tuple[int, bool]:
        db = self.open()
        text = str(text or "").strip()
        if not text:
            raise ToolError("add needs text")
        h = vhash(head + "\u0001" + text)
        dup = db.execute("SELECT id FROM chunks WHERE coll = ? AND hash = ?", (coll, h)).fetchone()
        if dup:
            return dup[0], False
        cur = db.execute("INSERT INTO chunks(coll, src, head, text, hash, ts) VALUES (?, ?, ?, ?, ?, ?)", (coll, src, head, text, h, int(time.time() * 1000)))
        db.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        return cur.lastrowid, True

    def remove(self, id: int = 0, coll: str = "", src: str = "") -> int:   # noqa: A002
        db = self.open()
        if id:
            where, params = "id = ?", [id]
        elif src:
            where, params = "src = ?" + (" AND coll = ?" if coll else ""), [src] + ([coll] if coll else [])
        elif coll:
            where, params = "coll = ?", [coll]
        else:
            raise ToolError("remove needs an id, a source or a collection")
        ids = [r[0] for r in db.execute(f"SELECT id FROM chunks WHERE {where}", params).fetchall()]
        db.execute("BEGIN")
        for i in ids:
            db.execute("DELETE FROM fts WHERE rowid = ?", (i,))
            db.execute("DELETE FROM chunks WHERE id = ?", (i,))
            if self.dim:
                db.execute("DELETE FROM vec WHERE rowid = ?", (i,))
        db.execute("COMMIT")
        return len(ids)

    def status(self) -> dict:
        db = self.open()
        rows = db.execute("SELECT coll, count(*), sum(emb) FROM chunks GROUP BY coll ORDER BY coll").fetchall()
        vec, sq = db.execute("select vec_version(), sqlite_version()").fetchone()
        st = self.jade.settings
        return {"sqlite": sq, "vec": vec, "model": self.model or self.model_name, "dim": self.dim, "host": st.embed_host or st.host,
                "collections": [{"name": r[0], "chunks": r[1], "embedded": r[2] or 0} for r in rows], "error": self.error}


# ---------------------------------------------------------------- the embeddings tool
async def embeddings_tool(ctx, action: str = "status", query: str = "", text: str = "", texts=None, collection: str = "", source: str = "", head: str = "",
                          path: str = "", mode: str = "hybrid", k: int = 8, limit: int = 0, id: int = 0, a: str = "", b: str = "", **_):  # noqa: A002
    e: Embeddings = ctx.jade.embeddings
    act = str(action or "status").lower()
    colls = [c.strip() for c in collection.split(",") if c.strip()] if collection else None
    if act == "status":
        s = e.status()
        size = f" ({s['dim']} numbers per text)" if s["dim"] else " (nothing embedded yet)"
        return (f"sqlite-vec {s['vec']} on SQLite {s['sqlite']}. Embedding model: {s['model']}{size} via {s['host']}.\n"
                + ("\n".join(f"- {c['name']}: {c['chunks']} texts, {c['embedded']} embedded" for c in s["collections"]) or "The index is empty.")
                + (f"\nLast problem: {s['error']}" if s["error"] else ""))
    if act in ("search", "find", "query"):
        q = query or text
        if not q:
            raise ToolError("search needs a query")
        hits, note = await e.search(q, colls, max(1, min(30, int(k or limit or 8))), mode if mode in ("hybrid", "semantic", "keyword") else "hybrid")
        if not hits:
            return f"Nothing found{' in ' + ', '.join(colls) if colls else ''}{f' ({note})' if note else ''}. Use the index action to embed the vault's notes first, or add text with the add action."
        lines = []
        for i, h in enumerate(hits):
            src = h["src"] if h["coll"] not in ("notes", "journal") else note_name(h["src"])
            where = (" · " + src if src and h["coll"] != "memory" else "") + (" › " + h["head"] if h["head"] and h["coll"] == "notes" else "")
            dist = f", distance {h['distance']:.3f}" if h["distance"] is not None else ""
            body = re.sub(r"\s+", " ", h["text"])[:400]
            lines.append(f"{i + 1}. [{h['coll']}{where}] (id {h['id']}{dist})\n   {body}")
        return (f"[{note}]\n" if note else "") + "\n".join(lines)
    if act in ("add", "store", "save"):
        coll = collection.strip()
        if not coll:
            raise ToolError("add needs a collection name")
        if coll in AUTO or coll in ("notes", "chats", "skills"):
            raise ToolError(f'"{coll}" is filled automatically; pick another collection name (or use the memory tool to remember a fact).')
        n = 0
        for t in (texts if isinstance(texts, list) else [text]):
            if str(t or "").strip() and e.add(coll, t, source, head)[1]:
                n += 1
        err = ""
        try:
            await e.embed_pending()
        except Exception as x:
            err = str(x)
        return f'Stored {n} text{"" if n == 1 else "s"} in "{coll}".' + (f" Not embedded yet ({err}); keyword search still finds them." if err else "")
    if act == "index":
        p = ctx.vault.norm(path) if path else ""
        r = await e.sync(notes=True, notes_path=p)
        n = next((c["chunks"] for c in e.status()["collections"] if c["name"] == "notes"), 0)
        return f"Indexed the vault{' under ' + p if p else ''}: {r['added']} new, {r['removed']} removed, {r['embedded']} embedded; \"notes\" holds {n} passages." + (f" Problem: {r['error']}" if r["error"] else "")
    if act in ("remove", "delete", "forget"):
        n = e.remove(int(id or 0), collection, source or path)
        return f"Removed {n} text{'' if n == 1 else 's'}."
    if act in ("compare", "similar", "similarity"):
        if not a or not b:
            raise ToolError("compare needs texts a and b")
        x, y = await e.embed([a, b])
        return f"Cosine similarity {sum(i * j for i, j in zip(x, y)):.3f} (1 = same meaning, near 0 = unrelated) using {e.model_name}."
    raise ToolError(f'unknown action "{act}" (use search, add, index, remove, compare or status)')


def setup(jade: "Jade") -> None:
    jade.embeddings = Embeddings(jade)
    spec = jade.specs["embeddings"]
    jade.tools.add(Tool("embeddings", spec["description"].replace("embeddings from Ollama", "embeddings from your model server"), spec["parameters"], embeddings_tool,
                        core=False, hints="embedding vector semantic similar index meaning meanings related", enabled=lambda j: j.settings.embed_on,
                        summary="embeddings: search by meaning with sqlite-vec (memory, journal, the vault after index, and collections of your own)"))
    _ = json
