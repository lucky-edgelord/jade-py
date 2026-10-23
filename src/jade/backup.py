"""Scheduled backup to a Jade backup server (jade.backup_server, the same server the app uses), on demand, and to a
file. The backup is the app's format 1 ({"app": "jade-vault", "format": 1, "kind", "settings", "vault": {"files",
"dirs"}, …}), gzip-compressed, optionally encrypted the same way as the app (AES-256-GCM, key from the passphrase with
PBKDF2-SHA256, 210 000 rounds), so the app can restore a backup made here and the other way round.

Settings: ab_on, ab_url, ab_token, ab_hours, ab_kind (full|config), ab_keys (include API keys; forces encryption),
ab_encrypt, ab_pass. Jade can check and start a backup (the backup tool); only the user restores
(`jade backup restore`, or Backups(jade).restore(...))."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

log = logging.getLogger("jade")
ITER = 210_000
SECRET_KEYS = ("api_key", "ollama_key", "ollaya_key", "api_token", "ab_token", "ab_pass", "secrets", "ssh_hosts")
NESTED_SECRETS = {"vector": ("token",), "discord": ("token",)}
SKIP = re.compile(r"^/Jade/(\.index|\.data)(/|$)|(^|/)(\.git|node_modules|\.trash)(/|$)|\.jvtmp$")
CONFIG_PATH = re.compile(r"^/jade/(soul|system|agents|memory|heartbeat|curiosity)\.md$|^/jade/mcp\.json$|^/jade/skills/|^/agents\.md$", re.I)


def _key(passphrase: str, salt: bytes, iters: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iters, 32)


def encrypt(data: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt, ITER)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode()   # noqa: E731
    return json.dumps({"app": "jade-vault-encrypted", "v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b64(salt), "iv": b64(iv), "data": b64(ct)}).encode()


def decrypt(env: dict, passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not passphrase:
        raise ValueError("This backup is encrypted: a passphrase is needed.")
    try:
        key = _key(passphrase, base64.b64decode(env["salt"]), int(env.get("iter") or ITER))
        return AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["data"]), None)
    except (InvalidTag, KeyError, ValueError):
        raise ValueError("Wrong passphrase, or the backup is damaged.") from None


def decode(raw: bytes, passphrase: str = "") -> dict:
    """Bytes of a backup (plain JSON, gzip, or an encrypted envelope) -> the backup object."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict) and data.get("app") == "jade-vault-encrypted":
        inner = decrypt(data, passphrase)
        return json.loads((gzip.decompress(inner) if inner[:2] == b"\x1f\x8b" else inner).decode("utf-8"))
    return data


