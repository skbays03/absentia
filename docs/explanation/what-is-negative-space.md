# What is negative-space search?

> _Stub. To be written before any v1.0 push — this is one of the three "punch above their weight" docs (alongside why-no-llm and how-mining-works). Tracked in `DEFERRALS.md`._

This doc explains the value prop in prose. Most code-hygiene tools answer "does this code violate someone's rule?" or "is this code likely buggy?" Negative-space search asks a third question: *"does this code follow the patterns the rest of this codebase follows?"*

Outline to write against:
- The problem: codebases drift in ways that aren't bugs and aren't style violations
- The shelf-positioning: linters vs. static analyzers vs. lacuna
- One worked example: an API folder where 9/10 endpoints follow a convention
- Why this is "negative space" — the answer is in the gap, not the code
- When to reach for lacuna and when not to
