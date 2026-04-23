---
name: cortex
description: Personal knowledge wiki builder. Ingests any source (PDF, web article, image, transcript, chat session, plain text) into a structured, interlinked Obsidian vault maintained by a local LLM. Use when the user says "ingest this", "add this to my wiki", "summarize and save", "add this to my brain", "save this chat", "query my wiki", "what do I know about X", "lint my wiki", "health check my notes", or anything implying they want to build, search, or maintain a personal knowledge base. Also triggers for "summarize this chat", "save this conversation", or "export session notes" — chat sessions are one supported source type.
---

# cortex

A personal knowledge wiki that compiles and maintains structured, interlinked markdown pages
from any source you feed it. Lives in your Obsidian vault. Syncs to GitHub automatically.

All LLM work runs locally via `gemma4:26b` (Ollama) — no API keys, no cloud calls.
Queries are answered by Claude Code itself, reading the relevant pages directly.

## Prerequisites

```bash
ollama serve
ollama pull gemma4:26b
pip install pymupdf          # for PDF ingestion
```

## .env setup

The skill always looks for its config at:

```
~/.claude/skills/.env
```

```dotenv
BRAIN_VAULT_ROOT=E:\brain

# LLM connection — defaults shown, override for remote Ollama (e.g. Tailscale)
LOCAL_LLM_URL=http://localhost:11434/api/generate
LOCAL_LLM_MODEL=gemma4:26b

# Timeouts in seconds — increase if Ollama is on a remote/slow machine
LLM_TIMEOUT_SHORT=300    # classify, relevance, image reads
LLM_TIMEOUT_MEDIUM=600   # overview, merge, backpatch
LLM_TIMEOUT_LONG=900     # full wiki page generation
```

Only `BRAIN_VAULT_ROOT` is required. All other keys have sensible defaults.
The timeouts are generous by default — lower them if Ollama is local and fast.

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
└── wiki/                        ← LLM-owned wiki pages
    ├── _entities/               ← shared entity/concept pages (cross-topic)
    │   ├── sdl2.md              ← created on 2nd appearance across any source
    │   └── reinforcement-learning.md
    └── <topic-folder>/
        ├── _overview.md         ← living topic synthesis
        └── <slug>-YYYY-MM-DD.md ← links to relevant _entities/ pages
```

## Three operations

See `references/operations.md` for full details.

| Command                  | What it does                                                                        | Model                                             |
| ------------------------ | ----------------------------------------------------------------------------------- | ------------------------------------------------- |
| `ingest <file>`          | **Default: claude-chat mode** — Claude Code synthesizes the page; zero Ollama calls | Claude Code                                       |
| `ingest <file> --ollama` | Opt-in: uses local Ollama for synthesis, merge, entities, back-patching             | gemma4:26b (local)                                |
| `query "question"`       | Load relevant pages → print for Claude Code to answer                               | gemma4:26b (topic finding) + Claude Code (answer) |
| `lint`                   | Orphans, dead links, missing overviews, contradiction scan                          | gemma4:26b (local)                                |

## Entity system

After writing each wiki page, `ingest.py` automatically:

1. **Extracts entities** — asks `gemma4:26b` to identify significant tools, frameworks,
   algorithms, people, and concepts from the source (3–8 per source, quality over quantity)
2. **Updates `entity_registry.json`** — tracks how many times each entity has been seen
3. **Creates entity pages on 2nd appearance** — `wiki/_entities/<slug>.md` is created
   the second time an entity appears, back-filled with content from both sources
4. **Updates entity pages on subsequent appearances** — new facts merged in, count updated
5. **Cross-links** — source wiki pages link to entity pages; entity pages link back to
   topic `_overview.md` files (not individual source pages) to preserve distinct clusters

Entity pages live in `wiki/_entities/`. Each source page also gets a `[_overview](_overview.md)`
parent link forming hub-and-spoke topology per topic. In Obsidian's graph this produces
distinct topic clusters bridged at their centers (overview nodes) via entity pages.

## How to invoke in Claude Code

Claude-chat mode is the **default** for `ingest`. No flag needed — Claude Code
synthesizes the wiki page with zero Ollama calls. Only pass `--ollama` when you
explicitly want the local pipeline (overnight batch, scripted runs).

```
/cortex ingest /path/to/file.pdf        ← default: claude-chat mode
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

