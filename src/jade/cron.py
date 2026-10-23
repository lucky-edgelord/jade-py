"""Cron expressions and plain-English times, the same rules as the Jade Vault app:
"every day at 9am", "weekdays at 8:30", "every monday at 6pm", "every 30 minutes", "in 20 minutes",
"tomorrow at 7pm", or a 5-field cron like "0 9 * * *". All in the given time zone."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

SHORT = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DOW = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
DOW_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
FIELDS = [("minute", 0, 59, None, 0), ("hour", 0, 23, None, 0), ("day of month", 1, 31, None, 0), ("month", 1, 12, MON, 1), ("day of week", 0, 7, DOW, 0)]
HELP = ('Use plain English like "every day at 9am", "weekdays at 8:30", "every monday at 6pm", "every 30 minutes", "in 20 minutes", '
        '"tomorrow at 7pm", or a 5-field cron like "0 9 * * *".')
DAYMAP = {"sun": 0, "sunday": 0, "sundays": 0, "mon": 1, "monday": 1, "mondays": 1, "tue": 2, "tues": 2, "tuesday": 2, "tuesdays": 2, "wed": 3, "weds": 3,
          "wednesday": 3, "wednesdays": 3, "thu": 4, "thur": 4, "thurs": 4, "thursday": 4, "thursdays": 4, "fri": 5, "friday": 5, "fridays": 5,
          "sat": 6, "saturday": 6, "saturdays": 6}


class CronError(ValueError):
    pass


@dataclass
class Cron:
    expr: str
    minute: set
    hour: set
    dom: set
    mon: set
    dow: set
    dom_star: bool
    dow_star: bool


def _field(src: str, f) -> set:
    name, lo_, hi_, names, base = f
    out = set()
    for part in src.lower().split(","):
        m = re.match(r"^(\*|[a-z0-9]+(?:-[a-z0-9]+)?)(?:/(\d+))?$", part)
        if not m:
            raise CronError(f'bad {name} "{part}"')

        def val(x):
            if names and x in names:
                return names.index(x) + base
            if not x.isdigit():
                raise CronError(f'bad {name} "{x}"')
            n = int(x)
            if n < lo_ or n > hi_:
                raise CronError(f"{name} {n} is out of range {lo_}-{hi_}")
            return n
        if m.group(1) == "*":
            lo, hi = lo_, (6 if name == "day of week" else hi_)
        elif "-" in m.group(1):
            lo, hi = (val(x) for x in m.group(1).split("-"))
            if lo > hi:
                raise CronError(f"{name} range {m.group(1)} runs backwards")
        else:
            lo = val(m.group(1))
            hi = hi_ if m.group(2) else lo
        step = int(m.group(2)) if m.group(2) else 1
        if not step:
            raise CronError(f"{name} step must be at least 1")
        for v in range(lo, hi + 1, step):
            out.add(0 if name == "day of week" and v == 7 else v)
    return out


def parse(expr: str) -> Cron:
    e = re.sub(r"\s+", " ", str(expr or "").strip())
    e = SHORT.get(e.lower(), e)
    parts = e.split(" ")
    if len(parts) != 5:
        raise CronError(f"cron needs 5 fields (minute hour day month weekday), got {len(parts)}")
    f = [_field(p, FIELDS[i]) for i, p in enumerate(parts)]
    return Cron(e, *f, parts[2] == "*", parts[4] == "*")


def _day_ok(c: Cron, d: datetime) -> bool:
    a, b = d.day in c.dom, ((d.weekday() + 1) % 7) in c.dow
    if c.dom_star and c.dow_star:
        return True
    if c.dom_star:
        return b
    if c.dow_star:
        return a
    return a or b


def next_after(expr: str | Cron, after: datetime) -> datetime | None:
    c = parse(expr) if isinstance(expr, str) else expr
    d = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(200000):
        if d.month not in c.mon:
            y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
            d = d.replace(year=y, month=m, day=1, hour=0, minute=0)
            continue
        if not _day_ok(c, d):
            d = (d + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if d.hour not in c.hour:
            d = (d + timedelta(hours=1)).replace(minute=0)
            continue
        if d.minute not in c.minute:
            d = d + timedelta(minutes=1)
            continue
        return d
    return None


def upcoming(expr: str, n: int, after: datetime) -> list[datetime]:
    out, t = [], after
    for _ in range(n):
        t = next_after(expr, t)
        if not t:
            break
        out.append(t)
    return out


def _hm(h: int, m: int) -> str:
    return datetime(2000, 1, 1, h, m).strftime("%-I:%M %p").lower().replace(":00 ", " ") if m == 0 else datetime(2000, 1, 1, h, m).strftime("%-I:%M %p").lower()


def describe(expr: str) -> str:
    try:
        c = parse(expr)
    except CronError:
        return expr
    mi, ho, dm, mo, dw = c.expr.split(" ")
    one = lambda s: s.isdigit()  # noqa: E731
    if (m := re.match(r"^\*/(\d+)$", mi)) and ho == dm == mo == dw == "*":
        return f"every {m.group(1)} minutes"
    if mi == ho == dm == mo == dw == "*":
        return "every minute"
    if one(mi) and ho == dm == mo == dw == "*":
        return "every hour" if mi == "0" else f"every hour at :{int(mi):02d}"
    if one(mi) and (m := re.match(r"^\*/(\d+)$", ho)) and dm == mo == dw == "*":
        return f"every {m.group(1)} hours at :{int(mi):02d}"
    if one(mi) and one(ho) and mo == "*":
        t = _hm(int(ho), int(mi))
        if dm == "*" and dw == "*":
            return f"daily at {t}"
        if dm == "*" and dw in ("1-5", "mon-fri"):
            return f"weekdays at {t}"
        if dm == "*" and dw in ("0,6", "6,0", "sat,sun"):
            return f"weekends at {t}"
        if dm == "*" and len(c.dow) == 1:
            return f"every {DOW_LONG[next(iter(c.dow))]} at {t}"
        if dm == "*" and dw != "*" and len(c.dow) > 1:
            return "every " + " & ".join(DOW_LONG[d][:3] for d in sorted(c.dow)) + f" at {t}"
        if one(dm) and dw == "*":
            return f"monthly on day {dm} at {t}"
        if (m := re.match(r"^\*/(\d+)$", dm)) and dw == "*":
            return f"every {m.group(1)} days at {t}"
    return c.expr


def _time_of(t: str):
    if re.search(r"\b(noon|midday)\b", t):
        return 12, 0
    if re.search(r"\bmidnight\b", t):
        return 0, 0
    m = re.search(r"(?:^|\s)(?:at\s+)?(\d{1,2})(?:[:.h](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?=\s|$)", t)
    if m and (m.group(2) or m.group(3) or re.search(r"\bat\s+\d", t) or re.fullmatch(r"\d{1,2}", t.strip()) or re.search(r"\b(daily|weekdays?|weekends?|day|week)\s+\d", t)):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h <= 23 and mi <= 59:
            return h, mi
    for w, hm in (("morning", (9, 0)), ("afternoon", (14, 0)), ("evening", (18, 0))):
        if re.search(rf"\b{w}\b", t):
            return hm
    if re.search(r"\b(tonight|night|nightly)\b", t):
        return 21, 0
    return None


def from_text(text: str, now: datetime) -> dict:
    """{"cron": expr} for something that repeats, {"once": datetime} for a single time."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        raise CronError("no time was given. " + HELP)
    if raw.lower() in SHORT or re.fullmatch(r"[\d*][\d*/,\-]*(\s+[\d*/,\-a-z]+){4}", raw, re.I):
        try:
            return {"cron": parse(raw).expr}
        except CronError as e:
            if len(raw.split()) == 5:
                raise CronError(f"{e}. {HELP}") from None
    t = " " + re.sub(r"\s+", " ", re.sub(r"\b(and|&|the|o'?clock)\b", " ", re.sub(r"[,;]", " ", raw.lower()))).strip() + " "
    if m := re.search(r"\bin\s+(\d+|an?|one)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?)\b", t):
        n = int(m.group(1)) if m.group(1).isdigit() else 1
        if not n:
            raise CronError("the delay must be at least 1. " + HELP)
        u = m.group(2)[0]
        return {"once": now + (timedelta(minutes=n) if u == "m" else timedelta(hours=n) if u == "h" else timedelta(days=n))}
    if re.search(r"\b(every|each)\s+minute\b", t):
        return {"cron": "* * * * *"}
    if m := re.search(r"\b(?:every|each)\s+(\d+)\s*(m|mins?|minutes?)\b", t):
        n = int(m.group(1))
        if n < 1:
            raise CronError("interval must be at least 1 minute")
        return {"cron": "0 * * * *" if n >= 60 else f"*/{n} * * * *"}
    if m := re.search(r"\b(?:every|each)\s+(\d+)\s*(h|hrs?|hours?)\b", t):
        n = int(m.group(1))
        if n < 1 or n > 23:
            raise CronError("hour interval must be 1-23")
        return {"cron": f"0 */{n} * * *"}
    if re.search(r"\b(hourly|every hour|each hour)\b", t):
        return {"cron": "0 * * * *"}
    every_n_days = re.search(r"\b(?:every|each)\s+(\d+)\s*days?\b", t)
    clean = re.sub(r"\bon\s+the\s+\d{1,2}(st|nd|rd|th)?\b|\b\d{1,2}(st|nd|rd|th)\b", " ", re.sub(r"\b(?:every|each|in)\s+\d+\s*\w+", " ", t))
    tm = _time_of(clean)
    h, mi = tm or (9, 0)
    days = {DAYMAP[w] for w in t.split() if w in DAYMAP}
    dow = "1-5" if re.search(r"\bweekdays?\b", t) else "0,6" if re.search(r"\bweekends?\b", t) else ",".join(str(d) for d in sorted(days)) if days else ""
    recurring = re.search(r"\b(every|each|daily|nightly|weekly|monthly|weekdays?|weekends?)\b", t) or re.search(r"\b(sun|mon|tues|wednes|thurs|fri|satur)days\b", t)
    if recurring:
        if every_n_days:
            n = int(every_n_days.group(1))
            if n < 1 or n > 31:
                raise CronError("day interval must be 1-31")
            return {"cron": f"{mi} {h} */{n} * *"}
        if re.search(r"\b(monthly|every month|each month)\b", t):
            d = (re.search(r"\b(\d{1,2})(st|nd|rd|th)\b", t) or re.search(r"\bon\s+(?:the\s+)?(\d{1,2})\b", t))
            d = int(d.group(1)) if d else 1
            if d < 1 or d > 31:
                raise CronError("day of month must be 1-31")
            return {"cron": f"{mi} {h} {d} * *"}
        if re.search(r"\b(weekly|every week|each week)\b", t) and not dow:
            dow = "1"
        return {"cron": f"{mi} {h} * * {dow or '*'}"}
    at = now.replace(second=0, microsecond=0)
    if re.search(r"\btomorrow\b", t):
        return {"once": (at + timedelta(days=1)).replace(hour=h, minute=mi)}
    if len(days) == 1:
        d = next(iter(days))
        cand = (at + timedelta(days=(d - (at.weekday() + 1) % 7) % 7)).replace(hour=h, minute=mi)
        if cand <= now:
            cand += timedelta(days=7)
        return {"once": cand}
    if tm or re.search(r"\b(today|tonight)\b", t):
        cand = at.replace(hour=h, minute=mi)
        if cand <= now:
            cand += timedelta(days=1)
        return {"once": cand}
    raise CronError(f'could not understand the time "{raw}". ' + HELP)
