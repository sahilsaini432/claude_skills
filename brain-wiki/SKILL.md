---
name: brain-wiki
description: Personal knowledge wiki builder. Ingests any source (PDF, web article, image, transcript, chat session, plain text) into a structured, interlinked Obsidian vault maintained by a local LLM. Use when the user says "ingest this", "add this to my wiki", "summarize and save", "add this to my brain", "save this chat", "query my wiki", "what do I know about X", "lint my wiki", "health check my notes", or anything implying they want to build, search, or maintain a personal knowledge base. Also triggers for "summarize this chat", "save this conversation", or "export session notes" — chat sessions are one supported source type.
---

# brain-wiki

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

| Command                       | What it does                                                           | Model                                             |
| ----------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------- |
| `ingest <file>`               | Read source → generate wiki page → update index → cross-reference      | gemma4:26b (local)                                |
| `ingest <file> --claude-chat` | Same pipeline but Claude Code synthesizes the page — zero Ollama calls | Claude Code                                       |
| `query "question"`            | Load relevant pages → print for Claude Code to answer                  | gemma4:26b (topic finding) + Claude Code (answer) |
| `lint`                        | Orphans, dead links, missing overviews, contradiction scan             | gemma4:26b (local)                                |

## Entity system

After writing each wiki page, `ingest.py` automatically:

1. **Extracts entities** — asks `gemma4:26b` to identify significant tools, frameworks,
   algorithms, people, and concepts from the source (3–8 per source, quality over quantity)
2. **Updates `entity_registry.json`** — tracks how many times each entity has been seen
3. **Creates entity pages on 2nd appearance** — `wiki/_entities/<slug>.md` is created
   the second time an entity appears, back-filled with content from both sources
4. **Updates entity pages on subsequent appearances** — new facts merged in, count updated
5. **Cross-links** — source wiki pages link to relevant entity pages and vice versa

Entity pages live in `wiki/_entities/` so they appear as hubs in Obsidian's graph view,
visually connecting all topics that reference the same tool or concept.

## How to invoke in Claude Code

> **IMPORTANT — always use `--claude-chat` for ingest when running from Claude Code.**
> Claude Code must never invoke the normal (Ollama) ingest path. The `--claude-chat`
> flag replaces all local LLM calls with Claude Code's own synthesis. This applies
> to every ingest regardless of how the user phrases the request.

```
/brain-wiki ingest /path/to/file.pdf        ← Claude Code automatically adds --claude-chat
/brain-wiki query "what do I know about reinforcement learning?"
/brain-wiki lint --fix
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

| Extension                   | Type                  | Handler                 |
| --------------------------- | --------------------- | ----------------------- |
| `.md` `.html`               | Article / Chat / Note | Text read               |
| `.txt`                      | Note                  | Text read               |
| `.pdf`                      | PDF                   | pymupdf text extraction |
| `.jpg` `.png` `.webp`       | Image                 | gemma4:26b vision       |
| `.srt` `.vtt` `.transcript` | Transcript            | Timestamp-stripped text |

## --claude-chat flag: Claude Code synthesizes the wiki page

Use `--claude-chat` when you want **zero Ollama calls** — Claude Code reads the
transcript and writes the wiki page itself. No warm-up, no GPU spin-up, no wait.

This is a **two-phase protocol**:

### Phase 1 — print synthesis prompt, exit 2

```bash
python3 ~/.claude/skills/brain-wiki/scripts/ingest.py <file> --claude-chat
```

The script:

1. Reads the source file
2. Prints a `BRAIN-WIKI CLAUDE-CHAT PHASE 1` block to stdout containing:
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
python3 ~/.claude/skills/brain-wiki/scripts/ingest.py <SOURCE_PATH> \
  --claude-chat --yes \
  --page-content-file /tmp/wiki_page.md \
  --entities-file /tmp/entities.json \
  --topic "Topic Name" \
  --slug "your-slug" \
  --description "One-line description of this source"
```

Phase 2 does everything the normal path does **except**:

- No Ollama classify/generate/merge calls
- No Ollama overview synthesis (appends a plain entry instead)
- No Ollama back-patching (prints the pages that need cross-refs for you to handle)
- Entity page creation/update still runs (using the entities you provided)

### When to use --claude-chat vs normal ingest

| Situation                                 | Use                                             |
| ----------------------------------------- | ----------------------------------------------- |
| Ollama is running and GPU is available    | Normal `ingest`                                 |
| Ollama is down / slow / on remote machine | `--claude-chat`                                 |
| Ingesting the _current_ Claude Code chat  | `--claude-chat` (you're already in Claude Code) |
| Batch ingesting many files overnight      | Normal `ingest`                                 |

## Chat session ingest (--claude-chat mode)

When triggered by "summarize this chat", "save this conversation", "save this session",
or any similar phrase — **always follow these exact steps, no shortcuts**:

### Step 1 — Get the raw/chats path

```bash
python3 ~/.claude/skills/brain-wiki/scripts/ingest.py --raw-chats-path
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
sys.path.insert(0, str(pathlib.Path.home() / '.claude/skills/brain-wiki/scripts'))
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
python3 ~/.claude/skills/brain-wiki/scripts/ingest.py <path printed above> --yes
```

Always pass `--yes` when running from Claude Code — the script cannot accept interactive
input. The generated page preview is still printed to the log for you to review.

The script automatically warms up the local model before starting:

1. Sends `keep_alive=0` to Ollama to evict the model from VRAM (clean slate)
2. Sends a minimal ping prompt to force a fresh load into GPU memory
3. Waits for the response — may take up to 15 mins on cold start
4. Prints "Model ready" then proceeds with ingest

Flags to control this behaviour:

- `--no-ping` — skip warm-up entirely (model must already be loaded)
- `--no-unload` — ping without evicting first (faster if model is already warm)

The script will:

- Detect it as a Chat type (USER:/ASSISTANT: pattern)
- Copy the raw file to `raw/chats/`
- Generate a wiki page via gemma4:26b
- Show you a preview for approval
- Write to `wiki/<topic>/`, update `Memory.md`, `log.md`, and cross-references

**Do not write wiki pages directly** — always go through `ingest.py` so the raw
source is archived, the log is updated, and cross-references are maintained.

## Additional references

- `references/operations.md` — detailed workflow for each operation
- `references/schema.md` — wiki page formats, Memory.md structure, log format
