import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jade import cron as C
from jade.scheduler import Daemon, heartbeat_body, in_active_hours, is_ack, run_heartbeat

NOW = datetime(2026, 10, 23, 10, 15, tzinfo=ZoneInfo("America/New_York"))   # a Friday


@pytest.mark.parametrize("text,want", [
    ("every day at 9am", "0 9 * * *"), ("weekdays at 8:30", "30 8 * * 1-5"), ("every monday at 6pm", "0 18 * * 1"),
    ("every 30 minutes", "*/30 * * * *"), ("every 2 hours", "0 */2 * * *"), ("hourly", "0 * * * *"), ("0 9 * * *", "0 9 * * *"),
    ("every weekend at noon", "0 12 * * 0,6"), ("monthly on the 15th at 8am", "0 8 15 * *"), ("every 3 days at 7pm", "0 19 */3 * *"), ("@daily", "0 0 * * *"),
    ("every tuesday and thursday at 7:45 pm", "45 19 * * 2,4"),
])
def test_cron_from_text(text, want):
    assert C.from_text(text, NOW) == {"cron": want}


def test_cron_once_and_next():
    assert C.from_text("in 20 minutes", NOW)["once"] == NOW + timedelta(minutes=20)
    assert C.from_text("tomorrow at 7pm", NOW)["once"] == datetime(2026, 10, 24, 19, 0, tzinfo=NOW.tzinfo)
    assert C.from_text("monday at 9", NOW)["once"].weekday() == 0
    assert C.next_after("0 9 * * 1-5", NOW) == datetime(2026, 10, 26, 9, 0, tzinfo=NOW.tzinfo)   # Friday 10:15 -> Monday 9:00
    assert C.next_after("*/15 * * * *", NOW).minute == 30
    assert C.describe("30 8 * * 1-5") == "weekdays at 8:30 am" and C.describe("0 18 * * 1") == "every Monday at 6 pm"
    with pytest.raises(C.CronError):
        C.from_text("whenever you like", NOW)
    with pytest.raises(C.CronError):
        C.parse("61 * * * *")


async def test_schedule_tool_and_runs(jade, fake):
    r = await jade.run_tool("schedule", {"action": "add", "when": "every day at 9am", "prompt": "Summarise my inbox"})
    assert "Created schedule" in r and "daily at 9 am" in r
    r = await jade.run_tool("schedule", {"action": "add", "when": "in 5 minutes", "command": "echo cron-ran > cron-out.txt", "name": "touch"})
    sid = jade.scheduler.list[-1]["id"]
    assert "one-time" in r
    # make both due now
    for s in jade.scheduler.list:
        s["next_run"] = time.time() - 1
    fake.script = [{"content": "Inbox has 3 items."}]
    due = jade.scheduler.due()
    assert len(due) == 2
    await asyncio.gather(*(jade.scheduler.run(s) for s in due))
    assert jade.vault.read("/cron-out.txt").strip() == "cron-ran"
    assert "echo cron-ran" in jade.vault.read("/Jade/Logs/cron.log")
    prompt_s = jade.scheduler.list[0]
    assert prompt_s["last_status"] == "ok" and prompt_s["next_run"] > time.time()
    assert jade.scheduler.find(sid)["enabled"] is False   # one-time: done
    chats = jade.chats()
    assert any(c["title"].startswith("⏰ Summarise my inbox") for c in chats)
    assert "Summarise my inbox" in jade.vault.read("/Jade/Logs/notifications.log") or "done" in jade.vault.read("/Jade/Logs/notifications.log")
    r = await jade.run_tool("schedule", {"action": "list"})
    assert "Summarise my inbox" in r
    assert "Deleted" in await jade.run_tool("schedule", {"action": "remove", "id": sid})


async def test_heartbeat(jade, fake):
    assert heartbeat_body(jade) == ""   # the seed file has only comments and an empty box
    assert "skipped" in await run_heartbeat(jade)
    jade.vault.write("/Jade/HEARTBEAT.md", "# HEARTBEAT\n## Check\n- If /Inbox.md has more than 2 items, remind me.\n")
    fake.script = [{"content": "", "tool_calls": [("read", {"path": "/Inbox.md"})]}, {"content": "HEARTBEAT_OK"}]
    assert (await run_heartbeat(jade)).startswith("OK (1 check)")
    s = jade.session(jade.state["hb_session"])
    assert not s.messages   # the OK run is pruned
    seen = []
    jade.notifiers.append(lambda t, b: seen.append((t, b)))
    fake.script = [{"content": "Your inbox has 3 items: time to sort it."}]
    assert (await run_heartbeat(jade)).startswith("alert")
    assert seen and "inbox" in seen[0][1].lower()
    assert is_ack("**HEARTBEAT_OK**") and not is_ack("Something needs you")
    assert in_active_hours("22:00", "07:00", NOW.replace(hour=23)) and not in_active_hours("22:00", "07:00", NOW)


async def test_daemon_runs_due_schedules(jade, fake):
    jade.scheduler.add(when="every minute", command="echo tick >> ticks.txt", name="ticker")
    jade.scheduler.list[-1]["next_run"] = time.time() - 1
    d = Daemon(jade)
    task = asyncio.create_task(d.run())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if jade.vault.exists("/ticks.txt"):
            break
    d.stop()
    await asyncio.wait_for(task, 5)
    assert jade.vault.read("/ticks.txt").strip() == "tick"
