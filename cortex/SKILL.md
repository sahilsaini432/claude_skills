---
name: cortex
description: Personal knowledge wiki builder. Ingests any source (PDF, web article, image, transcript, chat session, plain text) into a structured, interlinked Obsidian vault. Use when the user says "ingest this", "add this to my wiki", "summarize and save", "add this to my brain", "save this chat", "query my wiki", "what do I know about X", "lint my wiki", "health check my notes", or anything implying they want to build, search, or maintain a personal knowledge base. Also triggers for "summarize this chat", "save this conversation", or "export session notes" — chat sessions are one supported source type.
---

# cortex

A personal knowledge wiki that compiles and maintains structured, interlinked
markdown pages from any source you feed it. Lives in your Obsidian vault.

All synthesis is done by Claude Code itself — no local LLM, no API keys, no
cloud calls. The Python scripts only do file IO, classification routing, and
deterministic linking. Each operation that needs synthesis runs as a two-phase
flow: phase 1 prints a structured prompt and exits with code 2; Claude Code
reads the prompt, writes the page, and re-runs the script with the result.

## Prerequisites

```bash
pip install pymupdf          # for PDF ingestion (only PDF needs this)
```

## .env setup

The skill always looks for its config at:

```
~/.claude/skills/.env
```

```dotenv
BRAIN_VAULT_ROOT=E:\brain
```

`BRAIN_VAULT_ROOT` is the only required key.

## Vault structure

```
E:\brain\
├── Memory.md                    ← master index, grouped by topic
├── log.md                       ← append-only operation history
├── entity_registry.json         ← tracks entity appearance counts
├── raw/                         ← immutable source files (never modified)
│   ├── articles/
│   ├── pdfs/
│   ├── transcripts/
│   ├── images/
│   ├── notes/
│   └── chats/
└── wiki/                        ← Claude-Code-owned wiki pages
    ├── _entities/               ← shared entity/concept pages (cross-topic)
    │   ├── sdl2.md              ← created on first appearance, enriched via --backfill
    │   └── reinforcement-learning.md
    └── <topic-folder>/
        ├── _overview.md         ← topic index (append-only)
        └── <slug>-YYYY-MM-DD.md ← links to relevant _entities/ pages
```

## Three operations

See `references/operations.md` for full details.

| Command                             | What it does                                                                  |
| ----------------------------------- | ----------------------------------------------------------------------------- |
| `ingest <file>`                     | Two-phase: phase 1 prints synthesis prompt and exits 2; Claude Code synthesizes; phase 2 commits |
| `query "question"`                  | Print Memory.md + every topic Memory.md and _overview.md; Claude Code reads relevant pages and answers |
| `lint`                              | Dead-link, orphan, and entity-registry checks (deterministic, no LLM)         |
| `entities.py --backfill`            | Two-phase: phase 1 lists stub/missing entity pages; Claude Code enriches them; phase 2 commits |

## Entity system

After writing each wiki page, ingest phase 2 automatically:

1. **Reads the entities JSON** Claude Code wrote during phase 1 — 3-8 significant
   tools, frameworks, algorithms, people, or concepts (quality over quantity).
2. **Updates `entity_registry.json`** — tracks how many times each entity has been
   seen.
3. **Creates a stub entity page on first appearance** — `wiki/_entities/<slug>.md`
   gets a minimal placeholder with the entity's name, type, and description.
4. **Bumps the count on subsequent appearances** — no content rewrite.
5. **Cross-links** — source page links to entity pages; entity pages link back
   to topic `_overview.md` files (not individual source pages) to preserve
   distinct topic clusters.

Run `python3 scripts/entities.py --backfill` later to enrich stubs into full
entity pages — it prints a phase-1 prompt with all stub/missing entities, and
Claude Code synthesizes proper pages.

Entity pages live in `wiki/_entities/`. Each source page also gets a
`[_overview](_overview.md)` parent link forming hub-and-spoke topology per
topic. In Obsidian's graph this produces distinct topic clusters bridged at
their centers (overview nodes) via entity pages.

## How to invoke in Claude Code

```
/cortex ingest /path/to/file.pdf
/cortex ingest https://example.com/article
/cortex query "what do I know about reinforcement learning?"
/cortex lint --fix
```

Or naturally:

- "Ingest this article" + paste path
- "Add this chat to my wiki"
- "What do I know about X?"
- "Health check my wiki"

## How query works in Claude Code

`query.py` does no synthesis itself. It prints the master `Memory.md`, every
per-topic `Memory.md`, and every topic `_overview.md`. Claude Code reads that
output, identifies which source pages are relevant, uses its `Read` tool to
load them, and synthesizes the answer in chat.

To file an answer back into the wiki, write the answer as a complete
markdown page first, then:

```bash
python3 scripts/query.py "question" --save answer.md
```

The file is stored verbatim under `wiki/queries-and-synthesis/`.

## Supported source types

