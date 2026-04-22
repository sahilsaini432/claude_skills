# Schema Reference

## Wiki page format

```markdown
# <Title>

**Source:** <filename or URL>
**Date ingested:** YYYY-MM-DD
**Type:** Article | PDF | Image | Transcript | Note | Chat

---

## Summary
3–5 sentence synthesis of the key ideas.

## Key Points
- Point 1
- Point 2

## Concepts & Entities
Notable people, tools, frameworks, ideas — one line each.

## Quotes / Highlights
1–3 notable direct quotes or data points. Omit section if none.

## Connections
How this source relates to other things in the wiki.

## Related Pages
- [_overview](_overview.md) — topic index      ← always first; added automatically
- [entity-slug](../_entities/entity.md) — entity page
- [other-slug](other-slug-YYYY-MM-DD.md) — optional additional context

---
*Ingested by brain-wiki*
```

**Link model** (forms distinct topic clusters in Obsidian graph view):

| From | To | Direction |
|------|----|-----------|
| Source page | `_overview.md` (same topic) | spoke → hub |
| Source page | `_entities/*.md` | leaf → shared concept |
| `_overview.md` | source pages (same topic) | hub → spokes |
| `_entities/*.md` | topic `_overview.md` | concept → cluster center |

Entity pages link to topic overviews (not individual source pages) so they bridge
clusters rather than individual leaves. This keeps the graph in distinct topic clusters
connected at their centers instead of collapsing into one blob.

---

## _overview.md format

```markdown
# <Topic Name>

## What this topic covers
2–3 sentence description of the topic and why it exists.

## Pages
- [slug-YYYY-MM-DD](filename.md) — one-line description

## Evolving Thesis
Running synthesis — updated every time a new page is added to this topic.

---
*Managed by brain-wiki*
```

---

## Memory.md format

```markdown
# Memory

> Auto-maintained index of all wiki pages, grouped by topic.
> Managed by brain-wiki — do not edit manually.

---

## Claude Code & Skills
- [skill-builder-2026-04-10](wiki/claude-code-and-skills/skill-builder-2026-04-10.md) — Built brain-wiki skill with Ollama integration

## Python & Data Science
- [ppo-frozenlake-2026-04-01](wiki/python-and-data-science/ppo-frozenlake-2026-04-01.md) — PPO training loop for FrozenLake

## Queries & Synthesis
- [query-what-do-i-know-about-rl-2026-04-10](wiki/queries-and-synthesis/query-what-do-i-know-about-rl-2026-04-10.md) — Q: What do I know about reinforcement learning?

---
*Last updated: 2026-04-10*
```

---

## log.md format

```
## [YYYY-MM-DD HH:MM] operation | detail
```

Examples:
```
## [2026-04-10 14:32] ingest | paper.pdf → wiki/machine-learning/paper-2026-04-10.md
## [2026-04-10 15:01] query | What do I know about transformers?
## [2026-04-10 16:45] lint | health check complete
```

Grep tips:
```bash
# Last 5 operations
grep "^## \[" log.md | tail -5

# All ingests
grep "^## \[.*\] ingest" log.md

# Today's activity
grep "^## \[2026-04-10" log.md
```

---

## raw/ directory conventions

Sources copied here are **never modified**. This is the source of truth.

```
raw/
├── articles/     .md .html web clips
├── pdfs/         .pdf documents
├── transcripts/  .srt .vtt .transcript YouTube/podcast
├── images/       .jpg .png .webp screenshots, diagrams
├── notes/        .txt freeform notes
└── chats/        .txt .md Claude Code session exports
```