`query.py` uses gemma4:26b only to identify which topics are relevant, then loads
those wiki pages and prints them to stdout. Claude Code reads that output and synthesizes
the answer directly — no API call, no extra cost. To file the answer back:

```bash
python3 scripts/query.py "question" --save answer.md
```

## Supported source types

| Extension                   | Type                  | Handler                     |
| --------------------------- | --------------------- | --------------------------- |
| `http://` `https://` URL    | Article               | Fetch + HTML strip (stdlib) |
| `.md` `.html`               | Article / Chat / Note | Text read                   |
| `.txt`                      | Note                  | Text read                   |
| `.pdf`                      | PDF                   | pymupdf text extraction     |
| `.jpg` `.png` `.webp`       | Image                 | gemma4:26b vision           |
| `.srt` `.vtt` `.transcript` | Transcript            | Timestamp-stripped text     |

## Default claude-chat mode: Claude Code synthesizes the wiki page

Default mode — zero Ollama calls. Claude Code reads the source and writes the
wiki page itself. No warm-up, no GPU spin-up, no wait.

This is a **two-phase protocol**:

### Phase 1 — print synthesis prompt, exit 2

```bash
python3 ~/.claude/skills/cortex/scripts/ingest.py <file>
```

The script:

1. Reads the source file
2. Prints a `cortex CLAUDE-CHAT PHASE 1` block to stdout containing:
   - `SOURCE_NAME`, `SOURCE_TYPE`, `SOURCE_PATH`, `TODAY`, `AUTO_SLUG`
   - `EXISTING_PAGE` — path to any existing page with the same slug (merge hint)
   - `MEMORY_MD_EXCERPT` — first 3000 chars of Memory.md for topic classification
   - `SOURCE_CONTENT` — up to 12 000 chars of the source
   - Step-by-step instructions for Claude Code
3. Exits with **code 2** (sentinel meaning "Claude Code must complete phase 2")

### Phase 2 — Claude Code synthesizes, then commits

Claude Code must:

1. **Classify** the source against `MEMORY_MD_EXCERPT` — pick a topic, confirm/revise the slug, write a ≤12-word description.
2. **Write the wiki page** using the standard schema (same as normal ingest):
   - `# Title`, `**Source:**`, `**Date ingested:**`, `**Type:**`
   - `## Summary`, `## Key Points`, `## Concepts & Entities`
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

Phase 2 skips (vs `--ollama` path):

- No Ollama classify/generate/merge calls
- No Ollama overview synthesis (appends a plain entry instead)
- No Ollama back-patching (prints the pages that need cross-refs for you to handle)
- Entity page creation/update still runs (using the entities you provided)

### When to pass --ollama

| Situation                                      | Use                   |
| ---------------------------------------------- | --------------------- |
| Default (Claude Code invoking ingest)          | No flag — claude-chat |
| Batch ingesting many files overnight / cron    | `--ollama`            |
| Scripted/non-Claude runs with Ollama available | `--ollama`            |

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

Default mode is claude-chat — the script prints phase 1 output and exits with code 2.
Claude Code then synthesizes the wiki page and re-runs with `--page-content-file`.
Always pass `--yes` from Claude Code — the script cannot accept interactive input.

The script will:

- Detect it as a Chat type (USER:/ASSISTANT: pattern)
- Copy the raw file to `raw/chats/`
- Print SYNTHESIS PROMPT (phase 1) for Claude Code to consume
- On re-run: write wiki page, update `Memory.md`, `log.md`, entities, cross-references

Pass `--ollama` instead if you want the local model to synthesize (overnight batch).
Ollama-specific flags that only apply with `--ollama`:

- `--no-ping` — skip model warm-up
- `--no-unload` — ping without evicting from VRAM first

**Do not write wiki pages directly** — always go through `ingest.py` so the raw
source is archived, the log is updated, and cross-references are maintained.

## Additional references

- `references/operations.md` — detailed workflow for each operation
- `references/schema.md` — wiki page formats, Memory.md structure, log format
