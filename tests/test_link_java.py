"""The Jade Link app's real link code (Java, run on a plain JVM) against a real jade serve: dial-in and listening at once."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jade.api import create_app
from test_phone import free_port, serve, wait_for

ROOT = Path(__file__).resolve().parent.parent / "android"
pytestmark = pytest.mark.skipif(not (shutil.which("javac") and shutil.which("java")), reason="needs a JDK")


@pytest.fixture(scope="module")
def classes(tmp_path_factory):
    out = tmp_path_factory.mktemp("link-classes")
    src = ROOT / "src/app/netrunner/jade"
    files = [src / f for f in ("Link.java", "LinkClient.java", "LinkServer.java", "MiniJson.java")] + [ROOT / "test/LinkTest.java"]
    r = subprocess.run(["javac", "-nowarn", "--release", "8", "-d", str(out)] + [str(f) for f in files], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


class JavaPhone:
    def __init__(self, proc):
        self.proc = proc
        self.lines: list[str] = []

    async def pump(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            self.lines.append(line.decode().rstrip())

    def ops(self, op):
        return [l for l in self.lines if l.startswith(f"OP {op} ")]

    async def send(self, cmd):
        self.proc.stdin.write((cmd + "\n").encode())
        await self.proc.stdin.drain()


async def test_java_link_both_ways(jade, classes):
    jade.settings.api_token = "tok"
    jade.settings.sentry_allow = ["5550100300"]
    jade.flows.save({"name": "Fired", "steps": [{"type": "tool", "tool": "phone", "args": {"action": "sms_send", "to": "555-010-0300", "text": "from sentry"}}]})
    port, lport = free_port(), free_port()
    srv, task = await serve(create_app(jade, daemon=True), port)
    proc = await asyncio.create_subprocess_exec("java", "-cp", str(classes), "LinkTest", f"http://127.0.0.1:{port}", "tok", str(lport), "ltok",
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    jp = JavaPhone(proc)
    pt = asyncio.create_task(jp.pump())
    try:
        assert await wait_for(lambda: "READY" in jp.lines, 15)
        # dial-in: the phone shows up under the name it gave
        assert await wait_for(lambda: "jvm-phone" in jade.phones.phones and jade.phones.phones["jvm-phone"].online, 15)
        out = json.loads(await jade.run_tool("phone", {"action": "volume", "phone": "jvm-phone"}))
        assert out["media"]["level"] == 0.25
        # several at once (the phone runs them in parallel), and a failure comes back as an error
        r = await asyncio.gather(*[jade.run_tool("phone", {"action": "slow", "phone": "jvm-phone"}) for _ in range(3)])
        assert all(json.loads(x)["slow"] for x in r)
        with pytest.raises(RuntimeError, match="kaboom"):
            await jade.run_tool("phone", {"action": "boom", "phone": "jvm-phone"})
        # listening: the same phone, called directly
        jade.settings.phones = [{"name": "jvm-direct", "url": f"http://127.0.0.1:{lport}", "token": "ltok"}]
        jade.phones.sync_direct()
        assert json.loads(await jade.run_tool("phone", {"action": "volume", "phone": "jvm-direct"}))["media"]["max"] == 15
        jade.settings.phones = [{"name": "jvm-direct", "url": f"http://127.0.0.1:{lport}", "token": "wrong"}]
        jade.phones.sync_direct()
        with pytest.raises(RuntimeError, match="refused the token"):
            await jade.run_tool("phone", {"action": "volume", "phone": "jvm-direct"})
        jade.settings.phones = [{"name": "jvm-direct", "url": f"http://127.0.0.1:{lport}", "token": "ltok"}]
        jade.phones.sync_direct()
        assert await wait_for(lambda: jade.phones.phones["jvm-direct"].online, 30)
        # a Sentry fire reaches jade-py both ways, runs its flow once, texts the allowed number without asking, and is acknowledged
        await jp.send("fire Fired")
        assert await wait_for(lambda: any('"qid":"jvm@1"' in l for l in jp.ops("sentry.ack")), 30), jp.lines[-20:]
        await asyncio.sleep(1.5)
        assert len(jp.ops("sentry.ack")) == 1
        sms = [l for l in jp.ops("phone") if "sms_send" in l]
        assert len(sms) == 1 and "from sentry" in sms[0] and not jp.ops("ask")
        await jp.send("status")
        assert await wait_for(lambda: any(l.startswith("STATUS ") for l in jp.lines), 5)
        st = json.loads(next(l for l in jp.lines if l.startswith("STATUS "))[7:])
        assert st["dial_out"]["connected"] and st["dial_out"]["name"] == "jvm-phone" and st["listen"]["listening"]
    finally:
        if proc.returncode is None:
            await jp.send("quit")
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
        pt.cancel()
        srv.should_exit = True
        await task
