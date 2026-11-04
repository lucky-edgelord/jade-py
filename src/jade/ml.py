"""Models on this machine, as in the Jade Vault app: the onnx tool (ONNX Runtime: the GPU through CUDA, TensorRT,
ROCm, DirectML or CoreML when this machine has one, else the CPU) and the litert tool (LiteRT, the TensorFlow Lite
runtime, for .tflite models). Models come from the vault or a URL (saved to /models); inputs are numbers, nested
lists, vault .json/.f32 files or pictures; each output comes back summarised (shape, min/max/mean, first values,
top 5 with labels), and big outputs are saved to /models/out.

A model file is checked before it is loaded: an error page, a JSON error or a git-LFS pointer saved under a model's
name is reported as what it is, and a download that comes back as one is never saved. Downloading the same model
again reuses the file already in /models."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx
import numpy as np

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

MAX_MODELS = 4
GPU_PROVIDERS = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "ROCMExecutionProvider", "MIGraphXExecutionProvider",
                 "DmlExecutionProvider", "CoreMLExecutionProvider", "OpenVINOExecutionProvider"]


# ================================================================ model files
def model_check(data: bytes, kind: str, where: str) -> None:
    """Raise a clear error when `data` is not a .tflite / .onnx model (an error page, JSON, a git-LFS pointer…)."""
    n = len(data or b"")
    ext = ".tflite" if kind == "tflite" else ".onnx"

    def what(m: str) -> ToolError:
        return ToolError(f"{where} is not a {ext} model: {m}")
    if n < 16:
        raise what(f"it is only {n} bytes" if n else "it is empty")
    head = data[:400].decode("utf-8", "replace")
    snip = json.dumps(re.sub(r"\s+", " ", head).strip()[:160])
    text = "" if re.search(r"[\x00-\x08\x0e-\x1f]", head[:64]) else head.lstrip("﻿").lstrip()
    if re.match(r"version https://git-lfs", text, re.I):
        raise what("it is a git-LFS pointer file, not the model itself (download the file's \"resolve\" or raw link instead) " + snip)
    if re.match(r"<\?xml|<!doctype|<html|<error|<[a-z]", text, re.I):
        kind_ = "XML" if re.match(r"<\?xml|<error", text, re.I) else "HTML"
        raise what(f"it is an {kind_} page ({n} bytes), usually a server's error answer: {snip}")
    if re.match(r"[{\[]\s*[\"{\[\]]", text):
        raise what("it is a JSON text, usually an error answer: " + snip)
    if kind == "tflite" and data[4:8] != b"TFL3":
        raise what(f"bytes 4 to 8 should be \"TFL3\" and are {data[4:8]!r} ({n} bytes)")


def save_unique(jade: "Jade", folder: str, name: str, data: bytes, reuse: bool = False) -> str:
    """Save under folder/name, or name-2, name-3 … when taken; with reuse, an identical file already there is kept."""
    v = jade.vault
    stem, ext = os.path.splitext(name)
    p, i = f"{folder}/{name}", 2
    while v.exists(p):
        if reuse and v.is_file(p):
            try:
                if v.stat(p).st_size == len(data) and v.read_bytes(p) == data:
                    return p
            except OSError:
                pass
        p, i = f"{folder}/{stem}-{i}{ext}", i + 1
    v.write(p, data)
    return p


async def model_bytes(jade: "Jade", model: str, kind: str) -> tuple[bytes, str]:
    """(bytes, vault path) of a model given as a vault path or an http(s) URL (downloaded to /models)."""
    name = str(model or "").strip()
    if not name:
        raise ToolError(f"model is required: a .{kind} path in the vault, or a URL to download it from")
    if re.match(r"https?://", name, re.I):
        try:
            async with httpx.AsyncClient(timeout=600, follow_redirects=True, headers={"User-Agent": jade.settings.user_agent}) as c:
                r = await c.get(name, headers=jade.settings.fleet_headers(name))
        except httpx.HTTPError as e:
            raise ToolError(f"download failed: {e}") from None
        if r.status_code >= 400:
            raise ToolError(f"download failed ({r.status_code})")
        data = r.content
        url = name.split("?")[0]
        model_check(data, kind, "the download from " + url)
        fn = unquote(urlsplit(name).path.rsplit("/", 1)[-1]) or f"model.{kind}"
        if not fn.lower().endswith("." + kind):
            fn += "." + kind
        return data, save_unique(jade, "/models", re.sub(r"[\\/:*?\"<>|]+", "_", fn), data, reuse=True)
    p = jade.vault.norm(name)
    if not jade.vault.is_file(p):
        raise ToolError(f"{name} is not in the vault")
    data = jade.vault.read_bytes(p)
    model_check(data, kind, p)
    return data, p


# ================================================================ inputs and outputs
NP_DTYPES = {"float32": np.float32, "float16": np.float16, "float64": np.float64, "int8": np.int8, "uint8": np.uint8, "int16": np.int16,
             "int32": np.int32, "int64": np.int64, "uint16": np.uint16, "bool": np.bool_}


def _labels(jade: "Jade", a: dict) -> list[str] | None:
    if not a.get("labels"):
        return None
    p = jade.vault.norm(str(a["labels"]))
    if not jade.vault.is_file(p):
        raise ToolError(f"labels file {a['labels']} is not in the vault")
    return jade.vault.read(p).splitlines()


def image_tensor(jade: "Jade", spec: dict, shape: list[int], dtype: str, quant: tuple[float, int] | None = None) -> tuple[np.ndarray, str]:
    """A picture from the vault, resized and converted for the model's input (NHWC or NCHW)."""
    try:
        from PIL import Image
    except ImportError:
        raise ToolError("pictures need Pillow: pip install pillow") from None
    p = jade.vault.norm(str(spec["image"]))
    if not jade.vault.is_file(p):
        raise ToolError(f"{spec['image']}: no such picture in the vault")
    im = Image.open(BytesIO(jade.vault.read_bytes(p))).convert("RGB")
    layout = (spec.get("layout") or ("nchw" if len(shape) == 4 and shape[1] == 3 and shape[3] != 3 else "nhwc")).lower()
    if spec.get("size"):
        h, w = int(spec["size"][0]), int(spec["size"][-1])
    elif len(shape) == 4:
        h, w = (shape[2], shape[3]) if layout == "nchw" else (shape[1], shape[2])
    else:
        h = w = 224
    h, w = (h if h and h > 0 else 224), (w if w and w > 0 else 224)
    fit = (spec.get("fit") or "crop").lower()
    if fit == "stretch":
        im = im.resize((w, h), Image.BILINEAR)
    elif fit == "letterbox":
        s = min(w / im.width, h / im.height)
        r = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.BILINEAR)
        bg = Image.new("RGB", (w, h), (0, 0, 0))
        bg.paste(r, ((w - r.width) // 2, (h - r.height) // 2))
        im = bg
    else:
        s = max(w / im.width, h / im.height)
        r = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.BILINEAR)
        x, y = (r.width - w) // 2, (r.height - h) // 2
        im = r.crop((x, y, x + w, y + h))
    px = np.asarray(im, dtype=np.float32)   # h, w, 3 in 0..255
    integer = dtype in ("uint8", "int8")
    norm = (spec.get("norm") or ("none" if integer else "minus1_1")).lower()
    if integer and not spec.get("norm"):
        arr = px if dtype == "uint8" else px - 128
    else:
        if norm == "zero_1":
            arr = px / 255
        elif norm == "imagenet":
            arr = (px / 255 - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        elif norm == "none":
            arr = px
        else:
            arr = px / 127.5 - 1
        if integer and quant and quant[0]:
            arr = np.round(arr / quant[0]) + quant[1]
    if integer:
        lo, hi = (-128, 127) if dtype == "int8" else (0, 255)
        arr = np.clip(np.round(arr), lo, hi)
    arr = arr.astype(NP_DTYPES.get(dtype, np.float32))
    if layout == "nchw":
        arr = arr.transpose(2, 0, 1)
    arr = arr[None]
    return arr, f"{os.path.basename(p)} → {w}x{h} {layout.upper()} {dtype} ({'raw pixels' if integer and not spec.get('norm') else norm})"


def tensor_data(jade: "Jade", spec: Any, shape: list[int], dtype: str, quant: tuple[float, int] | None = None) -> tuple[np.ndarray, str]:
    """An input from the tool's arguments: {data, shape?, dtype?, real?}, {image: …}, plain numbers or a vault path."""
    if isinstance(spec, dict) and spec.get("image"):
        return image_tensor(jade, spec, shape, spec.get("dtype") or dtype, quant)
    data = spec.get("data") if isinstance(spec, dict) else spec
    dtype = (spec.get("dtype") if isinstance(spec, dict) else None) or dtype
    npd = NP_DTYPES.get(dtype, np.float32)
    note = ""
    if isinstance(data, str):
        p = jade.vault.norm(data)
        if not jade.vault.is_file(p):
            raise ToolError(f"{data} is not in the vault")
        if p.lower().endswith(".json"):
            data = json.loads(jade.vault.read(p))
        else:
            arr = np.frombuffer(jade.vault.read_bytes(p), dtype=npd).copy()
            data, note = arr, f"{os.path.basename(p)}: {arr.size} raw {dtype} values"
    arr = np.asarray(data, dtype=np.float64 if dtype in ("uint8", "int8") else None)
    if dtype in ("uint8", "int8"):
        lo, hi = (-128, 127) if dtype == "int8" else (0, 255)
        if isinstance(spec, dict) and spec.get("real") and quant and quant[0]:
            arr = np.round(arr / quant[0]) + quant[1]
        arr = np.clip(np.round(arr), lo, hi)
    arr = arr.astype(npd)
    want = (spec.get("shape") if isinstance(spec, dict) else None) or shape
    want = [int(d) if d is not None else -1 for d in want]
    if want:
        free = [i for i, d in enumerate(want) if d is None or d < 0]
        known = int(np.prod([d for d in want if d and d > 0])) if any(d and d > 0 for d in want) else 1
        if len(free) > 1:
            if arr.ndim == len(want):
                return arr, note
            raise ToolError(f"the input has a flexible shape {want}: give shape")
        if free:
            want[free[0]] = arr.size // known if known else arr.size
        if int(np.prod(want)) != arr.size:
            raise ToolError(f"the data has {arr.size} values but the input's shape {want} needs {int(np.prod(want))}")
        arr = arr.reshape(want)
    return arr, note


def _r(x: float, n: int = 5) -> float:
    return float(round(float(x), n))


def summarise(jade: "Jade", mid: str, i: int, name: str, v: np.ndarray, labels, offset: int, save: bool) -> dict:
    flat = np.asarray(v).astype(np.float64).ravel()
    n = int(flat.size)
    o: dict = {"index": i, "name": name, "shape": list(np.shape(v)), "dtype": str(np.asarray(v).dtype), "count": n}
    if n:
        o.update(min=_r(flat.min()), max=_r(flat.max()), mean=_r(flat.mean()), first=[_r(x) for x in flat[:16]])
    if 1 < n <= 100_000:
        top = np.argsort(-flat)[:5]
        o["top5"] = [{"index": int(j), **({"label": labels[j + offset].strip()} if labels and 0 <= j + offset < len(labels) else {}), "value": _r(flat[j])} for j in top]
    if n > 16 or save:
        body = json.dumps({"shape": o["shape"], "data": [round(float(x), 6) for x in flat]}).encode()
        o["saved"] = save_unique(jade, "/models/out", f"{mid}-{time.strftime('%Y%m%d-%H%M%S')}-out{i}.json", body)
    return o


def _dims(shape) -> list[int]:
    return [d if isinstance(d, int) and d >= 0 else -1 for d in (shape or [])]


def _pick(given: Any, names: list[str]) -> list:
    if isinstance(given, dict) and any(n in given for n in names):
        return [given.get(n) for n in names]
    if isinstance(given, list):
        return given
    return [given] if given is not None else []


# ================================================================ onnx
ORT_TYPES = {"tensor(float)": "float32", "tensor(float16)": "float16", "tensor(double)": "float64", "tensor(int8)": "int8", "tensor(uint8)": "uint8",
             "tensor(int16)": "int16", "tensor(int32)": "int32", "tensor(int64)": "int64", "tensor(uint16)": "uint16", "tensor(bool)": "bool"}


class Onnx:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.models: dict[str, dict] = {}

    @staticmethod
    def ort():
        try:
            import onnxruntime as ort
        except ImportError:
            raise ToolError("ONNX Runtime is not installed: pip install onnxruntime (or onnxruntime-gpu for CUDA / TensorRT)") from None
        ort.set_default_logger_severity(3)
        return ort

    def providers(self, accelerator: str = "") -> list[str]:
        ort = self.ort()
        have = ort.get_available_providers()
        acc = (accelerator or "").strip()
        if acc.lower() in ("cpu", "wasm"):
            return ["CPUExecutionProvider"]
        if acc and acc.lower() not in ("gpu", "webgpu", "auto"):
            want = [acc if acc.endswith("ExecutionProvider") else acc[:1].upper() + acc[1:] + "ExecutionProvider"]
        elif self.jade.settings.onnx_providers:
            want = list(self.jade.settings.onnx_providers)
        else:
            want = [p for p in GPU_PROVIDERS if p in have]
        missing = [p for p in want if p not in have]
        if acc and acc.lower() not in ("gpu", "webgpu", "auto", "cpu", "wasm") and missing:
            raise ToolError(f"{missing[0]} is not available here; this ONNX Runtime has: {', '.join(have)}")
        return [p for p in want if p in have and p != "CPUExecutionProvider"] + ["CPUExecutionProvider"]

    def status(self) -> dict:
        ort = self.ort()
        have = ort.get_available_providers()
        return {"runtime": "onnxruntime " + ort.__version__, "providers": have, "gpu": any(p in have for p in GPU_PROVIDERS),
                "threads": os.cpu_count(), "models": [{"id": k, "name": m["name"], "accelerator": m["acc"]} for k, m in self.models.items()]}

    @staticmethod
    def meta(s, which: str) -> list[dict]:
        xs = s.get_inputs() if which == "input" else s.get_outputs()
        return [{"name": x.name, "shape": _dims(x.shape), "dtype": ORT_TYPES.get(x.type, x.type)} for x in xs]

    async def load(self, a: dict) -> dict:
        ort = self.ort()
        data, name = await model_bytes(self.jade, a.get("model"), "onnx")
        provs = self.providers(a.get("accelerator") or "")
        note = None

        def make(p):
            o = ort.SessionOptions()
            o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            return ort.InferenceSession(data, sess_options=o, providers=p)
        try:
            s = await asyncio.to_thread(make, provs)
        except Exception as e:
            if provs == ["CPUExecutionProvider"]:
                raise ToolError(f"ONNX Runtime could not load {name}: {e}") from None
            note = f"{provs[0]} failed ({str(e)[:200]}); using the CPU"
            s = await asyncio.to_thread(make, ["CPUExecutionProvider"])
        acc = s.get_providers()[0]
        if acc != provs[0] and not note and provs[0] != "CPUExecutionProvider":
            note = f"{provs[0]} could not be used; running on {acc}"
        while len(self.models) >= MAX_MODELS:
            self.models.pop(next(iter(self.models)))
        mid = str(a.get("id") or f"x{len(self.models) + 1}-{time.strftime('%H%M%S')}")
        self.models[mid] = {"session": s, "name": name, "acc": acc}
        return {"id": mid, "model": name, "accelerator": acc, "inputs": self.meta(s, "input"), "outputs": self.meta(s, "output"), **({"note": note} if note else {})}

    async def run(self, a: dict) -> dict:
        m = self.get(a)
        s = m["session"]
        ins = self.meta(s, "input")
        given = _pick(a.get("inputs", a.get("input")), [d["name"] for d in ins])
        if not given or given[0] is None:
            raise ToolError(f"inputs is required: one {{data, shape?, dtype?}} per model input ({', '.join(d['name'] for d in ins)}), as a list in order or an object by name; data = numbers, nested lists, a vault path to .json/.f32, or {{image: path}} for a picture")
        feeds, prep = {}, []
        for i, d in enumerate(ins):
            if i >= len(given) or given[i] is None:
                raise ToolError(f"no data for input {d['name']}")
            arr, note = tensor_data(self.jade, given[i], d["shape"], d["dtype"] if d["dtype"] in NP_DTYPES else "float32")
            feeds[d["name"]] = arr
            if note:
                prep.append(note)
        labels = _labels(self.jade, a)
        t0 = time.perf_counter()
        outs = await asyncio.to_thread(s.run, None, feeds)
        ms = round((time.perf_counter() - t0) * 1000)
        names = [o.name for o in s.get_outputs()]
        res = [summarise(self.jade, a["id"], i, names[i], v, labels, int(a.get("label_offset") or 0), bool(a.get("save"))) for i, v in enumerate(outs)]
        return {"ms": ms, "accelerator": m["acc"], **({"prepared": prep} if prep else {}), "outputs": res}

    def get(self, a: dict) -> dict:
        m = self.models.get(str(a.get("id") or ""))
        if not m:
            raise ToolError(f'no loaded model "{a.get("id") or ""}". Load one first: {", ".join(self.models) or "(none loaded)"}')
        return m

    async def tool(self, ctx, action: str = "status", **a) -> str:
        if action == "status":
            return json.dumps(self.status())
        if action == "load":
            return json.dumps(await self.load(a))
        m = self.get(a)
        if action == "info":
            return json.dumps({"id": a["id"], "model": m["name"], "accelerator": m["acc"], "inputs": self.meta(m["session"], "input"), "outputs": self.meta(m["session"], "output")})
        if action == "unload":
            self.models.pop(a["id"], None)
            return "unloaded " + a["id"]
        if action == "run":
            return json.dumps(await self.run(a))
        raise ToolError(f"unknown onnx action {action}")


# ================================================================ litert (TensorFlow Lite)
class LiteRT:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.models: dict[str, dict] = {}

    @staticmethod
    def runtime():
        for mod, label in (("ai_edge_litert.interpreter", "ai-edge-litert"), ("tflite_runtime.interpreter", "tflite-runtime"), ("tensorflow.lite", "tensorflow")):
            try:
                m = __import__(mod, fromlist=["Interpreter"])
                return m.Interpreter, label
            except ImportError:
                continue
        raise ToolError("LiteRT is not installed: pip install ai-edge-litert (or tflite-runtime)")

    @staticmethod
    def details(ds) -> list[dict]:
        out = []
        for d in ds:
            q = d.get("quantization") or (0.0, 0)
            e = {"name": d.get("name"), "shape": [int(x) for x in (d.get("shape_signature") if d.get("shape_signature") is not None and len(d.get("shape_signature")) else d.get("shape"))],
                 "dtype": np.dtype(d.get("dtype")).name}
            if q and q[0]:
                e["quantization"] = {"scale": float(q[0]), "zeroPoint": int(q[1])}
            out.append(e)
        return out

    async def load(self, a: dict) -> dict:
        Interp, label = self.runtime()
        data, name = await model_bytes(self.jade, a.get("model"), "tflite")
        try:
            it = Interp(model_content=data, num_threads=os.cpu_count() or 1)
            it.allocate_tensors()
        except Exception as e:
            raise ToolError(f"LiteRT could not load {name}: {e}") from None
        while len(self.models) >= MAX_MODELS:
            self.models.pop(next(iter(self.models)))
        mid = str(a.get("id") or f"m{len(self.models) + 1}-{time.strftime('%H%M%S')}")
        self.models[mid] = {"it": it, "name": name, "acc": "cpu", "runtime": label}
        return {"id": mid, "model": name, "accelerator": "cpu", "inputs": self.details(it.get_input_details()), "outputs": self.details(it.get_output_details())}

    def get(self, a: dict) -> dict:
        m = self.models.get(str(a.get("id") or ""))
        if not m:
            raise ToolError(f'no loaded model "{a.get("id") or ""}". Load one first: {", ".join(self.models) or "(none loaded)"}')
        return m

    async def run(self, a: dict) -> dict:
        m = self.get(a)
        it = m["it"]
        ins = it.get_input_details()
        given = _pick(a.get("inputs", a.get("input")), [d.get("name") for d in ins])
        if not given or given[0] is None:
            raise ToolError("inputs is required: a list with one {data, shape?, dtype?} per model input (data = numbers, nested lists, or a vault path to .json/.f32), or {image: path} for a picture")
        prep, resized = [], False
        arrays = []
        for i, d in enumerate(ins):
            if i >= len(given) or given[i] is None:
                raise ToolError(f"no data for input {i} ({d.get('name')})")
            q = d.get("quantization") or (0.0, 0)
            sig = list(d.get("shape_signature")) if d.get("shape_signature") is not None and len(d.get("shape_signature")) else list(d["shape"])
            arr, note = tensor_data(self.jade, given[i], [int(x) for x in sig], np.dtype(d["dtype"]).name, (float(q[0]), int(q[1])) if q and q[0] else None)
            if list(arr.shape) != list(d["shape"]):
                it.resize_tensor_input(d["index"], list(arr.shape))
                resized = True
            arrays.append(arr)
            if note:
                prep.append(note)
        if resized:
            it.allocate_tensors()
        for d, arr in zip(ins, arrays):
            it.set_tensor(d["index"], arr)
        t0 = time.perf_counter()
        await asyncio.to_thread(it.invoke)
        ms = round((time.perf_counter() - t0) * 1000)
        labels = _labels(self.jade, a)
        res = []
        for i, d in enumerate(it.get_output_details()):
            v = it.get_tensor(d["index"])
            q = d.get("quantization") or (0.0, 0)
            if q and q[0] and not a.get("raw_output") and v.dtype in (np.int8, np.uint8):
                v = (v.astype(np.float32) - q[1]) * q[0]
            res.append(summarise(self.jade, a["id"], i, d.get("name") or f"out{i}", v, labels, int(a.get("label_offset") or 0), bool(a.get("save"))))
        return {"ms": ms, "accelerator": m["acc"], **({"prepared": prep} if prep else {}), "outputs": res}

    async def tool(self, ctx, action: str = "status", **a) -> str:
        if action == "status":
            try:
                _, label = self.runtime()
            except ToolError as e:
                return json.dumps({"runtime": None, "error": str(e), "models": []})
            return json.dumps({"runtime": label, "accelerator": "cpu", "threads": os.cpu_count(), "models": [{"id": k, "name": m["name"], "accelerator": m["acc"]} for k, m in self.models.items()]})
        if action == "load":
            return json.dumps(await self.load(a))
        m = self.get(a)
        if action == "info":
            return json.dumps({"id": a["id"], "model": m["name"], "accelerator": m["acc"], "inputs": self.details(m["it"].get_input_details()), "outputs": self.details(m["it"].get_output_details())})
        if action == "unload":
            self.models.pop(a["id"], None)
            return "unloaded " + a["id"]
        if action == "run":
            return json.dumps(await self.run(a))
        raise ToolError(f"unknown litert action {action}")


# ================================================================ tool definitions
_INPUTS = ("one {data, shape?, dtype?} per model input, as a list in order or an object by input name; data = numbers, nested lists, a vault path to .json or raw .f32, "
           "or {image: vault path, size?: [h, w], fit?: crop|stretch|letterbox, norm?: minus1_1|zero_1|imagenet|none, layout?: nhwc|nchw} for a picture "
           "(resized and converted for the model; uint8/int8 models get raw pixels unless norm says otherwise)")
ONNX_SPEC = {
    "description": "ONNX Runtime: run any .onnx model on this machine, on its GPU (CUDA, TensorRT, ROCm, DirectML, CoreML) when it has one, else the CPU. "
                   "status: the runtime, its execution providers and loaded models. load: model is a vault path or an https URL (downloaded to /models); returns an id and each input and output "
                   "(name, dtype, shape; -1 = flexible); accelerator gpu (default when there is one), cpu, or a provider name (cuda, tensorrt…). run: id and inputs (" + _INPUTS + "); "
                   "returns each output (shape, min/max/mean, first values, top5 with labels from a labels file) and saves big outputs to /models/out. info, unload. "
                   "A file that is not a model (an error page, a git-LFS pointer) is reported as what it is.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "load", "run", "info", "unload"]}, "model": {"type": "string"}, "id": {"type": "string"},
        "accelerator": {"type": "string", "description": "gpu (default when available), cpu, or a provider (cuda, tensorrt, rocm, coreml, dml, openvino)"},
        "inputs": {"description": "a list (in input order) or an object by input name"}, "labels": {"type": "string", "description": "vault path of a labels file, one per line"},
        "label_offset": {"type": "number"}, "save": {"type": "boolean"}}, "required": ["action"]}}