| Extension                   | Type                  | Handler                             |
| --------------------------- | --------------------- | ----------------------------------- |
| `http://` `https://` URL    | Article               | Fetch + HTML strip (stdlib)         |
| `.md` `.html`               | Article / Chat / Note | Text read                           |
| `.txt`                      | Note                  | Text read                           |
| `.pdf`                      | PDF                   | pymupdf text extraction             |
| `.jpg` `.png` `.webp`       | Image                 | Claude Code's Read tool (in phase 1) |
| `.srt` `.vtt` `.transcript` | Transcript            | Timestamp-stripped text             |

For images, phase 1 sets SOURCE_CONTENT to a placeholder pointing at the file.
Claude Code uses its `Read` tool on the image directly to extract content.

## Ingest two-phase protocol

### Phase 1 — print synthesis prompt, exit 2

```bash
python3 ~/.claude/skills/cortex/scripts/ingest.py <file>
```

The script:

1. Reads the source file
2. Prints a `cortex PHASE 1` block to stdout containing:
   - `SOURCE_NAME`, `SOURCE_TYPE`, `SOURCE_PATH`, `TODAY`, `AUTO_SLUG`
   - `EXISTING_PAGE` — path to any existing page with the same slug (merge hint)
   - `MEMORY_MD_EXCERPT` — first 3000 chars of Memory.md for topic classification
   - `SOURCE_CONTENT` — up to 12 000 chars of the source
   - Step-by-step instructions for Claude Code
3. Exits with **code 2** (sentinel meaning "Claude Code must complete phase 2")

### Phase 2 — Claude Code synthesizes, then commits

Claude Code must:

1. **Classify** the source against `MEMORY_MD_EXCERPT` — pick a topic, confirm/revise the slug, write a ≤12-word description.
2. **Write the wiki page** using the standard schema:
   - `# Title`, `**Source:**`, `**Date ingested:**`, `**Type:**`
   - `## Summary` ← 1–2 paragraphs; readable cold by someone new to the topic
   - `## Background / Context` ← OPTIONAL — prerequisites/jargon. Omit if topic is common knowledge.
   - `## Key Points`
   - `## Detailed Notes` ← OPTIONAL — preserve source structure verbatim (tables as markdown tables, code as fenced blocks with language tags, numbered steps as numbered lists, diagrams as short text descriptions). Omit if source has no structured content.
   - `## Concepts & Entities`
   - `## Quotes / Highlights`, `## Connections`
   - `## Related Pages` ← leave blank
3. **Extract 3–8 entities** as JSON: `[{"name":…,"slug":…,"description":…,"type":…},…]`
4. Write the wiki page to a temp file and entities to a temp file.
5. Re-run ingest.py with:

```bash
python3 ~/.claude/skills/cortex/scripts/ingest.py <SOURCE_PATH> --yes \
  --page-content-file /tmp/wiki_page.md \
  --entities-file /tmp/entities.json \
  --topic "Topic Name" \
  --slug "your-slug" \
  --description "One-line description of this source"
```

Phase 2 then writes the wiki page, archives the source under `raw/`, updates
`Memory.md` (master + per-topic), the topic `_overview.md`, `log.md`, the
entity registry, and adds deterministic Related-Pages cross-references.

## Chat session ingest

When triggered by "summarize this chat", "save this conversation", "save this session",
or any similar phrase — **always follow these exact steps, no shortcuts**:

### Step 1 — Get the raw/chats path

```bash
python3 ~/.claude/skills/cortex/scripts/ingest.py --raw-chats-path
```

This prints the full path to `raw/chats/` from your vault. Use it as the destination
for the transcript file in the next step.

### Step 2 — Dump the transcript

Write every message in the current conversation directly into `raw/chats/` as
`<slug>-<YYYY-MM-DD>.md` where slug is a 2–5 word kebab-case summary of the session topic.

Format: one turn per line, prefixed with `USER:` or `ASSISTANT:`.
Do NOT summarize or paraphrase — write the raw verbatim content.

```bash
python3 -c "
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.home() / '.claude/skills/cortex/scripts'))
from config import cfg
cfg.ensure_dirs()
transcript = '''USER: <exact message>
ASSISTANT: <exact message>
'''
dest = cfg.raw_dir / 'chats' / '<slug>-<YYYY-MM-DD>.md'
dest.write_text(transcript, encoding='utf-8')
print(dest)
"
```

### Step 3 — Run ingest

```bash
python3 ~/.claude/skills/cortex/scripts/ingest.py <path printed above> --yes
```

The script prints phase 1 output and exits with code 2. Claude Code then
synthesizes the wiki page and re-runs with `--page-content-file`. Always pass
`--yes` from Claude Code — the script cannot accept interactive input.

The script will:

- Detect it as a Chat type (USER:/ASSISTANT: pattern)
- Copy the raw file to `raw/chats/`
- Print SYNTHESIS PROMPT (phase 1) for Claude Code to consume
- On re-run: write wiki page, update `Memory.md`, `log.md`, entities, cross-references

**Do not write wiki pages directly** — always go through `ingest.py` so the raw
source is archived, the log is updated, and cross-references are maintained.

## Additional references

- `references/operations.md` — detailed workflow for each operation
- `references/schema.md` — wiki page formats, Memory.md structure, log format