class Backups:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.running = False

    def cfg(self) -> dict:
        s = self.jade.settings
        return {"on": s.ab_on, "url": s.ab_url.strip().rstrip("/"), "token": s.ab_token, "hours": max(1, s.ab_hours or 24), "kind": s.ab_kind,
                "keys": s.ab_keys, "encrypt": s.ab_encrypt or s.ab_keys, "pass": s.ab_pass}

    # ---- making
    def build(self, kind: str = "full", with_keys: bool = False) -> dict:
        j = self.jade
        settings = j.settings.model_dump(mode="json")
        if not with_keys:
            for k in SECRET_KEYS:
                settings.pop(k, None)
            for k, subs in NESTED_SECRETS.items():
                for sk in subs:
                    (settings.get(k) or {}).pop(sk, None)
        files: dict = {}
        dirs: list[str] = []
        root = str(j.vault.root)
        for dp, dns, fns in os.walk(root):
            vdp = "/" + os.path.relpath(dp, root).replace(os.sep, "/") if dp != root else "/"
            vdp = vdp.replace("//", "/")
            dns[:] = sorted(d for d in dns if not SKIP.search(f"{vdp.rstrip('/')}/{d}"))
            for d in dns:
                if kind == "full":
                    dirs.append(f"{vdp.rstrip('/')}/{d}")
            for fn in sorted(fns):
                p = f"{vdp.rstrip('/')}/{fn}"
                if SKIP.search(p) or (kind == "config" and not CONFIG_PATH.search(p)):
                    continue
                try:
                    b = Path(dp, fn).read_bytes()
                except OSError:
                    continue
                try:
                    if b"\x00" in b:
                        raise UnicodeDecodeError("utf-8", b, 0, 1, "nul")
                    files[p] = b.decode("utf-8")
                except UnicodeDecodeError:
                    files[p] = {"base64": base64.b64encode(b).decode()}
        out = {"app": "jade-vault", "format": 1, "kind": kind, "exportedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "includesKeys": bool(with_keys), "settings": settings, "storage": {}, "vault": {"files": files, "dirs": sorted(dirs)}}
        if kind == "full":
            chats = {}
            for c in j.chats():
                try:
                    chats[c["id"]] = json.loads((j.settings.data_path / "chats" / f"{c['id']}.json").read_text())
                except (OSError, ValueError):
                    pass
            out["jadePy"] = {"chats": chats, "state": j.state}
        return out

    def make(self, c: dict | None = None) -> dict:
        c = c or self.cfg()
        data = self.build(c["kind"], c["keys"])
        raw = gzip.compress(json.dumps(data, ensure_ascii=False).encode(), 6)
        ext = ".json.gz"
        if c["encrypt"]:
            if not c["pass"]:
                raise ToolError("Encryption is on but there is no passphrase (ab_pass).")
            raw, ext = encrypt(raw, c["pass"]), ".enc.json"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        return {"name": f"jade-vault-{c['kind']}-{stamp}{ext}", "bytes": raw, "files": len(data["vault"]["files"])}

    # ---- the server
    async def _call(self, c: dict, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None, timeout: float = 120) -> httpx.Response:
        if not re.match(r"^https?://[^/\s]+", c["url"]):
            raise ToolError("The server address (ab_url) must start with http:// or https://.")
        if not c["token"]:
            raise ToolError("There is no token (ab_token).")
        try:
            async with httpx.AsyncClient(timeout=timeout) as h:
                r = await h.request(method, c["url"] + path, content=body, headers={"Authorization": "Bearer " + c["token"], **(headers or {})})
        except httpx.TimeoutException:
            raise ToolError(f"no answer from {c['url']} in {timeout:g}s") from None
        except httpx.HTTPError as e:
            raise ToolError(f"could not reach {c['url']} ({e})") from None
        if r.status_code >= 400:
            try:
                m = r.json().get("error", "")
            except ValueError:
                m = ""
            raise ToolError(f"the server said {r.status_code}{': ' + m if m else ''}")
        return r

    def _status(self, **o) -> dict:
        st = {"at": time.time(), **o}
        self.jade.state["ab_status"] = st
        self.jade.save_state()
        return st

    async def run(self, manual: bool = False) -> dict:
        if self.running:
            return {"ok": False, "msg": "a backup is already running"}
        c = self.cfg()
        self.running = True
        S = self.jade.state
        try:
            m = self.make(c)
            sha = hashlib.sha256(m["bytes"]).hexdigest()
            from urllib.parse import quote
            r = await self._call(c, "PUT", "/backups/" + quote(m["name"]), body=m["bytes"], headers={"Content-Type": "application/octet-stream", "X-Sha256": sha})
            try:
                j = r.json()
            except ValueError:
                j = {}
            if j.get("sha256") and j["sha256"] != sha:
                raise ToolError("the server stored different bytes than were sent")
            S.update(ab_last=time.time(), ab_fails=0, ab_retry_at=0)
            return self._status(ok=True, name=m["name"], size=len(m["bytes"]), files=m["files"], encrypted=c["encrypt"],
                                msg=f"{_size(len(m['bytes']))} uploaded{' (encrypted)' if c['encrypt'] else ''}")
        except Exception as e:
            n = int(S.get("ab_fails") or 0) + 1
            S.update(ab_fails=n, ab_retry_at=time.time() + min(6 * 3600, 15 * 60 * 2 ** (n - 1)))
            self._status(ok=False, msg=str(e))
            return {"ok": False, "msg": str(e)}
        finally:
            self.running = False

    async def list(self) -> list[dict]:
        r = await self._call(self.cfg(), "GET", "/backups", timeout=30)
        return r.json().get("backups") or []

    async def fetch(self, name: str) -> bytes:
        from urllib.parse import quote
        r = await self._call(self.cfg(), "GET", "/backups/" + quote(name))
        want = r.headers.get("X-Sha256")
        if want and want != hashlib.sha256(r.content).hexdigest():
            raise ToolError("the download was damaged (checksum mismatch); nothing was restored")
        return r.content

    # ---- restoring (the user's call, never Jade's)
    def restore(self, data: dict, *, overwrite: bool = True, settings: bool = False) -> dict:
        """Write a backup's files into the vault (and chats, for one made here). Nothing is deleted."""
        if not (isinstance(data, dict) and data.get("app") == "jade-vault" and isinstance(data.get("vault"), dict)):
            raise ValueError("this is not a Jade Vault backup")
        v = self.jade.vault
        n = skipped = 0
        for d in data["vault"].get("dirs") or []:
            try:
                v.mkdir(d)
            except (OSError, ValueError):
                pass
        for p, body in (data["vault"].get("files") or {}).items():
            if SKIP.search(p) or (not overwrite and v.exists(p)):
                skipped += 1
                continue
            v.write(p, base64.b64decode(body["base64"]) if isinstance(body, dict) else str(body))
            n += 1
        chats = ((data.get("jadePy") or {}).get("chats") or {})
        folder = self.jade.settings.data_path / "chats"
        folder.mkdir(parents=True, exist_ok=True)
        for cid, c in chats.items():
            if re.fullmatch(r"[\w-]{1,64}", str(cid)) and (overwrite or not (folder / f"{cid}.json").exists()):
                (folder / f"{cid}.json").write_text(json.dumps(c, ensure_ascii=False))
        if settings and isinstance(data.get("settings"), dict):
            keep = {k: getattr(self.jade.settings, k) for k in SECRET_KEYS if k not in data["settings"]}
            known = {k: v_ for k, v_ in data["settings"].items() if k in type(self.jade.settings).model_fields and k not in ("vault", "data_dir")}
            self.jade.settings = self.jade.settings.model_copy(update={**known, **keep})
        return {"files": n, "skipped": skipped, "chats": len(chats)}

    async def loop(self, daemon) -> None:
        while not daemon.stopping.is_set():
            try:
                c = self.cfg()
                S = self.jade.state
                cu = getattr(self.jade, "curiosity", None)
                if c["on"] and c["url"] and c["token"] and not self.running and not (cu and cu.running) \
                        and time.time() >= (S.get("ab_last") or 0) + c["hours"] * 3600 and time.time() >= (S.get("ab_retry_at") or 0):
                    r = await self.run()
                    if not r["ok"]:
                        log.warning("backup failed: %s", r["msg"])
            except Exception as e:
                log.warning("backup: %s", e)
            if await daemon._sleep(60):
                return


def _size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)


