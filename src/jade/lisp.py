"""Jade Lisp: a small Scheme/Clojure-style Lisp for DSLs and for scripting Jade's tools. A Python port of the app's
interpreter (/Jade/tools/lisp.js in Jade Vault 2.26): the same language, the same builtins, the same answers (both pass
the shared cases in tests/fixtures/lisp_cases.json).

The evaluator is an explicit-stack machine, not recursive Python: deep recursion never hits Python's recursion limit,
tail calls run in constant space, and a call into Jade (a tool, ask, a flow, a file...) pauses the machine, is awaited
by the async driver (``run``), and resumes with the answer. Higher-order functions (map, filter, sort with a comparator,
...) are written in Lisp in the prelude, so they can call anything, including Jade.

    from jade.lisp import Lisp
    lisp = Lisp(host=my_async_host)          # host(op, args) -> value; None for pure Lisp
    value = await lisp.run("(+ 1 2)")        # lisp.output() has what it printed
"""
from __future__ import annotations

import json
import math
import random
import re
from typing import Any, Awaitable, Callable

VERSION = "1.0.0"
Host = Callable[[str, dict], Awaitable[Any]]


# ---------------------------------------------------------------- values
class Sym:
    __slots__ = ("name",)
    _t: dict[str, "Sym"] = {}

    def __new__(cls, name: str):
        s = cls._t.get(name)
        if s is None:
            s = object.__new__(cls)
            s.name = name
            cls._t[name] = s
        return s

    def __repr__(self):
        return self.name


class Kw:
    __slots__ = ("name",)
    _t: dict[str, "Kw"] = {}

    def __new__(cls, name: str):
        s = cls._t.get(name)
        if s is None:
            s = object.__new__(cls)
            s.name = name
            cls._t[name] = s
        return s

    def __repr__(self):
        return ":" + self.name


class Form(list):
    """A list read from source: remembers its line for error messages."""
    __slots__ = ("line",)

    def __init__(self, items=(), line: int = 0):
        super().__init__(items)
        self.line = line


class Brackets(Form):
    """[a b c]: a list literal when evaluated; let bindings and parameter lists read it as a plain list."""
    __slots__ = ()


class Fn:
    __slots__ = ("name", "params", "rest", "body", "env")

    def __init__(self, name, params, rest, body, env):
        self.name, self.params, self.rest, self.body, self.env = name, params, rest, body, env


class Macro(Fn):
    __slots__ = ()


class Prim:
    """A builtin. ctl=True builtins return a control step (they evaluate or call something) instead of a value."""
    __slots__ = ("name", "fn", "lo", "hi", "ctl")

    def __init__(self, name, fn, lo=0, hi=None, ctl=False):
        self.name, self.fn, self.lo, self.hi, self.ctl = name, fn, lo, hi, ctl


class Tool:
    """tool/NAME: calling it calls Jade's tool NAME."""
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


class Env:
    __slots__ = ("v", "parent")

    def __init__(self, parent: "Env | None" = None):
        self.v: dict[Sym, Any] = {}
        self.parent = parent

    def get(self, s: Sym, d=None):
        e = self
        while e is not None:
            if s in e.v:
                return e.v[s]
            e = e.parent
        return d

    def has(self, s: Sym) -> bool:
        e = self
        while e is not None:
            if s in e.v:
                return True
            e = e.parent
        return False

    def set(self, s: Sym, x) -> bool:
        e = self
        while e is not None:
            if s in e.v:
                e.v[s] = x
                return True
            e = e.parent
        return False


class LispError(Exception):
    def __init__(self, msg: str, value=None, kind: str = "error"):
        super().__init__(msg)
        self.value = msg if value is None else value
        self.kind = kind          # error | fuel | limit | read
        self.line = 0
        self.file = ""


_MISSING = object()
NIL = None


def is_map(v) -> bool:
    return isinstance(v, dict)


def truthy(v) -> bool:
    return v is not None and v is not False


def S(x, n) -> bool:
    return isinstance(x, Sym) and x.name == n


# ---------------------------------------------------------------- reader
_DELIM = set("()[]{}\"';`,")
_NUM = re.compile(r"^[+-]?(\d[\d_]*\.?[\d_]*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$")


def atom(t: str):
    if t in ("#t", "true"):
        return True
    if t in ("#f", "false"):
        return False
    if t in ("nil", "null"):
        return None
    if _NUM.match(t):
        s = t.replace("_", "")
        try:
            return int(s)
        except ValueError:
            f = float(s)
            return f
    if re.match(r"^[+-]?0x[0-9a-fA-F]+$", t):
        return int(t, 16)
    if len(t) > 1 and t[0] == ":":
        return Kw(t[1:])
    return Sym(t)


def read(src: str, file: str = "") -> list:
    out: list = []
    s = src
    n = len(s)
    st = {"i": 0, "line": 1}
    SKIP = object()

    def where():
        return (file + ":" if file else "line ") + str(st["line"])

    def fail(m):
        raise LispError(f"read error at {where()}: {m}", kind="read")

    def ws():
        while st["i"] < n:
            c = s[st["i"]]
            if c == "\n":
                st["line"] += 1
                st["i"] += 1
            elif c in " \t\r":
                st["i"] += 1
            elif c == ";":
                while st["i"] < n and s[st["i"]] != "\n":
                    st["i"] += 1
            elif c == "#" and st["i"] + 1 < n and s[st["i"] + 1] == "|":
                l0 = st["line"]
                st["i"] += 2
                while st["i"] < n and not (s[st["i"]] == "|" and st["i"] + 1 < n and s[st["i"] + 1] == "#"):
                    if s[st["i"]] == "\n":
                        st["line"] += 1
                    st["i"] += 1
                if st["i"] >= n:
                    st["line"] = l0
                    fail("#| comment is never closed with |#")
                st["i"] += 2
            elif c.isspace():
                st["i"] += 1
            else:
                return

    def seq(close, opn, cls):
        l0 = st["line"]
        v = cls(line=l0)
        while True:
            ws()
            if st["i"] >= n:
                st["line"] = l0
                fail(f"{opn} is never closed (add {close})")
            c = s[st["i"]]
            if c == close:
                st["i"] += 1
                return v
            if c in ")]}":
                fail(f"expected {close} to close the {opn} from line {l0}, found {c}")
            f = form()
            if f is not SKIP:
                v.append(f)

    def must():
        while True:
            ws()
            if st["i"] >= n or s[st["i"]] in ")]}":
                fail("a quote needs a form after it")
            f = form()
            if f is not SKIP:
                return f

    def tag(h, x, l0):
        return Form([Sym(h), x], line=l0)

    def form():
        ws()
        if st["i"] >= n:
            fail("unexpected end of input")
        c = s[st["i"]]
        l0 = st["line"]
        if c == "(":
            st["i"] += 1
            return seq(")", "(", Form)
        if c == "[":
            st["i"] += 1
            return seq("]", "[", Brackets)
        if c == "{":
            st["i"] += 1
            v = seq("}", "{", Form)
            v.insert(0, Sym("hash-map"))
            return v
        if c in ")]}":
            fail(f"unexpected {c}")
        if c == "#" and st["i"] + 1 < n and s[st["i"] + 1] == ";":
            st["i"] += 2
            must()
            return SKIP
        if c == "'":
            st["i"] += 1
            return tag("quote", must(), l0)
        if c == "`":
            st["i"] += 1
            return tag("quasiquote", must(), l0)
        if c == ",":
            st["i"] += 1
            if st["i"] < n and s[st["i"]] == "@":
                st["i"] += 1
                return tag("unquote-splicing", must(), l0)
            return tag("unquote", must(), l0)
        if c == '"':
            st["i"] += 1
            buf = []
            while True:
                if st["i"] >= n:
                    st["line"] = l0
                    fail("string is never closed")
                ch = s[st["i"]]
                st["i"] += 1
                if ch == '"':
                    return "".join(buf)
                if ch == "\n":
                    st["line"] += 1
                if ch != "\\":
                    buf.append(ch)
                    continue
                e = s[st["i"]] if st["i"] < n else ""
                st["i"] += 1
                if e == "n":
                    buf.append("\n")
                elif e == "t":
                    buf.append("\t")
                elif e == "r":
                    buf.append("\r")
                elif e == "0":
                    buf.append("\0")
                elif e == "u":
                    h = s[st["i"]:st["i"] + 4]
                    if not re.fullmatch(r"[0-9a-fA-F]{4}", h):
                        fail("\\u needs 4 hex digits")
                    buf.append(chr(int(h, 16)))
                    st["i"] += 4
                elif e == "\n":
                    st["line"] += 1
                    while st["i"] < n and s[st["i"]] in " \t":
                        st["i"] += 1
                else:
                    buf.append(e)
        j = st["i"]
        while st["i"] < n and not (s[st["i"]].isspace() or s[st["i"]] in _DELIM):
            st["i"] += 1
        return atom(s[j:st["i"]])

    while True:
        ws()
        if st["i"] >= n:
            break
        f = form()
        if f is not SKIP:
            out.append(f)
    return out


