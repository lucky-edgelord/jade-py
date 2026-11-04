"""USB devices plugged into this machine (Arduinos and other microcontrollers, serial adapters, sensors, custom
devices), through PyUSB and libusb: the usb tool of the Jade Vault app, with the same actions and answers.

Which devices Jade may open is settings.usb_allow (vendor:product or vendor, in hex; empty = any), which she cannot
change. On Linux, reaching a device without root needs a udev rule, e.g. for an Arduino Uno:
  SUBSYSTEM=="usb", ATTR{idVendor}=="2341", ATTR{idProduct}=="0043", MODE="0660", GROUP="plugdev"
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .tools.registry import Tool, ToolError

if TYPE_CHECKING:
    from .core import Jade

REQ_TYPE = {"standard": 0, "class": 1, "vendor": 2}
RECIPIENT = {"device": 0, "interface": 1, "endpoint": 2, "other": 3}
EP_TYPE = {0: "control", 1: "isochronous", 2: "bulk", 3: "interrupt"}


def usb_bytes(a: dict) -> bytes:
    """data: hex bytes ("01 a0 ff"), a list of numbers, or text (text=…)."""
    if a.get("text") is not None:
        return str(a["text"]).encode()
    d = a.get("data")
    if d is None:
        return b""
    if isinstance(d, list):
        try:
            return bytes(int(x) & 255 for x in d)
        except (TypeError, ValueError):
            raise ToolError("data as a list must be numbers 0-255") from None
    s = re.sub(r"0x", "", str(d), flags=re.I)
    s = re.sub(r"[\s,:;-]+", "", s)
    if not s or len(s) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", s):
        raise ToolError('data must be hex bytes ("01 a0 ff"), a list of numbers, or text= for text')
    return bytes.fromhex(s)


def show(data: bytes) -> dict:
    return {"bytes": len(data), "hex": " ".join(f"{b:02x}" for b in data), "text": "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else "." for b in data)}


def _vp(dev) -> str:
    return f"{dev.idVendor:04x}:{dev.idProduct:04x}"


def _str(dev, attr: str) -> str:
    try:
        return getattr(dev, attr) or ""
    except Exception:   # no permission to read string descriptors, or none
        return ""


class Usb:
    def __init__(self, jade: "Jade"):
        self.jade = jade
        self.opened: dict[str, Any] = {}     # id -> device
        self.claimed: dict[str, set[int]] = {}

    # ---- finding devices (tests replace find_devices)
    def find_devices(self) -> list:
        try:
            import usb.core
        except ImportError:
            raise ToolError("USB needs PyUSB and libusb: pip install pyusb (and libusb-1.0 from your system's packages)") from None
        try:
            return list(usb.core.find(find_all=True))
        except Exception as e:   # usb.core.NoBackendError and friends
            raise ToolError(f"USB is not available here: {e} (install libusb-1.0)") from None

    @staticmethod
    def dev_id(dev) -> str:
        return f"{getattr(dev, 'bus', 0)}:{getattr(dev, 'address', 0)}"

    def allowed(self, dev) -> bool:
        allow = [x.lower() for x in self.jade.settings.usb_allow]
        if not allow:
            return True
        vp = _vp(dev)
        return vp in allow or vp.split(":")[0] in allow

    def describe(self, dev) -> dict:
        ifs = []
        try:
            cfgs = list(dev)
        except Exception:
            cfgs = []
        for cfg in cfgs[:1]:
            for intf in cfg:
                ifs.append({"number": intf.bInterfaceNumber, "alternate": intf.bAlternateSetting, "class": intf.bInterfaceClass,
                            "endpoints": [f"{e.bEndpointAddress & 0x0f} {'in' if e.bEndpointAddress & 0x80 else 'out'} {EP_TYPE.get(e.bmAttributes & 3, '?')} {e.wMaxPacketSize}" for e in intf]})
        did = self.dev_id(dev)
        return {"id": did, "vendorProduct": _vp(dev), "manufacturer": _str(dev, "manufacturer"), "product": _str(dev, "product"),
                "serial": _str(dev, "serial_number"), "class": dev.bDeviceClass, "allowed": self.allowed(dev), "opened": did in self.opened,
                "claimed": sorted(self.claimed.get(did, ())), "interfaces": ifs}

    def pick(self, sel: str = "", must_allow: bool = True):
        devs = self.find_devices()
        if must_allow:
            devs = [d for d in devs if self.allowed(d)]
        s = str(sel or "").strip().lower()
        if not s:
            live = [d for d in devs if self.dev_id(d) in self.opened]
            if len(live) == 1:
                return live[0]
            if len(devs) == 1:
                return devs[0]
            raise ToolError("say which device: " + (", ".join(f"{self.dev_id(d)} {_vp(d)} {_str(d, 'product')}" for d in devs) or "(no allowed USB device is plugged in)"))
        for d in devs:
            if s in (self.dev_id(d), _vp(d)):
                return d
        if s.isdigit() and int(s) < len(devs):
            return devs[int(s)]
        for d in devs:
            if s in _str(d, "product").lower() or s in _str(d, "manufacturer").lower():
                return d
        raise ToolError(f"no allowed USB device {sel} (unplugged? see list; settings.usb_allow decides which may be opened)")

    def live(self, a: dict):
        d = self.pick(a.get("device") or "")
        if self.dev_id(d) not in self.opened:
            raise ToolError("the device is not open: open the device first")
        return self.opened[self.dev_id(d)]

    @staticmethod
    def _claim(dev, n: int) -> None:
        try:
            if dev.is_kernel_driver_active(n):
                dev.detach_kernel_driver(n)
        except Exception:
            pass
        if hasattr(dev, "_ctx"):
            import usb.util
            usb.util.claim_interface(dev, n)
        else:
            dev.claim_interface(n)

    @staticmethod
    def _release(dev, n: int) -> None:
        if hasattr(dev, "_ctx"):
            import usb.util
            usb.util.release_interface(dev, n)
        else:
            dev.release_interface(n)

    def _ep_ok(self, dev, ep: int, inward: bool) -> int:
        addr = (ep & 0x0f) | (0x80 if inward else 0)
        did = self.dev_id(dev)
        try:
            cfg = dev.get_active_configuration()
            for intf in cfg:
                if intf.bInterfaceNumber in self.claimed.get(did, set()) and any(e.bEndpointAddress == addr for e in intf):
                    return addr
        except Exception:
            return addr
        raise ToolError(f"no {'in' if inward else 'out'} endpoint {ep & 0x0f} on a claimed interface (claim the interface that has it)")

    async def tool(self, ctx, action: str = "list", **a) -> str:
        timeout = int(a.get("timeout") or 5000)
        if action == "list":
            return json.dumps([self.describe(d) for d in self.find_devices()])
        if action == "request":
            vid, pid = a.get("vendorId"), a.get("productId")
            devs = [d for d in self.find_devices() if (not vid or d.idVendor == int(str(vid), 16)) and (not pid or d.idProduct == int(str(pid), 16))
                    and (a.get("classCode") is None or d.bDeviceClass == int(a["classCode"]))]
            if not devs:
                raise ToolError("no USB device matches (is it plugged in?)")
            ok = [d for d in devs if self.allowed(d)]
            if not ok:
                raise ToolError(f"{_vp(devs[0])} is not in settings.usb_allow; ask the user to add it")
            return "allowed: " + ", ".join(f"{self.dev_id(d)} {_vp(d)} {_str(d, 'product')}" for d in ok)
        if action == "open":
            d = self.pick(a.get("device") or "")
            try:
                try:
                    d.get_active_configuration()
                except Exception:
                    d.set_configuration()
            except Exception as e:
                raise ToolError(f"could not open {_vp(d)}: {e} (on Linux this usually needs a udev rule; see the usb tool notes)") from None
            self.opened[self.dev_id(d)] = d
            self.claimed.setdefault(self.dev_id(d), set())
            return f"opened {_str(d, 'product') or _vp(d)} ({self.dev_id(d)})"
        if action == "close":
            d = self.pick(a.get("device") or "")
            did = self.dev_id(d)
            for n in list(self.claimed.get(did, ())):
                try:
                    self._release(d, n)
                except Exception:
                    pass
            self.opened.pop(did, None)
            self.claimed.pop(did, None)
            try:
                import usb.util
                usb.util.dispose_resources(d)
            except Exception:
                pass
            return f"closed {_str(d, 'product') or _vp(d)}"
        d = self.live(a)
        did = self.dev_id(d)
        if action in ("claim", "release", "select_alt"):
            n = int(a.get("interface", 0))
            if action == "claim":
                self._claim(d, n)
                self.claimed[did].add(n)
                return f"claimed interface {n}"
            if action == "release":
                self._release(d, n)
                self.claimed[did].discard(n)
                return f"released interface {n}"
            d.set_interface_altsetting(interface=n, alternate_setting=int(a.get("alternate", 0)))
            return f"interface {n} uses alternate setting {int(a.get('alternate', 0))}"
        if action in ("control_in", "control_out"):
            inward = action == "control_in"
            bm = (0x80 if inward else 0) | (REQ_TYPE.get(a.get("requestType") or "vendor", 2) << 5) | RECIPIENT.get(a.get("recipient") or "device", 0)
            try:
                if inward:
                    data = bytes(d.ctrl_transfer(bm, int(a.get("request", 0)), int(a.get("value", 0)), int(a.get("index", 0)), int(a.get("length") or 64), timeout))
                    return json.dumps({"status": "ok", **show(data)})
                n = d.ctrl_transfer(bm, int(a.get("request", 0)), int(a.get("value", 0)), int(a.get("index", 0)), usb_bytes(a), timeout)
                return json.dumps({"status": "ok", "bytesWritten": int(n)})
            except ToolError:
                raise
            except Exception as e:
                return json.dumps({"status": "stall", "error": f"the control transfer failed or timed out: {e}"})
        if action == "transfer_in":
            ep = self._ep_ok(d, int(a.get("endpoint", 1)), True)
            try:
                data = bytes(d.read(ep, max(1, int(a.get("length") or 64)), timeout))
            except Exception as e:
                if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                    return json.dumps({"status": "timeout", "bytes": 0})
                raise ToolError(f"transfer_in failed: {e}") from None
            out = {"status": "ok", **show(data)}
            if a.get("save"):
                out["saved"] = ctx.jade.vault.write(ctx.jade.vault.norm(str(a["save"])), data)
            return json.dumps(out)
        if action == "transfer_out":
            ep = self._ep_ok(d, int(a.get("endpoint", 1)), False)
            try:
                n = d.write(ep, usb_bytes(a), timeout)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(f"transfer_out failed: {e}") from None
            return json.dumps({"status": "ok", "bytesWritten": int(n)})
        raise ToolError(f"unknown usb action {action}")


SPEC = {
    "description": "USB devices plugged into this machine: Arduinos and other microcontrollers, serial adapters, sensors, custom devices. list: what is connected (ids, names, vendor:product, "
                   "interfaces and endpoints, whether you may open it). request: check that a device (vendorId/productId in hex, or classCode) is plugged in and allowed. open/close; "
                   "claim/release an interface (and select_alt). control_in/control_out: control transfers (requestType standard|class|vendor, recipient device|interface|endpoint|other, "
                   "request, value, index; length for in, data for out). transfer_in/transfer_out: bulk or interrupt transfers on an endpoint number (length for in; data for out). "
                   "data is hex bytes (\"01 a0 ff\"), a list of numbers, or text=; answers show hex and printable text, and save writes received bytes to a vault file. "
                   "device: its id (bus:address), vendor:product, index or part of its name (optional when only one is open or plugged in).",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "request", "open", "close", "claim", "release", "select_alt", "control_in", "control_out", "transfer_in", "transfer_out"]},
        "device": {"type": "string"}, "vendorId": {"type": "string", "description": "hex, e.g. 2341"}, "productId": {"type": "string"}, "classCode": {"type": "number"},
        "interface": {"type": "number"}, "alternate": {"type": "number"}, "endpoint": {"type": "number"},
        "requestType": {"type": "string", "enum": ["standard", "class", "vendor"]}, "recipient": {"type": "string", "enum": ["device", "interface", "endpoint", "other"]},
        "request": {"type": "number"}, "value": {"type": "number"}, "index": {"type": "number"}, "length": {"type": "number"},
        "data": {"description": "hex string or list of byte values"}, "text": {"type": "string"}, "timeout": {"type": "number", "description": "ms (default 5000)"},
        "save": {"type": "string", "description": "vault path for received bytes"}}, "required": ["action"]}}


def setup(jade: "Jade") -> None:
    jade.usb = Usb(jade)
    jade.tools.add(Tool("usb", SPEC["description"], SPEC["parameters"], jade.usb.tool, risky=True, core=False, enabled=lambda j: j.settings.usb_on,
                        hints="usb device arduino microcontroller serial otg plugged vendor endpoint bulk control transfer firmware",
                        summary="usb: USB devices plugged into this machine (list, open, control and bulk transfers)"))
