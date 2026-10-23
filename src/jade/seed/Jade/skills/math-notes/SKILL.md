---
name: math-notes
description: Write or tidy math, physics or statistics notes with LaTeX formulas. Use for equations, proofs, derivations, formula sheets or when the user asks for LaTeX.
---
# Math notes

The vault renders LaTeX with KaTeX.
- Inline: $E = mc^2$. Display: put $$ on its own lines around the formula.
- Also allowed: \( inline \) and \[ display \], or a fenced block with the language "math".
- Do not put math inside backticks, or it shows as code.
- Escape a literal dollar sign as \$.
- One idea per display block; explain each symbol right after it in a bullet list.
- For derivations, number the steps and keep each step to one transformation.
- KaTeX supports most of amsmath (aligned, cases, pmatrix, \frac, \sum, \int, \mathbb).
  It has no \newcommand across blocks: define macros inside each block.