async def backup_tool(ctx, action: str = "status", **kw):
    B: Backups = ctx.jade.backups
    a = str(action or "status").lower()
    c = B.cfg()
    S = ctx.jade.state
    if a == "status":
        s, last = S.get("ab_status"), S.get("ab_last")
        return (f"Scheduled backup: {'on' if c['on'] else 'off'}, every {c['hours']} h, {c['kind']}{', encrypted' if c['encrypt'] else ''}{', with keys' if c['keys'] else ''} "
                f"to {c['url'] or '(no server set)'}. Last success: {datetime.fromtimestamp(last).strftime('%Y-%m-%d %H:%M') if last else 'never'}. "
                f"Last attempt: {(('FAILED: ' if s.get('ok') is False else '') + s.get('msg', '')) if s else 'none'}.")
    if a == "now":
        r = await B.run()
        if not r["ok"]:
            raise ToolError(r["msg"])
        return f"Backup uploaded: {r['name']} ({r['msg']})."
    if a == "list":
        rows = await B.list()
        return "\n".join(f"{b['name']}  {_size(b.get('size', 0))}  {datetime.fromtimestamp(b.get('modified', 0)).strftime('%Y-%m-%d %H:%M')}" for b in rows) or "The server has no backups yet."
    raise ToolError(f"unknown action {a}. Use status, now or list. Restoring is something only the user can do (jade backup restore).")


def setup(jade: "Jade") -> None:
    jade.backups = Backups(jade)
    spec = jade.specs["backup"]
    jade.tools.add(Tool("backup", spec["description"], spec["parameters"], backup_tool, core=False, hints="backup back up server restore snapshot",
                        summary="backup: the scheduled vault backup to the user's server (status, now, list)"))
    jade.daemon_loops.append(lambda daemon: jade.backups.loop(daemon))
