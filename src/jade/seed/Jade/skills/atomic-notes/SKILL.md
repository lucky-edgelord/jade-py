---
name: atomic-notes
description: Split a long or messy note into small single-idea notes linked together (Zettelkasten style). Use when the user asks to break up, atomise, split or refactor a note.
---
# Atomic notes

1. read the note. List its distinct ideas, one line each, and show the list to the user first if
   there are more than 8.
2. For each idea, notes_search for an existing note on it. Edit that note if one exists; otherwise
   write a new note named after the idea (Title Case) with:
   - "# Title", 2 to 6 sentences or bullets in the user's own words,
   - a "Related:" line linking the sibling ideas and the original note.
3. Replace the moved text in the original note with a bulleted list of [[links]] to the new notes,
   keeping any text that did not move. Never drop content.
4. Reply with the list of notes created or changed, as [[links]].