# ---------------------------------------------------------------- printer
def fmt_num(v) -> str:
    if isinstance(v, float):
        if math.isnan(v):
            return "+nan.0"
        if math.isinf(v):
            return "+inf.0" if v > 0 else "-inf.0"
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        r = repr(v)
        if "e" in r:   # JavaScript writes 1e-7, 1.5e+21
            m, e = r.split("e")
            e = int(e)
            r = f"{m}e{'+' if e > 0 else '-'}{abs(e)}"
        return r
    return str(v)


def show(v, w: bool = True, d: int = 0) -> str:
    if d > 200:
        return "..."
    if v is None:
        return "nil"
    if v is True:
        return "#t"
    if v is False:
        return "#f"
    if isinstance(v, (int, float)):
        return fmt_num(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if w else v
    if isinstance(v, Sym):
        return v.name
    if isinstance(v, Kw):
        return ":" + v.name
    if isinstance(v, list):
        if w and len(v) == 2 and isinstance(v[0], Sym):
            q = {"quote": "'", "quasiquote": "`", "unquote": ",", "unquote-splicing": ",@"}.get(v[0].name)
            if q:
                return q + show(v[1], w, d + 1)
        return "(" + " ".join(show(x, w, d + 1) for x in v) + ")"
    if isinstance(v, Macro):
        return f"#<macro {v.name}>"
    if isinstance(v, Fn):
        return f"#<fn {v.name}>"
    if isinstance(v, Env):
        return "#<env>"
    if isinstance(v, Prim):
        return f"#<builtin {v.name}>"
    if isinstance(v, Tool):
        return f"#<tool {v.name}>"
    if isinstance(v, dict):
        return "{" + " ".join(":" + str(k) + " " + show(x, w, d + 1) for k, x in v.items()) + "}"
    return f"#<py {type(v).__name__}>"


def type_of(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "integer" if v.is_integer() else "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, Sym):
        return "symbol"
    if isinstance(v, Kw):
        return "keyword"
    if isinstance(v, list):
        return "list"
    if isinstance(v, Macro):
        return "macro"
    if isinstance(v, (Fn, Prim, Tool)):
        return "function"
    if isinstance(v, Env):
        return "env"
    if isinstance(v, dict):
        return "map"
    return "py"


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def equal(a, b) -> bool:
    if a is b:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if is_num(a) and is_num(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def key_name(k) -> str:
    if isinstance(k, (Kw, Sym)):
        return k.name
    if isinstance(k, str):
        return k
    if k is None:
        return "nil"
    if k is True:
        return "true"
    if k is False:
        return "false"
    return fmt_num(k) if is_num(k) else show(k)


def to_py(v, d: int = 0):
    """Lisp value -> plain JSON-able Python (keywords and symbols become their names)."""
    if d > 200:
        raise LispError("value is nested too deeply")
    if isinstance(v, (Kw, Sym)):
        return v.name
    if isinstance(v, list):
        return [to_py(x, d + 1) for x in v]
    if isinstance(v, dict):
        return {k: to_py(x, d + 1) for k, x in v.items()}
    if isinstance(v, (Fn, Prim, Tool, Env)):
        raise LispError(f"a {type_of(v)} cannot be passed as data")
    return v


def from_py(v):
    """Python data from Jade -> Lisp (tuples become lists; other objects are kept)."""
    if isinstance(v, tuple):
        return [from_py(x) for x in v]
    if isinstance(v, list):
        return [from_py(x) for x in v]
    if isinstance(v, dict):
        return {str(k): from_py(x) for k, x in v.items()}
    return v


def seq_of(v, who):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return list(v)
    if isinstance(v, dict):
        return [[Kw(k), x] for k, x in v.items()]
    raise LispError(f"{who}: expected a list, got {show(v)}")


def num(v, who):
    if not is_num(v):
        raise LispError(f"{who}: expected a number, got {show(v)}")
    return v


def strv(v, who) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (Sym, Kw)):
        return v.name
    raise LispError(f"{who}: expected a string, got {show(v)}")


def get(m, k, d=None):
    if m is None:
        return d
    if isinstance(m, (list, str)):
        if is_num(k) and float(k).is_integer() and 0 <= int(k) < len(m):
            return m[int(k)]
        return d
    if isinstance(m, dict):
        n = key_name(k)
        return m[n] if n in m else d
    return d


def cmp(a, b) -> int:
    if is_num(a) and is_num(b):
        return -1 if a < b else 1 if a > b else 0
    if isinstance(a, str) and isinstance(b, str):
        return -1 if a < b else 1 if a > b else 0
    if (isinstance(a, Kw) and isinstance(b, Kw)) or (isinstance(a, Sym) and isinstance(b, Sym)):
        return cmp(a.name, b.name)
    if isinstance(a, list) and isinstance(b, list):
        for x, y in zip(a, b):
            c = cmp(x, y)
            if c:
                return c
        return len(a) - len(b)
    if a is None and b is None:
        return 0
    raise LispError(f"cannot compare {show(a)} with {show(b)}")


def kwargs(args) -> dict:
    o: dict = {}
    i = 0
    while i < len(args):
        a = args[i]
        if isinstance(a, dict):
            o.update(to_py(a))
            i += 1
            continue
        if a is None:
            i += 1
            continue
        if isinstance(a, (Kw, Sym, str)):
            if i + 1 >= len(args):
                raise LispError(f":{key_name(a)} has no value after it")
            o[key_name(a)] = to_py(args[i + 1])
            i += 2
            continue
        raise LispError(f"arguments must be :key value pairs or a map, found {show(a)}")
    return o


# ---------------------------------------------------------------- the machine
SPECIAL = {"quote", "quasiquote", "if", "define", "def", "set!", "lambda", "fn", "begin", "do", "let", "let*", "letrec", "cond", "case",
           "when", "unless", "and", "or", "while", "defmacro", "define-macro", "try", "assert", "macroexpand"}
UNSAFE = re.compile(r"^(jade/|tflite/|onnx/|%host$|read-file$|write-file$|file-exists\?$|list-dir$|load$|eval$|sandbox$|eval-in$|env-define$)")


class Call:
    """A builtin asks the machine to do something: evaluate x in env, or apply f to args, or call Jade."""
    __slots__ = ("kind", "a", "b")

    def __init__(self, kind, a=None, b=None):
        self.kind, self.a, self.b = kind, a, b


class Lisp:
    def __init__(self, host: Host | None = None, *, fuel: int = 50_000_000, pure: bool = False, echo: Callable[[str], None] | None = None, file: str = "",
                 load_paths: list[str] | None = None):
        self.host = None if pure else host
        self.pure = pure
        self.fuel = fuel
        self.steps = 0
        self.out: list[str] = []
        self.echo = echo
        self.gensym = 0
        self.file = file
        self.load_paths = load_paths if load_paths is not None else ["/Jade/Lisp/lib"]
        self.line = 0
        self.G = Env()
        self.k: list = []
        _builtins(self)
        self._ready = False

    # ---- public
    def output(self) -> str:
        return "".join(self.out)

    def define(self, name: str, v) -> None:
        if callable(v) and not isinstance(v, (Fn, Prim)):
            f = v
            v = Prim(name, lambda m, a: f(*a), 0, None)
        self.G.v[Sym(name)] = v

    async def run(self, code: str, file: str = ""):
        await self._prelude()
        saved = self.file
        if file:
            self.file = file
        try:
            r = None
            for f in read(str(code), file or ""):
                r = await self._drive(("ev", f, self.G))
            return r
        finally:
            self.file = saved

    async def call(self, name: str, *args):
        await self._prelude()
        return await self._drive(("ap", self.G.get(Sym(name)), list(args)))

    async def _prelude(self):
        if self._ready:
            return
        self._ready = True
        for f in read(PRELUDE):
            await self._drive(("ev", f, self.G))
        if self.pure:
            for s in list(self.G.v):
                if UNSAFE.match(s.name):
                    del self.G.v[s]

    async def _drive(self, c):
        self.k = []
        while True:
            r = self._run(c)
            if r[0] == "done":
                return r[1]
            op, args = r[1], r[2]   # a call into Jade
            try:
                v = await self._host(op, args)
                c = ("ret", v)
            except LispError as e:
                c = self._unwind(e)
            except Exception as e:  # a tool or Jade failed: an ordinary, catchable error
                c = self._unwind(LispError(str(e) or type(e).__name__))

    async def _host(self, op: str, args: dict):
        if op == "%sleep":
            import asyncio
            await asyncio.sleep(max(0.0, float(args.get("ms") or 0) / 1000))
            return None
        if self.host is None:
            raise LispError(f"{args.get('who') or op}: Jade is not reachable here (this program runs pure, or outside Jade)")
        return from_py(await self.host(op, args))

    def emit(self, s: str) -> None:
        self.out.append(s)
        if self.echo:
            self.echo(s)

    # ---- the loop
    def _run(self, c):
        k = self.k
        while True:
            try:
                while True:
                    self.steps += 1
                    if self.steps > self.fuel:
                        raise LispError(f"out of fuel after {self.fuel} steps (raise --fuel, or look for an endless loop)", kind="fuel")
                    if len(k) > 1_000_000:
                        raise LispError("recursion is too deep (more than a million pending calls)", kind="limit")
                    t = c[0]
                    if t == "ev":
                        c = self._ev(c[1], c[2])
                    elif t == "ret":
                        if not k:
                            return ("done", c[1])
                        c = self._ret(k.pop(), c[1])
                    elif t == "ap":
                        c = self._apply(c[1], c[2])
                    else:   # host call
                        return c
            except LispError as e:
                if e.kind == "read" or e.kind == "limit":
                    k.clear()
                    raise
                if not e.line and self.line:
                    e.line, e.file = self.line, self.file
                c = self._unwind(e)
            except RecursionError:
                k.clear()
                raise LispError("a value is nested too deeply", kind="limit") from None

    def _unwind(self, e: LispError):
        k = self.k
        while k:
            f = k.pop()
            t = f[0]
            if t == "fuel":
                self.fuel = f[1]
                if e.kind == "fuel":
                    line = e.line
                    e = LispError(f"out of fuel: the sandboxed program used more than {f[2]} steps")
                    e.line, e.file = line, self.file
            elif t == "try" and e.kind not in ("fuel", "limit"):
                he = Env(f[3])
                he.v[f[1]] = e.value
                return self._body(f[2], he)
            elif t == "finally" and e.kind not in ("fuel", "limit"):
                k.append(("findone", e))
                return self._body(f[1], f[2])
        raise e

    # ---- helpers
    def lookup(self, s: Sym, env: Env):
        v = env.get(s, _MISSING)
        if v is not _MISSING:
            return v
        if s.name.startswith("tool/") and len(s.name) > 5 and env.has(Sym("jade/tool")):
            return Tool(s.name[5:])
        raise LispError(f"unbound symbol: {s.name}")

    def _body(self, body, env):
        if not body:
            return ("ret", None)
        if len(body) > 1:
            self.k.append(("seq", body, 1, env))
        return ("ev", body[0], env)

    def _bind(self, f: Fn, args):
        np = len(f.params)
        if len(args) < np or (f.rest is None and len(args) > np):
            raise LispError(f"{f.name} expects {np}{' or more' if f.rest is not None else ''} argument{'' if np == 1 and f.rest is None else 's'}, got {len(args)}")
        env = Env(f.env)
        for p, a in zip(f.params, args):
            env.v[p] = a
        if f.rest is not None:
            env.v[f.rest] = list(args[np:])
        return env

    def lambda_(self, name, ps, body, env):
        params, rest = [], None
        if isinstance(ps, Sym):
            rest = ps
        elif isinstance(ps, list):
            i = 0
            while i < len(ps):
                p = ps[i]
                if S(p, ".") or S(p, "&") or S(p, "&rest"):
                    if i + 2 != len(ps) or not isinstance(ps[i + 1], Sym):
                        raise LispError(f"{name}: {p.name} must be followed by exactly one name")
                    rest = ps[i + 1]
                    break
                if not isinstance(p, Sym):
                    raise LispError(f"{name}: parameters must be names, not {show(p)}")
                params.append(p)
                i += 1
        elif ps is not None:
            raise LispError(f"{name}: the parameter list must be a list")
        return Fn(name, params, rest, list(body), env)

    @staticmethod
    def bindings(b, who):
        if not isinstance(b, list):
            raise LispError(f"{who}: bindings must be a list")
        if b and isinstance(b[0], Sym):
            if len(b) % 2:
                raise LispError(f"{who}: [name value ...] needs an even number of forms")
            return [(b[i], b[i + 1]) for i in range(0, len(b), 2)]
        out = []
        for x in b:
            if not isinstance(x, list) or not x or not isinstance(x[0], Sym):
                raise LispError(f"{who}: a binding must be (name value), not {show(x)}")
            out.append((x[0], x[1] if len(x) > 1 else None))
        return out

    @staticmethod
    def is_else(t) -> bool:
        return S(t, "else") or t is True or (isinstance(t, Kw) and t.name == "else")

    # ---- evaluate one form
    def _ev(self, x, env):
        if isinstance(x, Sym):
            return ("ret", self.lookup(x, env))
        if not isinstance(x, list):
            return ("ret", x)
        if not x:
            return ("ret", [])
        if isinstance(x, Form) and x.line:
            self.line = x.line
        if isinstance(x, Brackets):
            return self._items(x, 0, [], env, "blist")
        h = x[0]
        if isinstance(h, Sym) and h.name in SPECIAL and not env.has(h):
            return self._special(h.name, x, env)
        if isinstance(h, Sym):
            f = self.lookup(h, env)
            return self._call_head(f, x, env)
        self.k.append(("head", x, env))
        return ("ev", h, env)

    def _call_head(self, f, x, env):
        if isinstance(f, Macro):
            self.k.append(("expand", env))
            return self._apply_fn(f, list(x[1:]))
        return self._items(x, 1, [f], env, "args")

    def _items(self, x, i, vals, env, kind):
        n = len(x)
        while i < n:
            a = x[i]
            if isinstance(a, list) and a:
                self.k.append((kind, x, i + 1, vals, env))
                return ("ev", a, env)
            vals.append(self.lookup(a, env) if isinstance(a, Sym) else ([] if isinstance(a, list) else a))
            i += 1
        if kind == "blist":
            return ("ret", vals)
        return ("ap", vals[0], vals[1:])

    def _apply(self, f, args):
        if isinstance(f, Fn) and not isinstance(f, Macro):
            return self._apply_fn(f, args)
        if isinstance(f, Prim):
            n = len(args)
            if n < f.lo or (f.hi is not None and n > f.hi):
                want = f"{f.lo}" if f.lo == f.hi else f"{f.lo} or more" if f.hi is None else f"{f.lo} to {f.hi}"
                raise LispError(f"{f.name} expects {want} argument{'' if f.lo == 1 and f.lo == f.hi else 's'}, got {n}")
            try:
                r = f.fn(self, args)
            except LispError:
                raise
            except ZeroDivisionError:
                raise LispError(f"{f.name}: division by zero") from None
            except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError) as e:
                raise LispError(f"{f.name}: {e}") from None
            if isinstance(r, Call):
                if r.kind == "ev":
                    return ("ev", r.a, r.b)
                if r.kind == "ap":
                    return ("ap", r.a, r.b)
                if r.kind == "host":
                    return ("host", r.a, r.b)
                if r.kind == "push":   # push a frame, then evaluate
                    self.k.append(r.a)
                    return r.b
            return ("ret", r)
        if isinstance(f, Tool):
            return ("host", "tool", {"name": f.name, "args": kwargs(args)})
        if isinstance(f, Kw):
            return ("ret", get(args[0] if args else None, f, args[1] if len(args) > 1 else None))
        if isinstance(f, dict):
            if not args:
                raise LispError("a map called as a function needs a key")
            return ("ret", get(f, args[0], args[1] if len(args) > 1 else None))
        if isinstance(f, Macro):
            raise LispError(f"{f.name} is a macro: it can only be used in code, not called as a function")
        raise LispError(f"not a function: {show(f)}")

    def _apply_fn(self, f: Fn, args):
        env = self._bind(f, args)
        return self._body(f.body, env)

    def _special(self, n, x, env):
        k = self.k
        a = lambda i: x[i] if i < len(x) else None  # noqa: E731
        if n == "quote":
            return ("ret", a(1))
        if n == "quasiquote":
            return ("ev", qq(a(1), 1), env)
        if n == "if":
            k.append(("if", a(3), env, a(2)))
            return ("ev", a(1), env)
        if n in ("define", "def"):
            t = a(1)
            if isinstance(t, list) and t:
                if not isinstance(t[0], Sym):
                    raise LispError("define: (define (name args...) body...) needs a name")
                env.v[t[0]] = self.lambda_(t[0].name, t[1:], x[2:], env)
                return ("ret", t[0])
            if not isinstance(t, Sym):
                raise LispError(f"define: cannot define {show(t)}")
            k.append(("def", t, env))
            return ("ev", a(2), env)
        if n == "set!":
            if not isinstance(a(1), Sym):
                raise LispError("set! needs a name")
            k.append(("set", a(1), env))
            return ("ev", a(2), env)
        if n in ("lambda", "fn"):
            return ("ret", self.lambda_("lambda", a(1), x[2:], env))
        if n in ("begin", "do"):
            return self._body(x[1:], env)
        if n in ("let", "let*", "letrec"):
            if isinstance(a(1), Sym):   # named let
                bs = self.bindings(a(2), "let")
                le = Env(env)
                f = self.lambda_(a(1).name, [b[0] for b in bs], x[3:], le)
                le.v[a(1)] = f
                return self._let(("named", f), [b[1] for b in bs], 0, [], env, None)
            bs = self.bindings(a(1), n)
            if n == "let":
                return self._let(("plain", [b[0] for b in bs]), [b[1] for b in bs], 0, [], env, x[2:])
            e2 = Env(env)
            return self._let(("star", [b[0] for b in bs]), [b[1] for b in bs], 0, [], e2, x[2:])
        if n == "cond":
            return self._cond(x, 1, env)
        if n == "case":
            k.append(("case", x, env))
            return ("ev", a(1), env)
        if n in ("when", "unless"):
            k.append(("when", x, n == "unless", env))
            return ("ev", a(1), env)
        if n in ("and", "or"):
            if len(x) == 1:
                return ("ret", n == "and")
            return self._andor(x, 1, n == "and", env)
        if n == "while":
            k.append(("whilet", x, env))
            return ("ev", a(1), env)
        if n in ("defmacro", "define-macro"):
            if n == "defmacro":
                name, ps, body = a(1), a(2), x[3:]
            else:
                name, ps, body = (a(1)[0] if isinstance(a(1), list) and a(1) else None), (a(1)[1:] if isinstance(a(1), list) else None), x[2:]
            if not isinstance(name, Sym):
                raise LispError(f"{n}: needs a name")
            f = self.lambda_(name.name, ps, body, env)
            env.v[name] = Macro(f.name, f.params, f.rest, f.body, f.env)
            return ("ret", name)
        if n == "try":
            body = list(x[1:])
            if body and isinstance(body[-1], list) and body[-1] and S(body[-1][0], "finally"):
                k.append(("finally", body[-1][1:], env))
                body = body[:-1]
            if body and isinstance(body[-1], list) and body[-1] and S(body[-1][0], "catch"):
                h = body[-1]
                if len(h) < 2 or not isinstance(h[1], Sym):
                    raise LispError("try: (catch name body...) needs a name for the error")
                k.append(("try", h[1], h[2:], env))
                body = body[:-1]
            return self._body(body, env)
        if n == "assert":
            k.append(("assert", x, env))
            return ("ev", a(1), env)
        if n == "macroexpand":
            k.append(("macexp", env))
            return ("ev", a(1), env)
        raise LispError(f"bad special form {n}")

    def _let(self, kind, inits, i, vals, env, body):
        while i < len(inits):
            x = inits[i]
            if isinstance(x, list) and x:
                self.k.append(("let", kind, inits, i, vals, env, body))
                return ("ev", x, env)
            v = self.lookup(x, env) if isinstance(x, Sym) else ([] if isinstance(x, list) else x)
            self._let_val(kind, i, vals, env, v)
            i += 1
        t = kind[0]
        if t == "plain":
            e = Env(env)
            for nm, v in zip(kind[1], vals):
                e.v[nm] = v
            return self._body(body, e)
        if t == "star":
            return self._body(body, env)
        return self._apply_fn(kind[1], vals)

    @staticmethod
    def _let_val(kind, i, vals, env, v):
        if kind[0] == "star":
            if isinstance(v, Fn) and v.name == "lambda":
                v.name = kind[1][i].name
            env.v[kind[1][i]] = v
        else:
            vals.append(v)

    def _cond(self, x, i, env):
        while i < len(x):
            cl = x[i]
            if not isinstance(cl, list) or not cl:
                raise LispError(f"cond: a clause must be (test body...), not {show(cl)}")
            if self.is_else(cl[0]):
                return self._clause(cl, True, env)
            self.k.append(("cond", x, i, env))
            return ("ev", cl[0], env)
        return ("ret", None)

    def _clause(self, cl, t, env):
        if len(cl) == 1:
            return ("ret", t)
        if S(cl[1], "=>"):
            self.k.append(("arrow", t))
            return ("ev", cl[2], env)
        return self._body(cl[1:], env)

    def _andor(self, x, i, is_and, env):
        if i < len(x) - 1:
            self.k.append(("andor", x, i + 1, is_and, env))
        return ("ev", x[i], env)

    # ---- a value came back to a frame
    def _ret(self, f, v):
        t = f[0]
        k = self.k
        if t == "args":
            _, x, i, vals, env = f
            vals.append(v)
            return self._items(x, i, vals, env, "args")
        if t == "seq":
            _, body, i, env = f
            if i < len(body) - 1:
                k.append(("seq", body, i + 1, env))
            return ("ev", body[i], env)
        if t == "if":
            return ("ev", f[3] if truthy(v) else f[1], f[2])
        if t == "blist":
            _, x, i, vals, env = f
            vals.append(v)
            return self._items(x, i, vals, env, "blist")
        if t == "head":
            _, x, env = f
            return self._call_head(v, x, env)
        if t == "def":
            if isinstance(v, Fn) and v.name == "lambda":
                v.name = f[1].name
            f[2].v[f[1]] = v
            return ("ret", f[1])
        if t == "set":
            if not f[2].set(f[1], v):
                raise LispError(f"set!: {show(f[1])} is not defined (use define first)")
            return ("ret", v)
        if t == "let":
            _, kind, inits, i, vals, env, body = f
            self._let_val(kind, i, vals, env, v)
            return self._let(kind, inits, i + 1, vals, env, body)
        if t == "cond":
            _, x, i, env = f
            if truthy(v):
                return self._clause(x[i], v, env)
            return self._cond(x, i + 1, env)
        if t == "arrow":
            return ("ap", v, [f[1]])
        if t == "case":
            _, x, env = f
            for cl in x[2:]:
                ds = cl[0]
                if self.is_else(ds) or (any(equal(d, v) for d in ds) if isinstance(ds, list) else equal(ds, v)):
                    return self._body(cl[1:], env)
            return ("ret", None)
        if t == "when":
            _, x, neg, env = f
            if truthy(v) == neg:
                return ("ret", None)
            return self._body(x[2:], env)
        if t == "andor":
            _, x, i, is_and, env = f
            if truthy(v) != is_and:
                return ("ret", v)
            return self._andor(x, i, is_and, env)
        if t == "whilet":
            _, x, env = f
            if not truthy(v):
                return ("ret", None)
            if len(x) > 2:
                k.append(("whileb", x, env))
                return self._body(x[2:], env)
            k.append(("whilet", x, env))
            return ("ev", x[1], env)
        if t == "whileb":
            _, x, env = f
            k.append(("whilet", x, env))
            return ("ev", x[1], env)
        if t == "try":
            return ("ret", v)
        if t == "finally":
            k.append(("findone", None, v))
            return self._body(f[1], f[2])
        if t == "findone":
            if f[1] is not None:
                raise f[1]
            return ("ret", f[2])
        if t == "expand":
            return ("ev", v, f[1])
        if t == "macexp":
            env = f[1]
            if isinstance(v, list) and v and isinstance(v[0], Sym) and isinstance(env.get(v[0]), Macro):
                k.append(("macexp", env))
                return self._apply_fn(env.get(v[0]), list(v[1:]))
            return ("ret", v)
        if t == "assert":
            _, x, env = f
            if truthy(v):
                return ("ret", v)
            if len(x) > 2:
                k.append(("assertmsg",))
                return ("ev", x[2], env)
            e = LispError("assertion failed: " + show(x[1]))
            e.user = True
            raise e
        if t == "assertmsg":
            e = LispError("assertion failed: " + show(v, False))
            e.user = True
            raise e
        if t == "load":
            path = f[1]
            forms = read(str(v), path)
            saved = self.file
            k.append(("loaddone", saved))
            self.file = path
            return self._body(forms, self.G)
        if t == "loaddone":
            self.file = f[1]
            return ("ret", v)
        if t == "fuel":
            self.fuel = f[1]
            return ("ret", v)
        if t == "callback":   # a builtin's continuation: f[1](value) returns a control step or a value
            r = f[1](v)
            if isinstance(r, tuple):
                return r
            return ("ret", r)
        raise LispError(f"internal: unknown frame {t}")



def qq(x, d):
    """quasiquote to code: `(a ,b ,@c) becomes (%append (%list 'a) (%list b) c)"""
    if not isinstance(x, list) or not x:
        if isinstance(x, (Sym,)) or (isinstance(x, list)):
            return [Sym("quote"), x]
        return x
    if S(x[0], "unquote"):
        return x[1] if d == 1 else [Sym("%list"), [Sym("quote"), Sym("unquote")], qq(x[1], d - 1)]
    if S(x[0], "quasiquote"):
        return [Sym("%list"), [Sym("quote"), Sym("quasiquote")], qq(x[1], d + 1)]
    parts: list = [Sym("%append")]
    for e in x:
        if d == 1 and isinstance(e, list) and e and S(e[0], "unquote-splicing"):
            parts.append(e[1])
        else:
            parts.append([Sym("%list"), qq(e, d)])
    return parts


# ---------------------------------------------------------------- builtins
def _builtins(m: Lisp) -> None:
    G = m.G

    def d(name, lo=0, hi=None, ctl=False):
        def deco(fn):
            G.v[Sym(name)] = Prim(name, fn, lo, hi, ctl)
            return fn
        return deco

    def alias(new, old):
        p = G.v[Sym(old)]
        G.v[Sym(new)] = Prim(new, p.fn, p.lo, p.hi, p.ctl)

    def nums(a, who):
        return [num(x, who) for x in a]

    def tidy(v):
        """JavaScript has one number type: keep whole results whole (6.0 -> 6)."""
        if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
            return int(v)
        return v

    @d("+")
    def _(m, a):
        return tidy(sum(nums(a, "+")))

    @d("*")
    def _(m, a):
        r = 1
        for x in nums(a, "*"):
            r *= x
        return tidy(r)

    @d("-", 1)
    def _(m, a):
        xs = nums(a, "-")
        if len(xs) == 1:
            return -xs[0]
        r = xs[0]
        for x in xs[1:]:
            r -= x
        return tidy(r)

    @d("/", 1)
    def _(m, a):
        xs = nums(a, "/")
        if len(xs) == 1:
            xs = [1] + xs
        r = xs[0]
        for x in xs[1:]:
            if x == 0:
                raise LispError("/: division by zero")
            r = r / x
        return tidy(r)

    def intop(name, f):
        @d(name, 2, 2)
        def _(m, a):
            x, y = num(a[0], name), num(a[1], name)
            if y == 0:
                raise LispError(f"{name}: division by zero")
            return tidy(f(x, y))

    intop("quotient", lambda x, y: math.trunc(x / y))
    intop("remainder", lambda x, y: math.fmod(x, y) if isinstance(x, float) or isinstance(y, float) else (abs(x) % abs(y)) * (1 if x >= 0 else -1))
    intop("modulo", lambda x, y: x % y)
    intop("mod", lambda x, y: x % y)
    for name, f in {"abs": abs, "floor": math.floor, "ceiling": math.ceil, "ceil": math.ceil, "truncate": math.trunc, "sqrt": math.sqrt, "exp": math.exp, "sin": math.sin,
                    "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "tanh": math.tanh}.items():
        (lambda name, f: d(name, 1, 1)(lambda m, a: tidy(f(num(a[0], name)))))(name, f)
    d("sign", 1, 1)(lambda m, a: (num(a[0], "sign") > 0) - (a[0] < 0))
    d("round", 1, 2)(lambda m, a: tidy(math.floor(num(a[0], "round") + 0.5)) if len(a) == 1 else tidy(math.floor(a[0] * 10 ** a[1] + 0.5) / 10 ** a[1]))
    d("expt", 2, 2)(lambda m, a: tidy(num(a[0], "expt") ** num(a[1], "expt")))
    d("log", 1, 2)(lambda m, a: math.log(a[0]) if len(a) == 1 else math.log(a[0]) / math.log(a[1]))
    d("atan", 1, 2)(lambda m, a: math.atan(a[0]) if len(a) == 1 else math.atan2(a[0], a[1]))
    d("min", 1)(lambda m, a: min(nums(a, "min")))
    d("max", 1)(lambda m, a: max(nums(a, "max")))
    d("inc", 1, 1)(lambda m, a: num(a[0], "inc") + 1)
    d("dec", 1, 1)(lambda m, a: num(a[0], "dec") - 1)
    d("random", 0, 1)(lambda m, a: random.random() if not a else (random.randrange(a[0]) if isinstance(a[0], int) else random.random() * a[0]))
    d("int", 1, 1)(lambda m, a: math.trunc(num(a[0], "int")))
    d("float", 1, 1)(lambda m, a: num(a[0], "float"))

    def chain(name, ok):
        @d(name)
        def _(m, a):
            return all(ok(cmp(a[i], a[i + 1])) for i in range(len(a) - 1))

    chain("<", lambda c: c < 0)
    chain(">", lambda c: c > 0)
    chain("<=", lambda c: c <= 0)
    chain(">=", lambda c: c >= 0)
    d("=")(lambda m, a: all(equal(a[i - 1], a[i]) for i in range(1, len(a))))
    alias("==", "=")
    d("!=", 2, 2)(lambda m, a: not equal(a[0], a[1]))
    alias("not=", "!=")
    d("compare", 2, 2)(lambda m, a: (cmp(a[0], a[1]) > 0) - (cmp(a[0], a[1]) < 0))
    d("not", 1, 1)(lambda m, a: not truthy(a[0]))
    d("eq?", 2, 2)(lambda m, a: a[0] is a[1] or (not isinstance(a[0], (list, dict)) and equal(a[0], a[1])))
    alias("eqv?", "eq?")
    d("equal?", 2, 2)(lambda m, a: equal(a[0], a[1]))
    preds = {"nil?": lambda v: v is None, "null?": lambda v: v is None or (isinstance(v, list) and not v), "number?": is_num,
             "integer?": lambda v: is_num(v) and float(v).is_integer(), "string?": lambda v: isinstance(v, str), "symbol?": lambda v: isinstance(v, Sym),
             "keyword?": lambda v: isinstance(v, Kw), "list?": lambda v: isinstance(v, list), "pair?": lambda v: isinstance(v, list) and bool(v), "map?": is_map,
             "boolean?": lambda v: isinstance(v, bool), "fn?": lambda v: (isinstance(v, Fn) and not isinstance(v, Macro)) or isinstance(v, (Prim, Tool)),
             "zero?": lambda v: v == 0 and is_num(v), "positive?": lambda v: is_num(v) and v > 0, "negative?": lambda v: is_num(v) and v < 0,
             "even?": lambda v: is_num(v) and v % 2 == 0, "odd?": lambda v: is_num(v) and abs(v % 2) == 1,
             "empty?": lambda v: v is None or (len(v) == 0 if isinstance(v, (list, str, dict)) else False)}
    alias_p = {"procedure?": "fn?"}
    for name, p in preds.items():
        (lambda name, p: d(name, 1, 1)(lambda m, a: bool(p(a[0]))))(name, p)
    for new, old in alias_p.items():
        alias(new, old)
    d("type-of", 1, 1)(lambda m, a: Sym(type_of(a[0])))

    # lists
    d("list")(lambda m, a: list(a))
    alias("%list", "list")
    d("cons", 2, 2)(lambda m, a: [a[0], *seq_of(a[1], "cons")])

    @d("car", 1, 1)
    def _(m, a):
        if not isinstance(a[0], list) or not a[0]:
            raise LispError(f"car: expected a non-empty list, got {show(a[0])}")
        return a[0][0]

    @d("cdr", 1, 1)
    def _(m, a):
        if not isinstance(a[0], list) or not a[0]:
            raise LispError(f"cdr: expected a non-empty list, got {show(a[0])}")
        return a[0][1:]

    d("first", 1, 1)(lambda m, a: (seq_of(a[0], "first") or [None])[0])
    d("rest", 1, 1)(lambda m, a: list(seq_of(a[0], "rest")[1:]))
    d("second", 1, 1)(lambda m, a: get(a[0], 1))
    d("third", 1, 1)(lambda m, a: get(a[0], 2))
    alias("cadr", "second")
    alias("caddr", "third")
    d("cddr", 1, 1)(lambda m, a: list(seq_of(a[0], "cddr")[2:]))
    d("caar", 1, 1)(lambda m, a: get(get(a[0], 0), 0))
    d("last", 1, 1)(lambda m, a: (seq_of(a[0], "last") or [None])[-1])
    d("butlast", 1, 1)(lambda m, a: list(seq_of(a[0], "butlast")[:-1]))
    d("length", 1, 1)(lambda m, a: 0 if a[0] is None else len(a[0]) if isinstance(a[0], dict) else len(seq_of(a[0], "length")))
    alias("count", "length")
    d("append")(lambda m, a: [x for l in a for x in seq_of(l, "append")])
    alias("concat", "append")
    alias("%append", "append")
    d("reverse", 1, 1)(lambda m, a: list(reversed(seq_of(a[0], "reverse"))))

    @d("nth", 2, 3)
    def _(m, a):
        s = seq_of(a[0], "nth")
        i = a[1]
        if is_num(i) and 0 <= i < len(s):
            return s[int(i)]
        if len(a) > 2:
            return a[2]
        raise LispError(f"nth: index {show(i)} is past the end of a list of {len(s)}")

    alias("list-ref", "nth")
    d("take", 2, 2)(lambda m, a: list(seq_of(a[1], "take")[:max(0, int(a[0]))]))
    d("drop", 2, 2)(lambda m, a: list(seq_of(a[1], "drop")[max(0, int(a[0])):]))
    d("slice", 2, 3)(lambda m, a: (a[0][a[1]:(a[2] if len(a) > 2 and a[2] is not None else None)]) if isinstance(a[0], str) else list(seq_of(a[0], "slice")[a[1]:(a[2] if len(a) > 2 and a[2] is not None else None)]))

    @d("range", 1, 3)
    def _(m, a):
        if len(a) == 1 or a[1] is None:
            lo, hi = 0, num(a[0], "range")
        else:
            lo, hi = num(a[0], "range"), num(a[1], "range")
        s = num(a[2], "range") if len(a) > 2 else 1
        if not s:
            raise LispError("range: step must not be 0")
        r = []
        i = lo
        while (i < hi) if s > 0 else (i > hi):
            r.append(i)
            if len(r) > 10_000_000:
                raise LispError("range: more than 10 million items")
            i += s
        return r

    d("iota", 1, 3)(lambda m, a: [(a[1] if len(a) > 1 else 0) + i * (a[2] if len(a) > 2 else 1) for i in range(int(a[0]))])
    d("repeat", 2, 2)(lambda m, a: [a[1]] * int(a[0]))

    @d("member", 2, 2)
    def _(m, a):
        s = seq_of(a[1], "member")
        for i, e in enumerate(s):
            if equal(e, a[0]):
                return list(s[i:])
        return False

    @d("contains?", 2, 2)
    def _(m, a):
        c, x = a
        if isinstance(c, dict):
            return key_name(x) in c
        if isinstance(c, str):
            return strv(x, "contains?") in c
        return any(equal(e, x) for e in seq_of(c, "contains?"))

    @d("index-of", 2, 2)
    def _(m, a):
        if isinstance(a[0], str):
            i = a[0].find(a[1])
            return None if i < 0 else i
        for i, e in enumerate(seq_of(a[0], "index-of")):
            if equal(e, a[1]):
                return i
        return None

    @d("assoc", 2)
    def _(m, a):
        x, r = a[0], a[1:]
        if len(r) == 1 and isinstance(r[0], list) and not isinstance(x, dict):
            for p in r[0]:
                if isinstance(p, list) and p and equal(p[0], x):
                    return p
            return False
        if len(r) % 2:
            raise LispError("assoc: (assoc map key value ...) needs a value for every key")
        if isinstance(x, list):
            o = list(x)
            for i in range(0, len(r), 2):
                o[int(r[i])] = r[i + 1]
            return o
        o = dict(x or {})
        for i in range(0, len(r), 2):
            o[key_name(r[i])] = r[i + 1]
        return o

    def flat(v):
        out = []
        for x in v:
            out.extend(flat(x) if isinstance(x, list) else [x])
        return out

    d("flatten", 1, 1)(lambda m, a: flat(seq_of(a[0], "flatten")))

    @d("distinct", 1, 1)
    def _(m, a):
        out: list = []
        for x in seq_of(a[0], "distinct"):
            if not any(equal(x, y) for y in out):
                out.append(x)
        return out

    d("sum", 1, 1)(lambda m, a: tidy(sum(num(x, "sum") for x in seq_of(a[0], "sum"))))

    @d("product", 1, 1)
    def _(m, a):
        r = 1
        for x in seq_of(a[0], "product"):
            r *= num(x, "product")
        return tidy(r)

    d("zip", 1)(lambda m, a: [list(t) for t in zip(*[seq_of(l, "zip") for l in a])])
    d("%sort", 1, 1)(lambda m, a: _natsort(seq_of(a[0], "sort")))

    @d("apply", 2, ctl=True)
    def _(m, a):
        f, mid, last = a[0], list(a[1:-1]), a[-1]
        return Call("ap", f, mid + list(seq_of(last, "apply")))

    # strings
    d("str")(lambda m, a: "".join(show(x, False) for x in a))
    d("string-append")(lambda m, a: "".join(strv(x, "string-append") for x in a))
    d("string-length", 1, 1)(lambda m, a: len(strv(a[0], "string-length")))
    d("substring", 2, 3)(lambda m, a: strv(a[0], "substring")[a[1]:(a[2] if len(a) > 2 and a[2] is not None else None)])
    d("string-upcase", 1, 1)(lambda m, a: strv(a[0], "string-upcase").upper())
    d("string-downcase", 1, 1)(lambda m, a: strv(a[0], "string-downcase").lower())
    d("string-trim", 1, 1)(lambda m, a: strv(a[0], "string-trim").strip())
    d("string-split", 1, 2)(lambda m, a: strv(a[0], "string-split").split() if len(a) == 1 else strv(a[0], "string-split").split(strv(a[1], "string-split")) if a[1] != "" else list(a[0]))
    d("string-join", 1, 2)(lambda m, a: (a[1] if len(a) > 1 else "").join(show(x, False) for x in seq_of(a[0], "string-join")))
    d("string-contains?", 2, 2)(lambda m, a: strv(a[1], "string-contains?") in strv(a[0], "string-contains?"))
    d("string-starts-with?", 2, 2)(lambda m, a: strv(a[0], "string-starts-with?").startswith(strv(a[1], "string-starts-with?")))
    d("string-ends-with?", 2, 2)(lambda m, a: strv(a[0], "string-ends-with?").endswith(strv(a[1], "string-ends-with?")))
    d("string-replace", 3, 3)(lambda m, a: strv(a[0], "string-replace").replace(strv(a[1], "string-replace"), strv(a[2], "string-replace")))
    d("string-pad-left", 2, 3)(lambda m, a: show(a[0], False).rjust(int(a[1]), (a[2] if len(a) > 2 else " ")[:1] or " "))
    d("string-pad-right", 2, 3)(lambda m, a: show(a[0], False).ljust(int(a[1]), (a[2] if len(a) > 2 else " ")[:1] or " "))
    d("string=?", 1)(lambda m, a: all(strv(x, "string=?") == strv(a[0], "string=?") for x in a))
    d("string<?", 2, 2)(lambda m, a: strv(a[0], "string<?") < strv(a[1], "string<?"))

    @d("string->number", 1, 1)
    def _(m, a):
        v = atom(str(a[0]).strip())
        return v if is_num(v) else False

    d("number->string", 1, 2)(lambda m, a: fmt_num(num(a[0], "number->string")) if len(a) == 1 or a[1] in (None, 10) else _radix(int(a[0]), int(a[1])))
    d("string->symbol", 1, 1)(lambda m, a: Sym(strv(a[0], "string->symbol")))
    alias("symbol", "string->symbol")
    d("symbol->string", 1, 1)(lambda m, a: strv(a[0], "symbol->string"))
    d("name", 1, 1)(lambda m, a: strv(a[0], "name"))
    d("keyword", 1, 1)(lambda m, a: Kw(strv(a[0], "keyword").lstrip(":")))
    d("string->list", 1, 1)(lambda m, a: list(strv(a[0], "string->list")))
    d("list->string", 1, 1)(lambda m, a: "".join(show(x, False) for x in seq_of(a[0], "list->string")))

    @d("format", 1)
    def _(m, a):
        it = iter(a[1:])

        def rep(mo):
            dd = mo.group(1).lower()
            if dd in ("%", "n"):
                return "\n"
            if dd == "~":
                return "~"
            try:
                v = next(it)
            except StopIteration:
                raise LispError("format: more directives than arguments") from None
            return show(v, dd == "s")
        return re.sub(r"~([asdn%~])", rep, strv(a[0], "format"), flags=re.I)

    d("regex-match", 2, 2)(lambda m, a: (lambda mo: [mo.group(0), *mo.groups()] if mo else None)(re.search(_rx(a[0]), str(a[1]))))
    d("regex-match-all", 2, 2)(lambda m, a: [[mo.group(0), *mo.groups()] for mo in re.finditer(_rx(a[0]), str(a[1]))])
    d("regex-replace", 3, 3)(lambda m, a: re.sub(_rx(a[0]), re.sub(r"\$(\d)", r"\\\1", str(a[2])), str(a[1])))
    d("regex-test", 2, 2)(lambda m, a: re.search(_rx(a[0]), str(a[1])) is not None)

    # maps
    @d("hash-map")
    def _(m, a):
        if len(a) % 2:
            raise LispError("hash-map: needs key value pairs")
        return {key_name(a[i]): a[i + 1] for i in range(0, len(a), 2)}

    d("get", 2, 3)(lambda m, a: get(a[0], a[1], a[2] if len(a) > 2 else None))

    @d("get-in", 2, 3)
    def _(m, a):
        c = a[0]
        for k in seq_of(a[1], "get-in"):
            c = get(c, k, _MISSING)
            if c is _MISSING:
                return a[2] if len(a) > 2 else None
        return c

    @d("assoc-in", 3, 3)
    def _(m, a):
        path = seq_of(a[1], "assoc-in")

        def go(o, i):
            if i == len(path):
                return a[2]
            base = o if isinstance(o, dict) else {}
            k = key_name(path[i])
            return {**base, k: go(base.get(k), i + 1)}
        return go(a[0], 0)

    d("dissoc", 1)(lambda m, a: {k: v for k, v in (a[0] or {}).items() if k not in {key_name(x) for x in a[1:]}})
    d("keys", 1, 1)(lambda m, a: [Kw(k) for k in (a[0] or {})])
    d("vals", 1, 1)(lambda m, a: list((a[0] or {}).values()))
    d("entries", 1, 1)(lambda m, a: [[Kw(k), v] for k, v in (a[0] or {}).items()])
    d("has-key?", 2, 2)(lambda m, a: bool(a[0]) and key_name(a[1]) in a[0])

    @d("merge")
    def _(m, a):
        o: dict = {}
        for x in a:
            if x:
                o.update(x)
        return o

    d("zipmap", 2, 2)(lambda m, a: {key_name(k): v for k, v in zip(seq_of(a[0], "zipmap"), seq_of(a[1], "zipmap"))})

    # output
    def out(s):
        m.emit(s)
        return None

    d("display", 1)(lambda m, a: out("".join(show(x, False) for x in a)))
    d("write", 1, 1)(lambda m, a: out(show(a[0], True)))
    d("newline", 0, 0)(lambda m, a: out("\n"))
    d("print")(lambda m, a: out(" ".join(show(x, False) for x in a) + "\n"))
    alias("println", "print")
    d("printf", 1)(lambda m, a: out(G.v[Sym("format")].fn(m, a)))
    d("pr-str")(lambda m, a: " ".join(show(x, True) for x in a))

    # code, errors, JSON
    @d("error")
    def _(m, a):
        e = LispError(" ".join(x if i == 0 and isinstance(x, str) else show(x, True) for i, x in enumerate(a)))
        e.user = True
        raise e

    @d("raise", 1, 1)
    def _(m, a):
        e = LispError(a[0] if isinstance(a[0], str) else show(a[0]), a[0])
        e.user = True
        raise e

    alias("throw", "raise")

    @d("gensym", 0, 1)
    def _(m, a):
        m.gensym += 1
        return Sym(f"{key_name(a[0]) if a else 'g'}__{m.gensym}")

    d("eval", 1, 2, ctl=True)(lambda m, a: Call("ev", a[0], a[1] if len(a) > 1 and isinstance(a[1], Env) else m.G))
    d("read-string", 1, 1)(lambda m, a: (read(strv(a[0], "read-string")) or [None])[0])
    d("read-all", 1, 1)(lambda m, a: read(strv(a[0], "read-all")))
    d("identity", 1, 1)(lambda m, a: a[0])
    d("steps", 0, 0)(lambda m, a: m.steps)
    d("defined?", 1, 1)(lambda m, a: m.G.has(a[0] if isinstance(a[0], Sym) else Sym(strv(a[0], "defined?"))))

    @d("json-parse", 1, 1)
    def _(m, a):
        try:
            return json.loads(strv(a[0], "json-parse"))
        except ValueError as e:
            raise LispError(f"json-parse: {e}") from None

    d("json-string", 1, 2)(lambda m, a: json.dumps(to_py(a[0]), indent=2 if len(a) > 1 and truthy(a[1]) else None, separators=None if len(a) > 1 and truthy(a[1]) else (",", ":"), ensure_ascii=False))
    import time as _t
    d("now", 0, 0)(lambda m, a: int(_t.time() * 1000))

    # sandboxes
    @d("sandbox")
    def _(m, a):
        e = Env()
        names = [y for x in a for y in (x if isinstance(x, list) else [x])]
        for n in names:
            if isinstance(n, Kw) and n.name == "pure":
                for s, v in m.G.v.items():
                    if not UNSAFE.match(s.name):
                        e.v[s] = v
                continue
            if isinstance(n, dict):
                for kk, v in n.items():
                    e.v[Sym(kk)] = v
                continue
            s = n if isinstance(n, Sym) else Sym(strv(n, "sandbox"))
            v = m.G.get(s, _MISSING)
            if v is _MISSING:
                raise LispError(f"sandbox: {s.name} is not defined")
            e.v[s] = v
        return e

    @d("env-define", 3, 3)
    def _(m, a):
        if not isinstance(a[0], Env):
            raise LispError("env-define: expected an env")
        a[0].v[a[1] if isinstance(a[1], Sym) else Sym(strv(a[1], "env-define"))] = a[2]
        return a[0]

    @d("eval-in", 2, 3, ctl=True)
    def _(m, a):
        if not isinstance(a[0], Env):
            raise LispError("eval-in: expected an env from (sandbox ...)")
        form = a[1]
        if isinstance(form, str):
            form = Form([Sym("begin"), *read(form)])
        fuel = get(a[2], "fuel") if len(a) > 2 else None
        if fuel:
            saved = m.fuel
            m.fuel = min(saved, m.steps + int(fuel))
            return Call("push", ("fuel", saved, int(fuel)), ("ev", form, a[0]))
        return Call("ev", form, a[0])

    # Jade (left out with pure). Everything that leaves the interpreter is one host call.
    def host(op, who=None):
        def f(m, a):
            return Call("host", op, a[0] if a and isinstance(a[0], dict) else {})
        return f

    d("%host", 2, 2, ctl=True)(lambda m, a: Call("host", strv(a[0], "%host"), to_py(a[1]) if isinstance(a[1], dict) else {}))
    d("jade/sleep", 1, 1, ctl=True)(lambda m, a: Call("host", "%sleep", {"ms": a[0]}))

    @d("load", 1, 1, ctl=True)
    def _(m, a):
        p = strv(a[0], "load")
        base = (m.file.rsplit("/", 1)[0] or "/") if m.file else "/Jade/Lisp"
        if p.startswith("/"):
            cands = [p, p + ".jl"]
        else:
            parent = base.rsplit("/", 1)[0] or ""
            cands = [f"{base}/{p}", f"{base}/{p}.jl", f"{parent}/lib/{p}.jl"] + [x for lp in m.load_paths for x in (f"{lp}/{p}", f"{lp}/{p}.jl")]
        return Call("push", ("callback", lambda found: m._load_found(found, p, cands)), ("host", "fs.find", {"paths": cands}))


def _load_found(self: Lisp, found, p, cands):
    if not found:
        raise LispError(f"load: {p} not found (looked in {', '.join(cands)})")
    self.k.append(("load", found["path"]))
    return ("ret", found["text"])


Lisp._load_found = _load_found


def _rx(s) -> str:
    """JavaScript regular expressions -> Python (the usual subset is the same; named groups differ)."""
    return re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", strv(s, "regex"))


def _radix(n: int, r: int) -> str:
    if not 2 <= r <= 36:
        raise LispError("radix must be 2 to 36")
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    neg, n = n < 0, abs(n)
    out = []
    while n:
        out.append(digits[n % r])
        n //= r
    return ("-" if neg else "") + "".join(reversed(out))


def _natsort(xs):
    import functools
    return sorted(xs, key=functools.cmp_to_key(cmp))


# Higher-order functions, macros and the Jade calls, in Lisp (they may call anything, even Jade, so they live here)
PRELUDE = r"""
(define (map f . ls)
  (if (null? (cdr ls))
      (let loop ((s (seq (car ls))) (acc (list))) (if (null? s) (reverse acc) (loop (cdr s) (cons (f (car s)) acc))))
      (let loop ((ss (map seq ls)) (acc (list)))
        (if (any? null? ss) (reverse acc) (loop (map cdr ss) (cons (apply f (map car ss)) acc))))))
(define (seq x) (cond ((list? x) x) ((nil? x) (list)) (else (%seq x))))
(define (for-each f . ls) (apply map f ls) nil)
(define (map-indexed f l) (let loop ((s (seq l)) (i 0) (acc (list))) (if (null? s) (reverse acc) (loop (cdr s) (+ i 1) (cons (f i (car s)) acc)))))
(define (filter f l) (let loop ((s (seq l)) (acc (list))) (if (null? s) (reverse acc) (loop (cdr s) (if (f (car s)) (cons (car s) acc) acc)))))
(define (remove f l) (filter (lambda (x) (not (f x))) l))
(define (reduce f init . l)
  (if (null? l)
      (let ((s (seq init))) (if (null? s) nil (reduce f (car s) (cdr s))))
      (let loop ((s (seq (car l))) (acc init)) (if (null? s) acc (loop (cdr s) (f acc (car s)))))))
(define (foldl f init l) (let loop ((s (seq l)) (acc init)) (if (null? s) acc (loop (cdr s) (f (car s) acc)))))
(define (find f l) (let loop ((s (seq l))) (cond ((null? s) nil) ((f (car s)) (car s)) (else (loop (cdr s))))))
(define (any? f l) (let loop ((s (seq l))) (cond ((null? s) #f) ((f (car s)) #t) (else (loop (cdr s))))))
(define (every? f l) (let loop ((s (seq l))) (cond ((null? s) #t) ((f (car s)) (loop (cdr s))) (else #f))))
(define (mapcat f l) (apply append (map f l)))
(define (group-by f l) (reduce (lambda (m x) (let ((k (f x))) (assoc m k (append (get m k (list)) (list x))))) {} l))
(define (%merge less a b)
  (let loop ((a a) (b b) (acc (list)))
    (cond ((null? a) (append (reverse acc) b))
          ((null? b) (append (reverse acc) a))
          ((less (car b) (car a)) (loop a (cdr b) (cons (car b) acc)))
          (else (loop (cdr a) b (cons (car a) acc))))))
(define (%msort less s) (if (< (length s) 2) s (let ((h (quotient (length s) 2))) (%merge less (%msort less (take h s)) (%msort less (drop h s))))))
(define (sort l . f) (if (null? f) (%sort l) (%msort (car f) (seq l))))
(define (sort-by kf l) (map cadr (%msort (lambda (a b) (< (compare (car a) (car b)) 0)) (map (lambda (x) (list (kf x) x)) (seq l)))))
(define (update m k f . a) (assoc m k (apply f (get m k) a)))
(defmacro defn (name params . body) `(define ,name (lambda ,params ,@body)))
(defmacro -> (x . forms) (if (null? forms) x (let ((f (car forms))) `(-> ,(if (list? f) `(,(car f) ,x ,@(cdr f)) `(,f ,x)) ,@(cdr forms)))))
(defmacro ->> (x . forms) (if (null? forms) x (let ((f (car forms))) `(->> ,(if (list? f) `(,@f ,x) `(,f ,x)) ,@(cdr forms)))))
(defmacro dolist (spec . body) `(for-each (lambda (,(car spec)) ,@body) ,(cadr spec)))
(defmacro dotimes (spec . body) (let ((n (gensym "n"))) `(let ((,n ,(cadr spec))) (let loop ((,(car spec) 0)) (when (< ,(car spec) ,n) ,@body (loop (+ ,(car spec) 1)))))))
(defmacro if-let (b then . else) `(let ((,(car b) ,(cadr b))) (if ,(car b) ,then ,@else)))
(defmacro when-let (b . body) `(let ((,(car b) ,(cadr b))) (when ,(car b) ,@body)))
(defmacro inc! (v . n) `(set! ,v (+ ,v ,(if (null? n) 1 (car n)))))
(defmacro push! (v x) `(set! ,v (append ,v (list ,x))))
(define (compose . fs) (if (null? fs) identity (let ((f (car fs)) (g (apply compose (cdr fs)))) (lambda args (f (apply g args))))))
(define (partial f . a) (lambda b (apply f (append a b))))
(define (complement f) (lambda args (not (apply f args))))
(define (constantly v) (lambda args v))
(define (%jsonish v) (if (and (string? v) (regex-test "^\\s*[{\\[]" v)) (try (json-parse v) (catch e v)) v))
(define (jade/tool name . a) (%host "tool" {:name (str name) :args (apply kwargs a)}))
(define (jade/tools) (%host "list" {}))
(define (jade/ask prompt . a) (%host "ask" (merge (apply kwargs a) {:prompt prompt})))
(define (jade/decide . a) (%host "decide" (apply kwargs a)))
(define (jade/agent msg . a) (%host "agent" (merge (apply kwargs a) {:message msg})))
(define (jade/flow name . input) (%host "flow" {:name name :input (if (null? input) nil (car input))}))
(define (jade/flows) (%host "flows" {}))
(define (jade/shell cmd) (%host "shell" {:command cmd}))
(define (jade/notify t . b) (%host "notify" {:title (str t) :body (if (null? b) "" (str (car b)))}))
(define (jade/search q . limit) (%host "search" {:query q :limit (if (null? limit) 10 (car limit))}))
(define (jade/kv-get k) (%host "kv" {:op "get" :key (name k)}))
(define (jade/kv-set k v) (%host "kv" {:op "set" :key (name k) :value v}))
(define (tflite/load model . a) (%jsonish (jade/tool "litert" (merge (apply kwargs a) {:action "load" :model model}))))
(define (tflite/run id inputs . a) (%jsonish (jade/tool "litert" (merge (apply kwargs a) {:action "run" :id id :inputs inputs}))))
(define (tflite/info . id) (%jsonish (jade/tool "litert" (if (null? id) {:action "info"} {:action "info" :id (car id)}))))
(define (tflite/unload id) (%jsonish (jade/tool "litert" {:action "unload" :id id})))
(define (onnx/run . a) (%jsonish (apply jade/tool "onnx" a)))
(define (read-file p) (%host "fs.read" {:path p}))
(define (write-file p content) (%host "fs.write" {:path p :content (if (string? content) content (str content))}) p)
(define (list-dir p) (%host "fs.list" {:path p}))
(define (file-exists? p) (%host "fs.exists" {:path p}))
"""


def _seq(m, a):
    v = a[0]
    return seq_of(v, "seq")


def _install_extra(m: Lisp):
    m.G.v[Sym("%seq")] = Prim("%seq", _seq, 1, 1)
    m.G.v[Sym("kwargs")] = Prim("kwargs", lambda m, a: kwargs(a), 0, None)


_orig_builtins = _builtins


def _builtins_all(m: Lisp):
    _orig_builtins(m)
    _install_extra(m)


_builtins = _builtins_all  # noqa: F811


def err_text(e: BaseException) -> str:
    if isinstance(e, LispError):
        return str(e) + (f" ({e.file + ':' if e.file else 'line '}{e.line})" if e.line else "")
    return f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- Jade: the host, the tool, the CLI
async def jade_host(jade, op: str, a: dict, session=None):
    """A Lisp call into Jade: the same calls as the app's jade.* bridge, plus files and search."""
    from .bridge import jade_bridge
    v = jade.vault
    if op == "fs.read":
        p = v.norm(str(a.get("path") or ""))
        if not v.is_file(p):
            raise LispError(f"read-file: {p} is not a file")
        return v.read(p)
    if op == "fs.find":
        for p in a.get("paths") or []:
            try:
                q = v.norm(str(p))
                if v.is_file(q):
                    return {"path": q, "text": v.read(q)}
            except Exception:
                continue
        return None
    if op == "fs.write":
        p = v.norm(str(a.get("path") or ""))
        if jade.tools.get("write"):
            await jade.run_tool("write", {"path": p, "content": str(a.get("content") or "")}, session)
        else:
            v.write(p, str(a.get("content") or ""))
        return p
    if op == "fs.list":
        p = v.norm(str(a.get("path") or "/"))
        if not v.is_dir(p):
            raise LispError(f"list-dir: {p} is not a folder")
        return [x.rstrip("/").rsplit("/", 1)[-1] for x in v.list(p)]
    if op == "fs.exists":
        return v.exists(v.norm(str(a.get("path") or "")))
    if op == "search":
        hits = v.search(str(a.get("query") or ""), int(a.get("limit") or 10))
        return [{"path": h.get("path"), "title": h.get("title"), "snippet": h.get("snippet", "")} for h in hits]
    return await jade_bridge(jade, op, a, via="lisp", session=session)


async def run_lisp(jade, code: str = "", file: str = "", args: list | None = None, fuel: int = 50_000_000, pure: bool = False, session=None, echo=None) -> tuple[str, Any, str]:
    """Run Lisp in Jade. Returns (output, value, error text or '')."""
    async def host(op, a):
        return await jade_host(jade, op, a, session)
    lisp = Lisp(None if pure else host, fuel=fuel, pure=pure, echo=echo)
    lisp.define("*args*", [str(x) for x in (args or [])])
    try:
        if file:
            p = jade.vault.norm(file)
            if not jade.vault.is_file(p):
                raise LispError(f"{p} is not a file")
            lisp.define("*file*", p)
            val = await lisp.run(jade.vault.read(p), p)
        else:
            val = await lisp.run(code)
        return lisp.output(), val, ""
    except LispError as e:
        return lisp.output(), None, err_text(e)


DESCRIPTION = ("Jade Lisp, your own Lisp (read the lisp-dsl skill first). code: forms to evaluate (gives the last value), or file: a .jl file in the vault to run with args (in *args*). "
               "Every tool is callable as (tool/NAME :arg v ...); also jade/ask, jade/flow, jade/shell, tflite/load, tflite/run, read-file, write-file, json-parse. "
               "Build DSLs with defmacro, or run untrusted programs with (sandbox :pure) and eval-in with a fuel limit. fuel: step limit (default 50 million); pure: no tools or files.")
PARAMS = {"type": "object", "properties": {"code": {"type": "string"}, "file": {"type": "string", "description": "a .jl file in the vault"}, "args": {"type": "array", "items": {"type": "string"}},
                                           "fuel": {"type": "number"}, "pure": {"type": "boolean"}}}


async def lisp_tool(ctx, code: str = "", file: str = "", args=None, fuel=None, pure=False, **kw) -> str:
    if not code and not file:
        from .tools.registry import ToolError
        raise ToolError("lisp needs code (or a file)")
    out, val, err = await run_lisp(ctx.jade, code, file, args or [], int(fuel or 50_000_000), pure in (True, "true"), ctx.session)
    out = out.rstrip()
    if err:
        return ((out + "\n") if out else "") + "[exit 1]\nlisp error: " + err
    shown = "" if (val is None and file) else show(val, True)
    text = (out + ("\n" if out and shown else "") + shown) if not file else (out or shown or "(no output)")
    if len(text) > 12000:
        text = text[:12000] + f"\n…({len(text) - 12000} more characters cut)"
    return text or "(no output and no value)"


SEED_VER = 1


def seed(jade) -> int:
    """The DSL library, the examples and the lisp-dsl skill, in the vault (same files as the app; kept up to date while
    they carry the seed marker; a file the user changed and unmarked is theirs)."""
    from importlib import resources
    n = 0
    root = resources.files("jade") / "seed_lisp"
    v = jade.vault

    def walk(node, rel):
        nonlocal n
        for ch in node.iterdir():
            r = rel + "/" + ch.name
            if ch.is_dir():
                walk(ch, r)
                continue
            text = ch.read_text(encoding="utf-8")
            cur = v.read(r) if v.is_file(r) else ""
            if cur:
                mo = re.search(r"jade-lisp-seed v(\d+)", cur)
                if not mo or int(mo.group(1)) >= SEED_VER:
                    continue
            v.write(r, text)
            n += 1
    walk(root, "")
    return n


def setup(jade) -> None:
    from .tools.registry import Tool
    jade.tools.add(Tool("lisp", DESCRIPTION, PARAMS, lisp_tool, core=False,
                        hints="lisp scheme clojure dsl domain specific language macro interpreter s-expression script program eval parse rules language",
                        summary="lisp: Jade Lisp, your own Lisp for DSLs and scripts that call any tool"))
    try:
        seed(jade)
    except Exception:
        pass
