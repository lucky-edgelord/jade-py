---
name: autonomy
description: Work on your own with the 3-tier scheduler (sched) and long-term plans (gantt). Use when the user asks you to take something on over time, plan a project, work while they are away, or when you run a slice in autonomous mode.
---
<!-- autonomy-skill v1: written by Jade Vault; delete this line to keep your own edits -->
# Autonomy: the scheduler and plans

**The scheduler (`sched`)** has three tiers, like an operating system:
- **Tier 1, interrupts:** only for what truly cannot wait (at most 3 of yours at a time). They run first.
- **Tier 2, the priority queue:** one-off work with a priority (0–100) and maybe a due time. Waiting raises priority.
- **Tier 3, round robin:** long goals. You get a short slice (2, 4 or 8 tool steps), then the next goal gets a turn. Using the whole slice moves a goal down a level (shorter turns come first); waiting long moves it back up.

Add work with `sched action=add goal="…"` (tier 3), or `kind=prompt tier=2 priority=70 goal="…"`, `kind=flow flow=…`, `kind=playbook`, `kind=shell command=…`, `kind=lisp file=…`. `at` or `in_minutes` starts it later.

**In a slice:** you remember nothing but the note you left last time. Do a few useful steps, then end with a short note and one line:
`STATUS: done` · `STATUS: continue — <next step>` · `STATUS: blocked: <what you need from the user>`.
Keep goals concrete ("draft the reading list in /Notes/reading.md"), not open-ended ("be helpful").

**Autonomous mode is the user's switch** (the Queue screen in the app; `jade sched on` or the dashboard's Queue page here), with their budgets: steps per hour, a daily cap, quiet hours. In the app you pause while they use it; here you wait behind other background runs. Tools that send messages, delete or reach outside the vault wait for their approval: if one is refused, stop and say what you wanted. Never try to get around that. Everything is logged in /Jade/Autonomy/log.md.

**Plans (`gantt`)** are for work that takes days or weeks, in /Jade/Projects, on the Plans screen (the dashboard's Plans page, `jade plan`):
1. `create` a project; `add` tasks with start, days, deps (task ids), owner (jade or user), milestones for checkpoints.
2. `plan` pushes tasks after their dependencies; `show` draws the chart as text; `report` lists late, blocked, ready and the critical path.
3. Update `progress` as you work. The user's tasks are theirs: ask before changing one (then confirm=true).
With autonomous mode on, your ready plan tasks become tier 3 goals by themselves and are marked 100% when done.

**Start small:** one goal, a short plan, a few slices. Then tell the user what you did and what you would like to take on next.
