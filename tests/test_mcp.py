import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_demo_server.py")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")


def config(jade, servers):
    jade.vault.write("/Jade/mcp.json", json.dumps({"mcpServers": servers}))


async def test_stdio_exposures_and_resources(jade, fake):
    config(jade, {"demo": {"command": sys.executable, "args": [SERVER], "exposure": "deferred", "toolExposure": {"add": "direct", "fail": "direct", "sh*": "hidden"}}})
    await jade.mcp.ensure()
    s = jade.mcp.servers["demo"]
    assert s.state == "connected", s.error
    names = {t.name for t in jade.tools.active(jade)}
    assert "mcp__demo__add" in names and "mcp__demo__shout" not in names and "read_mcp_resource" in names and "codemode" not in names
    assert jade.tools.get("mcp__demo__add").core
    assert await jade.run_tool("mcp__demo__add", {"a": 2, "b": 3}) == "5"
    with pytest.raises(RuntimeError, match="boom"):
        await jade.run_tool("mcp__demo__fail", {"msg": "boom"})
    assert await jade.run_tool("read_mcp_resource", {"server": "demo", "uri": "note://hello"}) == "hi there"
    assert "note://hello" in await jade.run_tool("list_mcp_resources", {})
    # the model sees the direct tool and the server in the prompt, and can call it in a turn
    fake.script = [{"tool_calls": [("mcp__demo__add", {"a": 20, "b": 22})]}, {"content": "42 it is"}]
    jade.settings.approve = "auto"
    s2 = jade.session()
    assert await s2.ask("add 20 and 22") == "42 it is"
    req = fake.requests[0]
    assert "mcp__demo__add" in {t["function"]["name"] for t in req["tools"]} and "Demo server for tests." in req["messages"][0]["content"]
    assert any(m.get("role") == "tool" and m.get("content") == "42" for m in s2.messages)
    # edits to mcp.json apply on the next call
    config(jade, {})
    await jade.mcp.ensure()
    assert not jade.mcp.servers and not any(n.startswith("mcp__") for n in jade.tools.names())
    await jade.mcp.aclose()


@needs_node
async def test_codemode(jade):
    config(jade, {"demo": {"command": sys.executable, "args": [SERVER]}})
    await jade.mcp.ensure()
    assert "codemode" in {t.name for t in jade.tools.active(jade)} and "mcp__demo__add" not in {t.name for t in jade.tools.active(jade)}
    assert "mcp__demo (3 tools)" in jade.tools.get("codemode").describe(jade)
    jade.vault.write("/Inbox.md", "milk")
    out = await jade.run_tool("codemode", {"code": """
const [a, b] = await Promise.all([tools.mcp__demo__add({a: 1, b: 2}), tools.mcp__demo__shout({text: 'hey'})]);
console.log('sum', a.structuredContent.result);
text(b.content[0].text);
const inbox = await tools.read({path: '/Inbox.md'});
await store('k', {n: 7});
return { found: searchTools('add numbers')[0].name, schema: Object.keys(describeTool('mcp__demo__add').inputSchema.properties), inbox: inbox.includes('milk') };"""})
    assert "sum 3" in out and "HEY" in out and '"found": "mcp__demo__add"' in out and '"inbox": true' in out
    assert "7" in await jade.run_tool("codemode", {"code": "return (await load('k')).n"})
    with pytest.raises(RuntimeError, match="no tool named"):
        await jade.run_tool("codemode", {"code": "await tools.nope({})"})
    await jade.mcp.aclose()


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_http_with_secret_header(jade):
    port, token = _port(), "tok-" + "x" * 20
    p = subprocess.Popen([sys.executable, SERVER, "--http", str(port), "--token", token])
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        jade.settings.secrets["DEMO_TOKEN"] = token
        config(jade, {"web": {"url": f"http://127.0.0.1:{port}/mcp", "headers": {"Authorization": "Bearer ${DEMO_TOKEN}"}, "exposure": "direct"},
                      "bad": {"url": f"http://127.0.0.1:{port}/mcp", "headers": {"Authorization": "Bearer wrong"}, "connectTimeout": 10}})
        await jade.mcp.ensure()
        assert jade.mcp.servers["web"].state == "connected", jade.mcp.servers["web"].error
        assert jade.mcp.servers["bad"].state == "error"
        assert await jade.run_tool("mcp__web__shout", {"text": "ok"}) == "OK"
        listing = await jade.run_tool("mcp", {"action": "list"})
        assert "web: connected" in listing and "bad: error" in listing
        r = await jade.run_tool("mcp", {"action": "disable", "name": "bad"})
        assert "removed or disabled" in r and "bad" not in jade.mcp.servers
        await jade.mcp.aclose()
    finally:
        p.terminate()
        p.wait(5)
