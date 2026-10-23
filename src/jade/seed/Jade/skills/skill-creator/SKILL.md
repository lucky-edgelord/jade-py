---
name: skill-creator
description: Create or improve one of Jade's skills. Use when the user asks for a new skill, workflow, routine or recipe that Jade should follow, or wants to change an existing skill.
---
# Skill creator

A skill is a folder /Jade/skills/<kebab-case-name>/ containing SKILL.md, plus optional extra files
(templates, examples) that SKILL.md refers to by relative path.

1. Ask what the skill should do and when it should trigger, unless the user already said.
2. notes_search or ls /Jade/skills so you do not duplicate an existing skill; edit it instead if it is close.
3. Write /Jade/skills/<name>/SKILL.md:
   ---
   name: <name>
   description: <what it does>. Use when <the situations or phrases that should trigger it>.
   ---
   # <Title>
   Numbered, concrete steps using your real tools (read, write, edit, bash, notes_search, web_fetch,
   memory, schedule...). Name exact paths and note formats.
4. The description decides when the skill is used, so make it specific.
5. Tell the user they can run it any time with /skill:<name>.
