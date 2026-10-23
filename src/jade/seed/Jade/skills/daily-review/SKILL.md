---
name: daily-review
description: Run the user's daily review. Use when they ask for a daily review, a plan for today, or "what's on my plate".
---
# Daily review

1. Open today's daily note /daily/YYYY-MM-DD.md (today's date). If it is missing, write it with
   the sections "## Notes" and "## Tasks" and a link to [[Inbox]].
2. Collect open tasks: run bash `grep -rn -- "- \[ \]" / --include=*.md` and group them by note.
3. Read /Inbox.md. For each item, suggest where it belongs (an existing note, or a new one).
4. Append to today's note a "## Plan" section: at most 5 tasks, most important first, each with a
   [[link]] to the note it came from.
5. Reply with a short summary and ask whether to file the Inbox items as suggested.
