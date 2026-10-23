---
name: literature-note
description: Turn a web page, article or paper into a literature note with summary, key ideas, quotes and source. Use when the user shares a URL or asks to make notes on an article.
---
# Literature note

1. web_fetch the URL (web_search first if you only have a title).
2. notes_search for the title so you do not duplicate an existing note.
3. Write the note "/sources/<Short Title>.md":
   ---
   source: <url>
   author: <if known>
   read: <today>
   ---
   # <Title>
   ## Summary
   3 to 5 sentences in plain words.
   ## Key ideas
   - one bullet per idea; link related notes with [[...]]
   ## Quotes
   > at most 3 short quotes, exactly as written
   ## My thoughts
   (leave empty for the user)
   #source
4. Add "- [[<Short Title>]]" to /Inbox.md and open_note the new note.
