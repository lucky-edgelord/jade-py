"""What came over from Jade Vault 2.18-2.22: decisions from Ollama, web calls of one round run in parallel, the
onnx / litert / usb tools (with model-file checks and download reuse), and Jade Fleet (vault_search, vision, the
broker as the way to every model)."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from jade import ml
from jade import reasoning as R

FIX = Path(__file__).parent / "fixtures"
XML_ERR = "<?xml version='1.0' encoding='UTF-8'?><Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>"


class Server:
    """A tiny HTTP server whose answers a test sets: routes[(method, path)] = fn(handler, body) -> (status, type, bytes)."""
    def __init__(self):
        self.routes: dict = {}
        self.seen: list[dict] = []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                path, _, query = self.path.partition("?")
                body = self.rfile.read(int(self.headers.get("content-length") or 0)) if method == "POST" else b""
                me.seen.append({"method": method, "path": path, "query": query, "headers": dict(self.headers), "body": body})
                fn = me.routes.get((method, path))
                code, ctype, out = fn(self, body) if fn else (404, "application/json", b'{"ok": false, "error": "no route"}')
                self.send_response(code)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def server():
    s = Server()
    yield s
    s.srv.shutdown()


def J(o) -> bytes:
    return json.dumps(o).encode()


# ---------------------------------------------------------------- decisions from Ollama
async def test_decide_on_ollama(jade, server):
    def systemone(h, body):
        q = json.loads(body)["questions"]["q"]
        return 200, "application/json", J({"model": "judge-o", "answers": {"q": {"choice": q["criteria"][0], "confidence": 0.9, "probabilities": {q["criteria"][0]: 0.9, q["criteria"][1]: 0.1}}}})
    server.routes[("POST", "/v1/systemone")] = systemone
    st = jade.settings
    st.ollaya_model, st.decide_provider, st.host = "judge-o", "ollama", server.url
    r = await jade.run_tool("decide", {"state": "s", "question": "q", "type": "choice", "options": ["a", "b"]})
    assert r.startswith("choice: a") and server.seen[-1]["path"] == "/v1/systemone"   # the Ollama address (host), since decide_ollama_host is empty
    st.decide_ollama_host = "http://127.0.0.1:9"   # nothing listens there
    with pytest.raises(RuntimeError, match="could not reach Ollama at http://127.0.0.1:9. Is Ollama 0.35 or newer running"):
        await jade.run_tool("decide", {"state": "s", "question": "q", "type": "yesno"})
    st.decide_ollama_host = server.url
    server.routes[("POST", "/v1/systemone")] = lambda h, b: (404, "application/json", J({"error": "404 page not found"}))
    with pytest.raises(RuntimeError, match="Ollama said 404: 404 page not found .decisions need Ollama 0.35 or newer"):
        await jade.run_tool("decide", {"state": "s", "question": "q", "type": "yesno"})
    st.decide_provider = "ollaya"
    assert R.decide_server(st)[0] == "Ollaya"


# ---------------------------------------------------------------- web calls of one round run at the same time
async def test_web_calls_run_in_parallel(jade, fake):
    running, peak, order = 0, 0, []

    async def slow_fetch(ctx, url, **_):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.4)
        running -= 1
        order.append(url)
        return "page " + url
    jade.tools.get("web_fetch").fn = slow_fetch
    fake.script = [{"tool_calls": [("web_fetch", {"url": "https://a.test"}), ("web_fetch", {"url": "https://b.test"}), ("web_fetch", {"url": "https://c.test"})]},
                   {"content": "done"}]
    s = jade.session()
    t0 = time.monotonic()
    assert await s.ask("read three pages") == "done"
    took = time.monotonic() - t0
    assert peak == 3 and took < 1.0, (peak, took)            # 3 x 0.4 s one after another would be 1.2 s
    tools = [m for m in s.messages if m.get("role") == "tool"]
    assert [m["content"] for m in tools] == ["page https://a.test", "page https://b.test", "page https://c.test"]   # in the order asked

    # step by step: an edited call runs with its new arguments, a skipped one never reports back
    order.clear()
    fake.script = [{"tool_calls": [("web_fetch", {"url": "https://x.test"}), ("web_fetch", {"url": "https://y.test"}), ("web_fetch", {"url": "https://z.test"})]},
                   {"content": "ok"}]
    st = s.stepper("again")
    await st.step()
    ids = [c.id for c in st.pending]
    await st.step()                                           # x runs; y and z start early
    st.edit(ids[1], {"url": "https://y2.test"})
    st.skip(ids[2])
    await st.step()
    out = [m["content"] for m in s.messages if m.get("role") == "tool"][-3:]
    assert out == ["page https://x.test", "Not run: skipped by the user.", "page https://y2.test"], out
    assert order[-1] == "https://y2.test"   # the edited call ran again with its new arguments; the early y result was dropped


def test_unreadable_note_does_not_break_the_index(jade):
    v = jade.vault
    v.write("/A.md", "see [[B]] #tag")
    v.write("/B.md", "b")
    (v.root / "Gone.md").symlink_to(v.root / "nowhere.md")   # listed, cannot be read
    idx = v.index()
    assert "/B.md" in idx.out["/A.md"] and "tag" in idx.tags


# ---------------------------------------------------------------- model files
def test_model_check():
    with pytest.raises(ml.ToolError, match="XML page .*NoSuchKey"):
        ml.model_check(XML_ERR.encode(), "tflite", "/models/bad.tflite")
    with pytest.raises(ml.ToolError, match="HTML page"):
        ml.model_check(b"<!DOCTYPE html><html><body>404 Not Found</body></html>", "onnx", "x")
    with pytest.raises(ml.ToolError, match="git-LFS pointer"):
        ml.model_check(b"version https://git-lfs.github.com/spec/v1\noid sha256:4d7a\nsize 14000000\n", "onnx", "x")
    with pytest.raises(ml.ToolError, match="JSON text"):
        ml.model_check(b'{"error":"Repository not found","status":401}', "onnx", "x")
    with pytest.raises(ml.ToolError, match='should be "TFL3"'):
        ml.model_check(bytes((i * 37 + 3) & 255 for i in range(64)), "tflite", "x")
    with pytest.raises(ml.ToolError, match="only 3 bytes"):
        ml.model_check(b"abc", "onnx", "x")
    ml.model_check((FIX / "tiny_matmul.onnx").read_bytes(), "onnx", "x")
    ml.model_check((FIX / "add.tflite").read_bytes(), "tflite", "x")


# ---------------------------------------------------------------- onnx
async def test_onnx(jade, server):
    T = lambda a: jade.run_tool("onnx", a)   # noqa: E731
    jade.vault.write("/models/tiny.onnx", (FIX / "tiny_matmul.onnx").read_bytes())
    st = json.loads(await T({"action": "status"}))
    assert st["runtime"].startswith("onnxruntime ") and "CPUExecutionProvider" in st["providers"]
    ld = json.loads(await T({"action": "load", "model": "/models/tiny.onnx", "id": "tiny"}))
    assert ld["inputs"][0] == {"name": "X", "shape": [-1, 4], "dtype": "float32"} and ld["outputs"][0]["name"] == "Y"
    r1 = json.loads(await T({"action": "run", "id": "tiny", "inputs": [{"data": [[1, 2, 3, 4], [0, 0, 0, 1]]}]}))
    assert r1["outputs"][0]["first"] == [5.5, 5, 17, 1.5, 0, 11] and r1["outputs"][0]["shape"] == [2, 3]   # Y = X·W + B, batch from the data
    r2 = json.loads(await T({"action": "run", "id": "tiny", "inputs": {"X": {"data": [1, 2, 3, 4]}}}))
    assert r2["outputs"][0]["first"] == [5.5, 5, 17]
    jade.vault.write("/in/x.json", "[[2, 0, 0, 0]]")
    jade.vault.write("/in/labels.txt", "low\nmiddle\nhigh\n")
    r3 = json.loads(await T({"action": "run", "id": "tiny", "inputs": [{"data": "/in/x.json"}], "labels": "/in/labels.txt"}))
    assert r3["outputs"][0]["first"] == [2.5, -1, 10] and r3["outputs"][0]["top5"][0] == {"index": 2, "label": "high", "value": 10.0}
    big = json.loads(await T({"action": "run", "id": "tiny", "inputs": [{"data": [[1, 1, 1, 1]] * 8}]}))
    assert big["outputs"][0]["saved"].startswith("/models/out/tiny-") and jade.vault.is_file(big["outputs"][0]["saved"])
    with pytest.raises(RuntimeError, match="needs"):
        await T({"action": "run", "id": "tiny", "inputs": [{"data": [1, 2, 3]}], })
    cpu = json.loads(await T({"action": "load", "model": "/models/tiny.onnx", "id": "c", "accelerator": "cpu"}))
    assert cpu["accelerator"] == "CPUExecutionProvider"
    with pytest.raises(RuntimeError, match="TensorrtExecutionProvider is not available here"):
        await T({"action": "load", "model": "/models/tiny.onnx", "accelerator": "tensorrt"})
    assert "unloaded c" == await T({"action": "unload", "id": "c"})
    with pytest.raises(RuntimeError, match='no loaded model "c"'):
        await T({"action": "run", "id": "c", "inputs": [{"data": [1, 2, 3, 4]}]})
    assert json.loads(await jade.run_tool("onnx", {"action": "status"}))["models"][0]["id"] == "tiny"

    # files and downloads that are not models
    jade.vault.write("/models/bad.onnx", "<!DOCTYPE html><html><body>404</body></html>")
    with pytest.raises(RuntimeError, match=r"/models/bad.onnx is not a .onnx model: it is an HTML page"):
        await T({"action": "load", "model": "/models/bad.onnx"})
    model = (FIX / "tiny_matmul.onnx").read_bytes()
    server.routes[("GET", "/m/tiny.onnx")] = lambda h, b: (200, "application/octet-stream", model)
    server.routes[("GET", "/m/mobilenet_v2.onnx")] = lambda h, b: (200, "application/xml", XML_ERR.encode())
    a = json.loads(await T({"action": "load", "model": server.url + "/m/tiny.onnx", "id": "d1"}))
    b = json.loads(await T({"action": "load", "model": server.url + "/m/tiny.onnx", "id": "d2"}))
    assert a["model"] == b["model"] == "/models/tiny.onnx"           # the identical file already there is reused
    assert not jade.vault.exists("/models/tiny-2.onnx")
    with pytest.raises(RuntimeError, match=r"the download from .*/m/mobilenet_v2.onnx is not a .onnx model: it is an XML page"):
        await T({"action": "load", "model": server.url + "/m/mobilenet_v2.onnx"})
    assert not jade.vault.exists("/models/mobilenet_v2.onnx")        # an error answer is never saved as a model
    assert "onnx" in [t.name for t in jade.tools.active(jade)]


def test_image_inputs(jade):
    from PIL import Image
    from io import BytesIO
    b = BytesIO()
    Image.new("RGB", (40, 20), (255, 0, 0)).save(b, "PNG")
    jade.vault.write("/pics/red.png", b.getvalue())
    arr, note = ml.image_tensor(jade, {"image": "/pics/red.png"}, [1, 8, 8, 3], "float32")
    assert arr.shape == (1, 8, 8, 3) and np.allclose(arr[0, 0, 0], [1, -1, -1]) and "NHWC" in note
    arr, _ = ml.image_tensor(jade, {"image": "/pics/red.png", "norm": "zero_1"}, [1, 3, 6, 6], "float32")
    assert arr.shape == (1, 3, 6, 6) and np.allclose(arr[0, :, 0, 0], [1, 0, 0])
    arr, _ = ml.image_tensor(jade, {"image": "/pics/red.png"}, [1, 4, 4, 3], "uint8")
    assert arr.dtype == np.uint8 and arr[0, 0, 0].tolist() == [255, 0, 0]
    arr, _ = ml.image_tensor(jade, {"image": "/pics/red.png", "fit": "letterbox"}, [1, 10, 10, 3], "uint8")
    assert arr[0, 0, 5].tolist() == [0, 0, 0] and arr[0, 5, 5].tolist() == [255, 0, 0]   # bars above and below


# ---------------------------------------------------------------- litert (a stand-in interpreter; the real one is ai-edge-litert)
class FakeInterpreter:
    """out = (in0 + in1), with a uint8 quantized output (scale 0.5, zero point 10)."""
    def __init__(self, model_content=None, num_threads=None):
        assert model_content[4:8] == b"TFL3"
        self.shape = [1, 4]
        self.t: dict = {}

    def allocate_tensors(self):
        pass

    def get_input_details(self):
        return [{"name": f"in{i}", "index": i, "shape": np.array(self.shape), "shape_signature": np.array([-1, 4]), "dtype": np.float32, "quantization": (0.0, 0)} for i in range(2)]

    def get_output_details(self):
        return [{"name": "out", "index": 9, "shape": np.array(self.shape), "shape_signature": np.array([-1, 4]), "dtype": np.uint8, "quantization": (0.5, 10)}]

    def resize_tensor_input(self, i, shape):
        self.shape = list(shape)

    def set_tensor(self, i, v):
        self.t[i] = v

    def invoke(self):
        self.t[9] = np.round((self.t[0] + self.t[1]) / 0.5 + 10).astype(np.uint8)

    def get_tensor(self, i):
        return self.t[i]


async def test_litert(jade, monkeypatch):
    for m in ("ai_edge_litert.interpreter", "ai_edge_litert", "tensorflow.lite", "tensorflow"):
        monkeypatch.setitem(sys.modules, m, None)
    with pytest.raises(RuntimeError, match="pip install ai-edge-litert"):
        await jade.run_tool("litert", {"action": "load", "model": "/x.tflite"})
    assert "pip install ai-edge-litert" in json.loads(await jade.run_tool("litert", {"action": "status"}))["error"]
    mod = types.ModuleType("tflite_runtime.interpreter")
    mod.Interpreter = FakeInterpreter
    monkeypatch.setitem(sys.modules, "tflite_runtime", types.ModuleType("tflite_runtime"))
    monkeypatch.setitem(sys.modules, "tflite_runtime.interpreter", mod)
    jade.vault.write("/models/add.tflite", (FIX / "add.tflite").read_bytes())
    jade.vault.write("/models/bad.tflite", XML_ERR)
    with pytest.raises(RuntimeError, match="bad.tflite is not a .tflite model: it is an XML page"):
        await jade.run_tool("litert", {"action": "load", "model": "/models/bad.tflite"})
    ld = json.loads(await jade.run_tool("litert", {"action": "load", "model": "/models/add.tflite", "id": "adder"}))
    assert ld["accelerator"] == "cpu" and ld["outputs"][0]["quantization"] == {"scale": 0.5, "zeroPoint": 10}
    r = json.loads(await jade.run_tool("litert", {"action": "run", "id": "adder", "inputs": [{"data": [1, 2, 3, 4]}, {"data": [10, 20, 30, 40]}]}))
    assert r["outputs"][0]["first"] == [11, 22, 33, 44]                # dequantized with the model's scale and zero point
    raw = json.loads(await jade.run_tool("litert", {"action": "run", "id": "adder", "inputs": [{"data": [[1, 1, 1, 1], [2, 2, 2, 2]]}, {"data": [[0, 0, 0, 0], [0, 0, 0, 0]]}], "raw_output": True}))
    assert raw["outputs"][0]["shape"] == [2, 4] and raw["outputs"][0]["first"][:5] == [12, 12, 12, 12, 14]   # resized to a batch of 2, raw integers
    assert json.loads(await jade.run_tool("litert", {"action": "status"}))["runtime"] == "tflite-runtime"


# ---------------------------------------------------------------- usb (stand-in devices with PyUSB's interface)
class EP:
    def __init__(self, addr, attrs, size):
        self.bEndpointAddress, self.bmAttributes, self.wMaxPacketSize = addr, attrs, size


class Intf(list):
    def __init__(self, n, cls, eps):
        super().__init__(eps)
        self.bInterfaceNumber, self.bAlternateSetting, self.bInterfaceClass = n, 0, cls


class FakeDev:
    def __init__(self, vid, pid, product, address):
        self.idVendor, self.idProduct, self.product, self.manufacturer, self.serial_number = vid, pid, product, "Arduino (www.arduino.cc)", "7573"
        self.bus, self.address, self.bDeviceClass = 1, address, 2
        self.cfg = [Intf(0, 2, [EP(0x82, 3, 8)]), Intf(1, 10, [EP(0x04, 2, 64), EP(0x83, 2, 64)])]
        self.stored, self.claimed, self.calls = b"", set(), []

    def __iter__(self):
        return iter([self.cfg])

    def get_active_configuration(self):
        return self.cfg

    def set_configuration(self):
        pass

    def is_kernel_driver_active(self, n):
        return n == 0

    def detach_kernel_driver(self, n):
        self.calls.append(("detach", n))

    def claim_interface(self, n):
        self.claimed.add(n)

    def release_interface(self, n):
        self.claimed.discard(n)

    def ctrl_transfer(self, bm, req, value, index, data_or_length, timeout):
        self.calls.append(("ctrl", bm, req, value, index))
        if bm == 0x80 and req == 6 and value == 0x100:
            return bytes([18, 1, 0x10, 1, 2, 0, 0, 8, 0x41, 0x23, 0x43, 0, 1, 0, 1, 2, 3, 1])[:data_or_length]
        if bm & 0x80:
            raise OSError("[Errno 32] Pipe error")
        return len(data_or_length)

    def write(self, ep, data, timeout):
        assert 1 in self.claimed
        self.stored = bytes(data)
        return len(data)

    def read(self, ep, n, timeout):
        assert ep == 0x83
        return self.stored.upper()[:n]


async def test_usb(jade):
    uno, other = FakeDev(0x2341, 0x0043, "Arduino Uno", 2), FakeDev(0x1a86, 0x7523, "USB Serial", 3)
    jade.usb.find_devices = lambda: [uno, other]
    T = lambda a: jade.run_tool("usb", a)   # noqa: E731
    lst = json.loads(await T({"action": "list"}))
    assert lst[0]["vendorProduct"] == "2341:0043" and "4 out bulk 64" in lst[0]["interfaces"][1]["endpoints"] and lst[0]["allowed"]
    jade.settings.usb_allow = ["2341"]
    assert [d["allowed"] for d in json.loads(await T({"action": "list"}))] == [True, False]
    with pytest.raises(RuntimeError, match="no allowed USB device 1a86:7523"):
        await T({"action": "open", "device": "1a86:7523"})
    with pytest.raises(RuntimeError, match="1a86:7523 is not in settings.usb_allow"):
        await T({"action": "request", "vendorId": "1a86"})
    assert "allowed: 1:2 2341:0043 Arduino Uno" == await T({"action": "request", "vendorId": "2341"})
    with pytest.raises(RuntimeError, match="open the device first"):
        await T({"action": "transfer_in", "endpoint": 3})
    assert "opened Arduino Uno (1:2)" == await T({"action": "open", "device": "uno"})
    with pytest.raises(RuntimeError, match="no out endpoint 4 on a claimed interface"):
        await T({"action": "transfer_out", "endpoint": 4, "text": "x"})
    assert "claimed interface 1" == await T({"action": "claim", "interface": 1})
    assert json.loads(await T({"action": "transfer_out", "endpoint": 4, "text": "ping"}))["bytesWritten"] == 4
    inn = json.loads(await T({"action": "transfer_in", "endpoint": 3, "length": 16, "save": "/usb/reply.bin"}))
    assert inn["text"] == "PING" and inn["hex"] == "50 49 4e 47" and jade.vault.read("/usb/reply.bin") == "PING"
    assert json.loads(await T({"action": "transfer_out", "endpoint": 4, "data": "68 69 0a"}))["bytesWritten"] == 3
    assert json.loads(await T({"action": "transfer_in", "endpoint": 3}))["hex"] == "48 49 0a"
    ci = json.loads(await T({"action": "control_in", "requestType": "standard", "request": 6, "value": 256, "length": 18}))
    assert ci["bytes"] == 18 and ci["hex"].startswith("12 01 10 01")
    assert json.loads(await T({"action": "control_in", "request": 99, "length": 4}))["status"] == "stall"
    assert json.loads(await T({"action": "control_out", "request": 1, "value": 2, "data": [1, 2]}))["bytesWritten"] == 2
    assert uno.calls[-1] == ("ctrl", 0x40, 1, 2, 0)                   # vendor, device, out
    with pytest.raises(RuntimeError, match="hex bytes"):
        await T({"action": "transfer_out", "endpoint": 4, "data": "zz"})
    assert "closed Arduino Uno" == await T({"action": "close"})
    assert jade.tools.get("usb").risky


# ---------------------------------------------------------------- Jade Fleet
async def test_fleet(jade, server, fake):
    from jade.fleet import fleet_preset
    from jade.providers import make_provider
    st = jade.settings
    assert "vault_search" not in [t.name for t in jade.tools.active(jade)]
    fleet_preset(st, server.url + "/", "tok-1", "lab-pc")
    assert st.fleet_url == server.url and st.provider == "custom" and st.base_url == server.url + "/v1" and st.host == st.embed_host == server.url + "/ollama"
    assert {"vault_search", "vision"} <= {t.name for t in jade.tools.active(jade)}
    assert st.fleet_headers(server.url + "/v1/search") == {"Authorization": "Bearer tok-1", "X-Jade-Device": "lab-pc"}
    assert st.fleet_headers("https://elsewhere.test/v1") == {} and st.fleet_headers(server.url + "evil.test") == {}   # the token goes only to the broker

    server.routes[("GET", "/v1/search")] = lambda h, b: (200, "application/json", J({"hits": [
        {"kind": "note", "id": 1, "source": "/Garden/Tomatoes.md", "content": "Water every morning", "distance": 0.12},
        {"kind": "memory", "id": 7, "source": "preference", "content": "Likes cherry tomatoes", "distance": 0.2}]}))
    r = await jade.run_tool("vault_search", {"query": "watering plants", "k": 50})
    assert "[note] /Garden/Tomatoes.md (distance 0.120)\nWater every morning" in r and "[memory · preference]" in r
    q = server.seen[-1]
    assert "q=watering+plants" in q["query"] and "k=20" in q["query"] and q["headers"]["Authorization"] == "Bearer tok-1" and q["headers"]["X-Jade-Device"] == "lab-pc"

    jade.vault.write("/cam/door.jpg", b"\xff\xd8\xff fake jpeg")
    server.routes[("POST", "/v1/vision/detect")] = lambda h, b: (200, "application/json", J({"model": "yolov8n", "device": "DLA0", "width": 640, "height": 480, "ms": {"total": 9},
                                                                                         "detections": [{"label": "person", "confidence": 0.91, "box": [10.2, 20.7, 110, 300]}]}))
    server.routes[("POST", "/v1/vision/classify")] = lambda h, b: (200, "application/json", J({"model": "mobilenetv2", "device": "DLA1", "width": 640, "height": 480, "top": [{"label": "tabby cat", "prob": 0.8}]}))
    d = await jade.run_tool("vision", {"action": "detect", "path": "/cam/door.jpg"})
    assert "yolov8n on DLA0, 1 object:\nperson 91% at [10, 21, 110, 300]" in d
    assert server.seen[-1]["body"] == b"\xff\xd8\xff fake jpeg" and server.seen[-1]["headers"]["Content-Type"] == "image/jpeg" and "conf=0.25" in server.seen[-1]["query"]
    assert "tabby cat: 80.0%" in await jade.run_tool("vision", {"action": "classify", "path": "/cam/door.jpg", "top": 3})
    with pytest.raises(RuntimeError, match="no such image"):
        await jade.run_tool("vision", {"action": "detect", "path": "/cam/none.jpg"})

    # chat, embeddings and decisions go to the broker with the token
    p = make_provider(st)
    try:
        assert p.headers["Authorization"] == "Bearer tok-1" and p.headers["X-Jade-Device"] == "lab-pc" and p.base == server.url + "/v1"
    finally:
        await p.aclose()
    server.routes[("POST", "/v1/systemone")] = lambda h, b: (200, "application/json", J({"model": "laya", "answers": {"q": {"noul": True, "confidence": 0.9, "probabilities": [0.1, 0.9]}}}))
    st.ollaya_model = "laya"
    o = await R.decide_data(jade, "s", "ok?", "yesno")
    assert o["answer"] is True and server.seen[-1]["headers"]["Authorization"] == "Bearer tok-1"

    server.routes[("GET", "/v1/search")] = lambda h, b: (401, "application/json", J({"ok": False, "error": "unauthorized"}))
    with pytest.raises(RuntimeError, match="the broker refused the fleet token"):
        await jade.run_tool("vault_search", {"query": "x"})
    from jade.backup import SECRET_KEYS
    assert "fleet_token" in SECRET_KEYS
