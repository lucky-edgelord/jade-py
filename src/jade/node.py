"""Real Node.js for what the app ran in its JavaScript sandbox:
  - flow expressions ({{ $json.x }}, conditions): a long-lived node process evaluating each one in an isolated
    vm context with a 5 s limit, no require, no network;
  - JavaScript steps and scripts: a node process per script, with the app's globals ($json, $input…), fs rooted at
    the vault (as in the app; realfs is the real one), fetch, and jade.* calling back into Python over a pipe."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from .core import Jade

JV_DATE = r"""function $date(fmt, d, days) {
  fmt = fmt == null || fmt === '' ? 'YYYY-MM-DD' : String(fmt);
  let t = d instanceof Date ? new Date(d.getTime()) : d == null || d === '' ? new Date() : new Date(typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d + 'T00:00:00' : d);
  if (isNaN(t.getTime())) t = new Date();
  if (days) t.setDate(t.getDate() + Number(days));
  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const p = (n, w = 2) => String(n).padStart(w, '0'); const h = t.getHours(), D = t.getDate();
  const iso = (() => { const x = new Date(Date.UTC(t.getFullYear(), t.getMonth(), D)); const dn = x.getUTCDay() || 7; x.setUTCDate(x.getUTCDate() + 4 - dn); const y0 = new Date(Date.UTC(x.getUTCFullYear(), 0, 1)); return Math.ceil(((x - y0) / 864e5 + 1) / 7); })();
  const ord = n => n + (n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th');
  const map = { YYYY: t.getFullYear(), YY: p(t.getFullYear() % 100), MMMM: MONTHS[t.getMonth()], MMM: MONTHS[t.getMonth()].slice(0, 3), MM: p(t.getMonth() + 1), M: t.getMonth() + 1, Do: ord(D), DD: p(D), D,
    dddd: DAYS[t.getDay()], ddd: DAYS[t.getDay()].slice(0, 3), d: t.getDay(), HH: p(h), H: h, hh: p(h % 12 || 12), h: h % 12 || 12, mm: p(t.getMinutes()), m: t.getMinutes(), ss: p(t.getSeconds()), s: t.getSeconds(),
    A: h < 12 ? 'AM' : 'PM', a: h < 12 ? 'am' : 'pm', ww: p(iso), w: iso, X: Math.floor(t.getTime() / 1000), x: t.getTime() };
  return fmt.replace(/\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|Do|DD|dddd|ddd|HH|hh|mm|ss|ww|M|D|d|H|h|m|s|A|a|w|X|x/g, (m, lit) => lit !== undefined ? lit : String(map[m]));
}"""

EVAL_JS = r"""
const vm = require('vm');
""" + JV_DATE + r"""
const str = v => v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
const cache = new Map();
const script = src => { let s = cache.get(src); if (!s) { s = new vm.Script('(' + src + '\n)'); if (cache.size > 500) cache.clear(); cache.set(src, s); } return s; };
let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => {
  buf += d; let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line.trim()) continue;
    const { id, jobs, ctx } = JSON.parse(line); const out = []; let k = 0;
    try {
      const sandbox = vm.createContext({ ...ctx, $date, JSON, Math, Date, String, Number, Array, Object, Boolean, RegExp, parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent });
      const run = src => script(src).runInContext(sandbox, { timeout: 5000 });
      for (; k < jobs.length; k++) {
        const j = jobs[k];
        if (j.mode === 'expr') out.push(run(j.src));
        else {
          const one = /^\s*\{\{([\s\S]+?)\}\}\s*$/.exec(j.src);
          if (one && j.src.indexOf('{{') === j.src.lastIndexOf('{{')) out.push(run(one[1]));
          else out.push(j.src.replace(/\{\{([\s\S]+?)\}\}/g, (_, e) => str(run(e))));
        }
      }
      process.stdout.write(JSON.stringify({ id, ok: true, out: JSON.parse(JSON.stringify(out, (kk, v) => v === undefined ? null : v)) }) + '\n');
    } catch (err) {
      process.stdout.write(JSON.stringify({ id, ok: false, error: String((err && err.message) || err), src: jobs[k] && jobs[k].src }) + '\n');
    }
  }
});
"""

SCRIPT_JS = r"""
const __rfs = require('fs'), path = require('path');
const __VAULT = process.env.JADE_VAULT;
const __req = __rfs.createWriteStream(null, { fd: +process.env.JADE_RPC_FD });
let __seq = 0; const __wait = new Map(); let __buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { __buf += d; let i; while ((i = __buf.indexOf('\n')) >= 0) { const l = __buf.slice(0, i); __buf = __buf.slice(i + 1); if (!l.trim()) continue; const m = JSON.parse(l); const w = __wait.get(m.id); if (w) { __wait.delete(m.id); m.err ? w.no(new Error(m.err)) : w.ok(m.r); } } });
const __call = (op, args) => new Promise((ok, no) => { const id = ++__seq; __wait.set(id, { ok, no }); __req.write(JSON.stringify({ t: 'rpc', id, op, args: args || {} }) + '\n'); });
const jade = {
  call: __call, tool: (name, args) => __call('tool', { name, args }), list: () => __call('list'), decide: a => __call('decide', a), ask: (prompt, o = {}) => __call('ask', { ...o, prompt }),
  agent: (message, o = {}) => __call('agent', { ...o, message }), flow: (name, input) => __call('flow', { name, input }), flows: () => __call('flows'), shell: command => __call('shell', { command }),
  notify: (title, body) => __call('notify', { title, body }), kv: { get: key => __call('kv', { op: 'get', key }), set: (key, value) => __call('kv', { op: 'set', key, value }), list: () => __call('kv', { op: 'list' }) },
};
jade.tools = new Proxy({}, { get: (_, n) => a => jade.tool(String(n), a) });
// fs rooted at the vault, like the app's sandbox ("/Inbox.md" is the vault's); realfs is the real filesystem
const __map = p => typeof p === 'string' && p.startsWith('/') && !(p === __VAULT || p.startsWith(__VAULT + '/')) ? path.join(__VAULT, p) : p;
const fs = new Proxy(__rfs, { get(t, k) { const v = t[k]; if (typeof v !== 'function') return v; if (k === 'promises') return v; return (...a) => { if (a.length) a[0] = __map(a[0]); if (/^(rename|copyFile|cp|link|symlink)(Sync)?$/.test(k) && a.length > 1) a[1] = __map(a[1]); return v.apply(t, a); }; } });
const realfs = __rfs;
Object.assign(globalThis, JSON.parse(process.env.JADE_GLOBALS || '{}'), { $date: """ + JV_DATE.replace("function $date", "function") + r""" });
(async () => {
  try {
    const __ret = await (async () => { %CODE%
    })();
    __req.write(JSON.stringify({ t: 'ret', value: __ret === undefined ? null : __ret }) + '\n');
    __req.end(() => process.exit(0));
  } catch (e) { process.stderr.write(String((e && e.stack) || e) + '\n'); __req.end(() => process.exit(1)); }
})();
"""


class NodeMissing(RuntimeError):
    pass


def node_path() -> str:
    p = shutil.which("node") or shutil.which("nodejs")
    if not p:
        raise NodeMissing("this needs Node.js (flows use JavaScript): install it, e.g. sudo apt install nodejs")
    return p


class Evaluator:
    """Flow expressions in a long-lived node process."""

    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.seq = 0
        self.lock = asyncio.Lock()

    async def _start(self):
        self.proc = await asyncio.create_subprocess_exec(node_path(), "-e", EVAL_JS, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def run(self, jobs: list[dict], ctx: dict) -> list[Any]:
        if not jobs:
            return []
        async with self.lock:
            if not self.proc or self.proc.returncode is not None:
                await self._start()
            self.seq += 1
            msg = json.dumps({"id": self.seq, "jobs": jobs, "ctx": ctx}, default=str) + "\n"
            self.proc.stdin.write(msg.encode())
            await self.proc.stdin.drain()
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), 15)
            except asyncio.TimeoutError:
                self.proc.kill()
                self.proc = None
                raise RuntimeError("an expression ran longer than 5 s (an endless loop?)") from None
            if not line:
                err = (await self.proc.stderr.read()).decode(errors="replace")[-300:]
                self.proc = None
                raise RuntimeError(f"expression error: {err or 'node stopped'}")
            r = json.loads(line)
            if not r["ok"]:
                src = r.get("src")
                raise RuntimeError(f"{'{{ ' + str(src).strip()[:80] + ' }}: ' if src else ''}{r['error']}")
            return r["out"]

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


async def run_script(jade: "Jade", code: str, *, globals_: dict | None = None, timeout: float = 60, rpc: Callable[[str, dict], Awaitable[Any]] | None = None) -> dict:
    """Run JavaScript like a flow step: returns {code, out, err, ret} (ret = what the code returned)."""
    src = SCRIPT_JS.replace("%CODE%", code)
    r_fd, w_fd = os.pipe()
    env = {**os.environ, "JADE_VAULT": str(jade.vault.root), "JADE_GLOBALS": json.dumps(globals_ or {}, default=str), "JADE_RPC_FD": str(w_fd), **jade.settings.secrets}
    proc = await asyncio.create_subprocess_exec(node_path(), "-e", src, cwd=str(jade.vault.root), env=env, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, pass_fds=(w_fd,))
    os.close(w_fd)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=2**24)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(r_fd, "rb"))
    out, err, ret = [], [], None

    async def pump(stream, sink):
        while chunk := await stream.read(65536):
            sink.append(chunk.decode(errors="replace"))

    async def requests():
        nonlocal ret
        while line := await reader.readline():
            m = json.loads(line)
            if m.get("t") == "ret":
                ret = m.get("value")
            elif m.get("t") == "rpc":
                try:
                    if not rpc:
                        raise RuntimeError("jade.* is not available here")
                    resp = {"id": m["id"], "r": await rpc(m["op"], m.get("args") or {})}
                except Exception as e:
                    resp = {"id": m["id"], "err": str(e)}
                proc.stdin.write((json.dumps(resp, default=str) + "\n").encode())
                await proc.stdin.drain()

    try:
        await asyncio.wait_for(asyncio.gather(pump(proc.stdout, out), pump(proc.stderr, err), requests(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"code": 124, "out": "".join(out), "err": "".join(err) + f"\ntimed out after {timeout:g}s", "ret": None}
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    return {"code": proc.returncode, "out": "".join(out), "err": "".join(err), "ret": ret}
