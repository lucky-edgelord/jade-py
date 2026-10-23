import json

import asyncssh
import pytest

from jade import ssh as SS


class Srv(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "pi" and password == "pw"


@pytest.fixture
async def server(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")

    async def shell(proc):
        # a tiny line shell: echo commands, run "add a b", exit on "exit"
        proc.stdout.write("welcome\r\n$ ")
        while True:
            try:
                line = await proc.stdin.readuntil(["\r", "\n"])   # a real pty turns Enter (\r) into \n; this one does not
            except asyncssh.IncompleteReadError:
                break
            line = line.strip()
            if line == "exit":
                break
            if proc.command:
                break
            out = str(sum(int(x) for x in line.split()[1:])) if line.startswith("add") else f"\x1b[32m{line}\x1b[0m"
            proc.stdout.write(f"{out}\r\n$ ")
        proc.exit(0)

    async def handler(proc):
        if proc.command:
            if proc.command.startswith("fail"):
                proc.stderr.write("bad\n")
                proc.exit(3)
            else:
                proc.stdout.write(f"ran {proc.command}\n")
                proc.exit(0)
            return
        await shell(proc)
    root = tmp_path / "sftp"
    root.mkdir()
    srv = await asyncssh.listen("127.0.0.1", 0, server_host_keys=[key], server_factory=Srv, process_factory=handler, line_editor=False,
                                sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=str(root).encode()))
    port = srv.sockets[0].getsockname()[1]
    yield port, key.get_fingerprint("sha256"), root
    srv.close()


def test_keys():
    assert SS.key_bytes("ctrl-c") == "\x03" and SS.key_bytes("tab") == "\t" and SS.key_bytes("up") == "\x1b[A" and SS.key_bytes("f5") == "\x1b[15~"
    with pytest.raises(Exception):
        SS.key_bytes("hyper-q")
    assert SS.clean("\x1b[32mhi\x1b[0m\r\nthere") == "hi\nthere"


async def test_ssh_flow(jade, server):
    try:
        await _flow(jade, server)
    finally:
        await jade.ssh.close_all()


async def _flow(jade, server):
    port, fp, root = server
    jade.settings.ssh_hosts = [{"name": "box", "host": "127.0.0.1", "port": port, "user": "pi", "password": "pw"}]
    T = lambda **a: jade.run_tool("ssh", a)   # noqa: E731
    hosts = json.loads(await T(action="hosts"))["hosts"]
    assert hosts == [{"name": "box", "host": "127.0.0.1", "port": port, "user": "pi", "auth": "password", "trusted": False, "confirm": True}]
    assert "pw" not in json.dumps(hosts)
    # no approver: the unknown host key is refused
    r = json.loads(await T(action="open", host="box"))
    assert "not trusted yet" in r["error"] and fp in r["error"]
    asked = []
    jade.approver = lambda call, tool: asked.append(call) or True
    out = await T(action="open", host="box")
    assert out.startswith(SS.WARN)
    o = json.loads(out.split("\n", 1)[1])
    sid = o["session"]
    assert "welcome" in o["output"] and asked[0].name == "ssh.trust" and asked[0].arguments["fingerprint"] == fp
    assert jade.state["ssh_fingerprints"]["box"] == fp
    o = json.loads((await T(action="send", session=sid, text="hello", enter=True, until=r"\$ $")).split("\n", 1)[1])
    assert "hello" in o["output"] and "\x1b" not in o["output"] and asked[-1].name == "ssh.run" and asked[-1].arguments["command"] == "hello"
    o = json.loads((await T(action="run", session=sid, command="add 2 40")).split("\n", 1)[1])
    assert "42" in o["output"]
    assert json.loads(await T(action="list"))["sessions"][0]["session"] == sid
    # one-shot run, exit codes
    o = json.loads((await T(action="run", host="box", command="uptime")).split("\n", 1)[1])
    assert o["exit"] == 0 and o["stdout"].strip() == "ran uptime"
    o = json.loads((await T(action="run", host="box", command="fail now")).split("\n", 1)[1])
    assert o["exit"] == 3 and "bad" in o["stderr"]
    # sftp
    jade.vault.write("/Notes/up.md", "payload")
    assert json.loads(await T(action="put", session=sid, path="/up.md", local="/Notes/up.md"))["bytes"] == 7
    assert (root / "up.md").read_text() == "payload"
    assert '"text": "payload"' in await T(action="get", session=sid, path="/up.md")
    await T(action="close", session=sid)
    assert not jade.ssh.sessions
    # the user declines a command
    jade.approver = lambda call, tool: call.name != "ssh.run"
    assert "did not approve" in await T(action="run", host="box", command="uptime")
    # a changed host key is refused
    jade.state["ssh_fingerprints"]["box"] = "SHA256:somethingelse"
    assert json.loads(await T(action="open", host="box"))["host_key_changed"] is True
