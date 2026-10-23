import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

PAGES = {
    "/": """<html><head><title>Shop</title></head><body><h1>Lamp shop</h1>
<form action="/result"><label>Search <input name="q" placeholder="Search"></label><button>Go</button></form>
<select id="size"><option value="s">Small</option><option value="l">Large</option></select>
<label><input type="checkbox" id="gift"> Gift wrap</label>
<p class="price">$12</p><p class="price">$30</p><a href="/about">About us</a>
<p style="display:none" class="secret">hidden text</p></body></html>""",
    "/about": "<html><head><title>About</title></head><body><p>We sell lamps.</p><a href='/'>Home</a></body></html>",
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        body = PAGES.get(u.path)
        if u.path == "/result":
            q = parse_qs(u.query).get("q", [""])[0]
            body = f"<html><head><title>Results</title></head><body><p id='r'>You searched for {q}</p></body></html>"
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        b = body.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_browser_tool(jade, site):
    T = lambda **a: jade.run_tool("browser", a)   # noqa: E731
    try:
        page = await T(action="open", url=site + "/")
        assert page.startswith("[This is a web page.") and "title: Shop" in page and "Lamp shop" in page and "hidden text" not in page
        assert '"About us"' in page
        out = await T(action="type", target="Search", text="red lamp", submit=True)
        assert "title: Results" in out and "You searched for red lamp" in out
        await T(action="back")
        out = await T(action="select", target="Small", option="Large")
        assert "selected Large" in out
        assert "checked: True" in await T(action="check", target="Gift wrap")
        assert "We sell lamps" in await T(action="click", target="About us")
        assert 'We sell lamps.' in await T(action="eval", code="return document.querySelector('p').textContent")
        assert "lamps" in await T(action="read")
        st = json.loads(await T(action="state"))
        assert st["url"].endswith("/about") and st["title"] == "About"
        shot = json.loads(await T(action="screenshot", label="about"))
        assert jade.vault.read_bytes(shot["saved"])[:2] == b"\xff\xd8"
        assert "Nothing on this page matches" in await T(action="find", text="zebra")
    finally:
        await jade.aclose()


async def test_playbook_browser_steps(jade, site):
    pb = {"description": "price check", "allow_hosts": ["127.0.0.1"], "vars": {"what": "desk lamp"}, "tasks": [
        {"do": "browser.goto", "url": site + "/"},
        {"do": "browser.assert", "title_contains": "Shop"},
        {"do": "browser.extract", "css": ".price", "all": True, "register": "prices"},
        {"do": "browser.exists", "css": ".secret", "register": "secret_seen"},
        {"do": "browser.select", "css": "#size", "label": "large"},
        {"do": "browser.check", "css": "#gift"},
        {"do": "browser.type", "css": "input[name=q]", "text": "{{ what }}", "submit": True},
        {"do": "browser.assert", "css": "#r", "contains": "desk lamp"},
        {"do": "browser.url", "register": "where"},
        {"do": "browser.screenshot"},
    ]}
    try:
        r = await jade.playbooks.run({**pb, "name": "price"}, via="test")
        assert r["ok"], r
        assert r["vars"]["prices"] == ["$12", "$30"] and r["vars"]["secret_seen"] is False and "/result?q=desk+lamp" in r["vars"]["where"]
        bad = await jade.playbooks.run({"name": "elsewhere", "allow_hosts": ["example.com"], "tasks": [{"do": "browser.goto", "url": site + "/"}]}, via="test")
        assert not bad["ok"] and "allow_hosts" in bad["failed"]["error"]
    finally:
        await jade.aclose()
