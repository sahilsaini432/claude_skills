---
name: brain-wiki
description: Personal knowledge wiki builder. Ingests any source (PDF, web article, image, transcript, chat session, plain text) into a structured, interlinked Obsidian vault maintained by a local LLM. Use when the user says "ingest this", "add this to my wiki", "summarize and save", "add this to my brain", "save this chat", "query my wiki", "what do I know about X", "lint my wiki", "health check my notes", or anything implying they want to build, search, or maintain a personal knowledge base. Also triggers for "summarize this chat", "save this conversation", or "export session notes" — chat sessions are one supported source type.
---

# brain-wiki

A personal knowledge wiki that compiles and maintains structured, interlinked markdown pages
from any source you feed it. Lives in your Obsidian vault. Syncs to GitHub automatically.

All LLM work runs locally via `gemma4:31b` (Ollama) — no API keys, no cloud calls.
Queries are answered by Claude Code itself, reading the relevant pages directly.

## Prerequisites

```bash
ollama serve
ollama pull gemma4:31b
pip install pymupdf          # for PDF ingestion
```

## .env setup

The skill always looks for its config at:
```
~/.claude/skills/brain-wiki/.env
```

```dotenv
BRAIN_VAULT_ROOT=E:\brain
LOCAL_LLM_URL=http://localhost:11434/api/generate   # optional, this is the default
LOCAL_LLM_MODEL=gemma4:31b                          # optional, this is the default
```

Only `BRAIN_VAULT_ROOT` is required. The LLM settings default to Ollama on localhost
if not set — override them if your Ollama runs on a different port or machine.
Since the .env is at a fixed path, the skill works from any working directory.

## Vault structure

```
E:\brain\
├── Memory.md                    ← master index, grouped by topic
├── log.md                       ← append-only operation history
├── raw/                         ← immutable source files (never modified)
│   ├── articles/
│   ├── pdfs/
│   ├── transcripts/
│   ├── images/
│   ├── notes/
│   └── chats/
└── wiki/                        ← LLM-owned wiki pages
    └── <topic-folder>/
        ├── _overview.md         ← living topic synthesis
        └── <slug>-YYYY-MM-DD.md
```

## Three operations

See `references/operations.md` for full details.

| Command | What it does | Model |
|---|---|---|
| `ingest <file>` | Read source → generate wiki page → update index → cross-reference | gemma4:31b (local) |
| `query "question"` | Load relevant pages → print for Claude Code to answer | gemma4:31b (topic finding) + Claude Code (answer) |
| `lint` | Orphans, dead links, missing overviews, contradiction scan | gemma4:31b (local) |

## How to invoke in Claude Code

```
/brain-wiki ingest /path/to/file.pdf
/brain-wiki query "what do I know about reinforcement learning?"
/brain-wiki lint --fix
```

Or naturally:
- "Ingest this article" + paste path
- "Add this chat to my wiki"
- "What do I know about X?"
- "Health check my wiki"

## How query works in Claude Code

`query.py` uses gemma4:31b only to identify which topics are relevant, then loads
those wiki pages and prints them to stdout. Claude Code reads that output and synthesizes
the answer directly — no API call, no extra cost. To file the answer back:

```bash
python scripts/query.py "question" --save answer.md
```

## Supported source types

| Extension | Type | Handler |
|---|---|---|
| `.md` `.html` | Article / Chat | Text read |
| `.txt` | Note | Text read |
| `.pdf` | PDF | pymupdf text extraction |
| `.jpg` `.png` `.webp` | Image | gemma4:31b vision |
| `.srt` `.vtt` `.transcript` | Transcript | Timestamp-stripped text |

## Chat session ingest

When triggered by "summarize this chat" / "save this conversation":
1. Dump the current context to `/tmp/chat-transcript.txt` (USER: / ASSISTANT: format)
2. Run: `python scripts/ingest.py /tmp/chat-transcript.txt`
   The script detects `.txt` and routes it to `raw/chats/`

## Additional references

- `references/operations.md` — detailed workflow for each operation
- `references/schema.md` — wiki page formats, Memory.md structure, log format