LITERT_SPEC = {
    "description": "LiteRT (TensorFlow Lite): run any .tflite model on this machine's CPU. status, load (model: a vault path or an https URL, downloaded to /models; returns an id and the "
                   "input/output shapes, dtypes and quantization), run (id and inputs: " + _INPUTS + "), info, unload. Quantized int8/uint8 models work: outputs are converted back to "
                   "real numbers with the model's scale and zero point (raw_output: true keeps the integers), and {data, real: true} quantizes real inputs the same way. "
                   "labels (a vault text file, one per line; label_offset 1 if the model has a background class first) names the top 5. Big outputs are saved to /models/out.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "load", "run", "info", "unload"]}, "model": {"type": "string"}, "id": {"type": "string"},
        "inputs": {"description": "a list (in input order) or an object by input name"}, "raw_output": {"type": "boolean"}, "labels": {"type": "string"},
        "label_offset": {"type": "number"}, "save": {"type": "boolean"}}, "required": ["action"]}}


def setup(jade: "Jade") -> None:
    jade.onnx = Onnx(jade)
    jade.litert = LiteRT(jade)
    jade.tools.add(Tool("onnx", ONNX_SPEC["description"], ONNX_SPEC["parameters"], jade.onnx.tool, core=False, enabled=lambda j: j.settings.onnx_on,
                        hints="onnx model inference neural network classify detect embed run model gpu cuda tensorrt",
                        summary="onnx: run .onnx models on this machine's GPU or CPU"))
    jade.tools.add(Tool("litert", LITERT_SPEC["description"], LITERT_SPEC["parameters"], jade.litert.tool, core=False, enabled=lambda j: j.settings.litert_on,
                        hints="tflite litert tensorflow lite model inference classify quantized int8",
                        summary="litert: run .tflite models on this machine"))
