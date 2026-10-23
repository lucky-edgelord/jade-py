"""The web dashboard in a real (headless) Chromium against a real jade serve: sign-in, chat streaming, the vault editor,
settings, approvals, Sentry and phones pages, the phone layout with its drawer, and talk mode with a fake phone voice
(window.NetrunnerAndroid, as Jade Link provides)."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from jade.api import create_app
from test_phone import FakePhone, dial_in, free_port, serve, wait_for

pw = pytest.importorskip("playwright.async_api")
CHROME = os.environ.get("JADE_TEST_CHROME", "/opt/pw-browsers/chromium")
SHOTS = Path(os.environ.get("JADE_SHOTS", "")) if os.environ.get("JADE_SHOTS") else None
pytestmark = pytest.mark.skipif(not Path(CHROME).exists() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), reason="needs Chromium")

# a fake Jade Link voice: speech recognition answers from a script, speaking finishes at once
FAKE_PHONE_VOICE = """
window.__heard = []; window.__said = []; window.__sttScript = [];
window.NetrunnerAndroid = {
  speak(uid, text) { window.__said.push(text); setTimeout(() => { window.__jadeTTS && window.__jadeTTS('start', uid); setTimeout(() => window.__jadeTTS && window.__jadeTTS('done', uid), 30); }, 10); return true; },
  stop() {}, ttsAvailable() { return true; }, sttAvailable() { return true; }, micAllowed() { return true; },
  sttStart() { const next = window.__sttScript.shift(); setTimeout(() => { if (!window.__jadeSTT) return; window.__jadeSTT('ready', ''); if (next == null) { window.__jadeSTT('error', '7'); return; } window.__jadeSTT('partial', next.slice(0, 3)); window.__jadeSTT('final', next); window.__heard.push(next); }, 60); },
  sttStop() {}, keepScreenOn() {}, kioskStatus() { return JSON.stringify({ on: false }); }, setKiosk() {}, setup() {},
};
"""


async def shot(page, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


@pytest.fixture
async def site(jade):
    jade.settings.api_token = "tok"
    port = free_port()
    srv, task = await serve(create_app(jade, daemon=True), port)
    async with pw.async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"])
        yield jade, f"http://127.0.0.1:{port}", b
        await b.close()
    srv.should_exit = True
    await task


async def open_page(b, base, *, phone=False, voice=False, hash_="#token=tok"):
    ctx = await b.new_context(viewport={"width": 412, "height": 860} if phone else {"width": 1280, "height": 860}, device_scale_factor=1)
    if voice:
        await ctx.add_init_script(FAKE_PHONE_VOICE)
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text and "status of 401" not in m.text else None)
    await page.goto(base + "/ui/" + hash_)
    return page, errors


async def test_sign_in_home_chat_vault(site, fake):
    jade, base, b = site
    page, errors = await open_page(b, base, hash_="")
    await page.wait_for_selector("#tokIn")
    await page.fill("#tokIn", "wrong")
    await page.click("#tokGo")
    await page.wait_for_function("document.querySelector('#tokMsg').textContent.length > 0")
    await page.fill("#tokIn", "tok")
    await page.click("#tokGo")
    await page.wait_for_selector("text=Talk to her")
    await shot(page, "home")
    # chat: streamed, a tool card, then the answer
    jade.vault.write("/Notes/Plan.md", "# Plan\n- ship jade-py\n")
    fake.script = [{"tool_calls": [("read", {"path": "/Notes/Plan.md"})]}, {"content": "Your plan: **ship jade-py**."}]
    await page.click("[data-r=chat]")
    await page.wait_for_selector("#input")
    await page.fill("#input", "what is my plan?")
    await page.click("#sendBtn")
    await page.wait_for_selector(".msg-ai strong:text('ship jade-py')", timeout=15000)
    assert await page.locator(".tool .tname:text('read')").count() >= 1
    assert "/chat/" in page.url
    await shot(page, "chat")
    # the vault: open, edit, save, and it is on disk
    await page.goto(base + "/ui/#/vault?open=/Notes/Plan.md")
    await page.wait_for_selector("#vText")
    await page.fill("#vText", "# Plan\n- ship jade-py\n- celebrate\n")
    await page.click("#vSave")
    await page.wait_for_function("document.querySelector('#vSave').disabled")
    assert "celebrate" in jade.vault.read("/Notes/Plan.md")
    await page.click("#vPrev")
    await page.wait_for_selector("#vPreview li:text('celebrate')")
    await shot(page, "vault")
    assert not errors, errors


async def test_settings_approvals_logs(site):
    jade, base, b = site
    page, errors = await open_page(b, base)
    await page.goto(base + "/ui/#/settings")
    await page.wait_for_selector("#set_max_steps", state="attached")
    await page.click("summary:text('The agent')")
    await page.fill("#set_max_steps", "17")
    await page.fill("#set_api_key", "")   # masked fields left alone are not changed
    await page.click("#saveSet")
    await page.wait_for_function("[...document.querySelectorAll('.toast div')].some(d => d.textContent.includes('max_steps'))")
    assert jade.settings.max_steps == 17
    import tomllib
    saved = tomllib.loads(Path(jade.config_path).read_text())
    assert saved == {"max_steps": 17}, saved   # only what changed is written; the user's own config keeps the rest
    assert str(jade.config_path).startswith(str(Path(os.environ["JADE_CONFIG"]).parent))
    await shot(page, "settings")
    # an approval appears in the page; Allow answers it
    from jade.providers import ToolCall
    await page.goto(base + "/ui/#/")
    await page.wait_for_selector("text=Talk to her")
    await asyncio.sleep(0.5)
    t = asyncio.create_task(jade.ask_approval(ToolCall("bash", {"command": "rm -rf /tmp/x"}), jade.tools.get("bash")))
    await page.wait_for_selector("#apYes", timeout=10000)
    assert "rm -rf /tmp/x" in await page.inner_text(".modal pre")
    await shot(page, "approval")
    await page.click("#apYes")
    assert await asyncio.wait_for(t, 10) is True
    jade.notify("Hello", "from a test")
    await page.wait_for_selector(".toast div:has-text('from a test')")
    await page.goto(base + "/ui/#/logs/notifications")
    await page.wait_for_selector("#logBox:has-text('from a test')")
    assert not errors, errors


async def test_phones_sentry_and_phone_layout(site):
    jade, base, b = site
    phones = [FakePhone(), FakePhone()]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(dial_in(base, "tok", phones[0], stop, asyncio.Queue(), did="d1", name="Pixel")),
             asyncio.create_task(dial_in(base, "tok", phones[1], stop, asyncio.Queue(), did="d2", name="Tablet"))]
    try:
        assert await wait_for(lambda: {"pixel", "tablet"} <= set(jade.phones.phones), 10)
        page, errors = await open_page(b, base, phone=True)
        await page.goto(base + "/ui/#/phones")
        await page.wait_for_selector("text=All at once")
        await page.click("[data-p=all][data-q='3']")   # battery on both at once
        await page.wait_for_selector("[data-out=all] pre:has-text('### tablet')", timeout=15000)
        await shot(page, "phones-phone")
        # Sentry: run it on the tablet only, switch a system on just there
        jade.vault.write("/Jade/Config/Sentry.json", json.dumps({"enabled": True, "triggers": [{"id": "texts", "on": {"event": "sms"}, "alert": {"notify": "text!"}}]}))
        await page.goto(base + "/ui/#/sentry")
        await page.wait_for_selector("input[data-dev=tablet]")
        await page.check("input[data-dev=tablet]")
        await page.wait_for_function("document.querySelector('input[data-dev=tablet]') && document.querySelector('input[data-dev=tablet]').checked && document.querySelectorAll('select[data-sysdev]').length > 0")
        await page.select_option("select[data-sysdev=camera][data-dev=tablet]", "on")
        await asyncio.sleep(0.6)
        cfg = json.loads(jade.vault.read("/Jade/Config/Sentry.json"))
        assert cfg["phones"] == ["tablet"] and cfg["devices"]["tablet"]["systems"]["camera"] is True
        assert await wait_for(lambda: (phones[1].rules or {}).get("systems", {}).get("camera") is True and phones[0].rules and phones[0].rules["enabled"] is False, 10)
        await shot(page, "sentry-phone")
        # the drawer (tray): chats and status, and the pages the tab bar leaves out
        await page.click("#drawerBtn")
        await page.wait_for_selector("#drawer .drawer-nav a:text('Settings')", state="visible")
        await asyncio.sleep(0.4)
        await page.wait_for_function("document.querySelector('#drStatus').textContent.includes('phones online')")
        await shot(page, "drawer")
        assert await page.is_hidden("#rail a[data-r=settings]")
        await page.click("#drawer .drawer-nav a:text('Settings')")
        await page.wait_for_selector("#set_max_steps", state="attached")
        assert not errors, errors
    finally:
        stop.set()
        for t in tasks:
            await t


async def test_talk_mode_with_the_phone_voice(site, fake):
    jade, base, b = site
    page, errors = await open_page(b, base, phone=True, voice=True, hash_="#token=tok&app=1")
    await page.wait_for_selector("text=Talk to her")
    await page.evaluate("S.wakeWords = 'hey jade'; saveSettings()")
    # she hears chatter (ignored: no wake phrase), then "hey jade what time is it", answers aloud, listens again
    await page.evaluate("window.__sttScript.push('just chatting here', 'hey jade what time is it')")
    fake.script = [{"content": "It is time to ship."}]
    await page.click("#homeTalk")
    await page.wait_for_selector("body.hf-solo #jadeStage")
    await page.wait_for_function("window.__said.some(t => t.includes('time to ship'))", timeout=20000)
    asked = [m for m in fake.requests[-1]["messages"] if m["role"] == "user"][-1]["content"]
    assert asked.endswith("what time is it") and "hey jade" not in asked and "chatting" not in asked
    await page.wait_for_function("document.querySelector('#jadeBubble') && document.querySelector('#jadeBubble').textContent.includes('ship')")
    await shot(page, "talk-mode")
    # it keeps listening after she speaks
    n = await page.evaluate("window.__heard.length")
    await page.evaluate("window.__sttScript.push('hey jade thanks')")
    fake.script = [{"content": "Any time."}]
    await page.wait_for_function("window.__said.some(t => t.includes('Any time'))", timeout=20000)
    assert await page.evaluate("window.__heard.length") > n
    # Back (from Jade Link) leaves talk mode, like the app
    assert await page.evaluate("window.onAndroidBack()") is True
    await page.wait_for_function("!document.body.classList.contains('hf-solo')")
    assert await page.evaluate("HF.on") is False
    # the avatar loaded (WebGL in headless Chromium), or said why not
    status = await page.evaluate("(document.querySelector('#jadeStatus')||{}).textContent || ''")
    ready = await page.evaluate("!!(window.Jade && window.Jade.ready)")
    assert ready or "could not load" in status, status
    assert not [e for e in errors if "WebGL" not in e], errors
