---
name: chat-summarizer
description: Summarizes the current chat session and saves it as a structured .md file. Trigger this skill whenever the user asks to "summarize this chat", "save this conversation", "export session notes", "generate a chat summary", "make a markdown of this session", or anything implying they want a written record or recap of the current conversation. Even for casual phrasings like "wrap up our chat" or "save what we talked about", use this skill. Always produce a .md file as output — never just an inline summary if the user wants a file.
---

# Chat Summarizer Skill

Produces a structured `.md` summary using **gemma4:31b via Ollama** (no Claude API credits used),
saves it into a **per-topic folder**, cross-references it with related past sessions,
and links everything into `Memory.md`.

## Prerequisites

```bash
ollama serve
ollama pull gemma4:31b
```

## Vault structure produced

```
$SUMMARY_OUTPUT_DIR/
├── Memory.md                          ← master index, grouped by topic
├── claude-code-and-skills/
│   ├── skill-builder-2026-04-08.md
│   ├── chat-summarizer-evolved-2026-04-09.md
│   └── chat-summarizer-xref-2026-04-10.md
└── python-and-data-science/
    └── ppo-frozenlake-2026-04-01.md
```

---

## Trigger phrases

**Active session** (default): "summarize this chat", "save this conversation", "export session notes",
"generate a chat summary", "make a markdown of this session", "wrap up our chat"

**External file**: "summarize this file", "process this chat log", "run the flow on this md",
"add this to my vault", "index this chat export"

---

## Workflow

### Determine input mode

| Situation                                    | Input mode                                |
| -------------------------------------------- | ----------------------------------------- |
| User asks to summarize the current session   | **Mode A** — dump transcript from context |
| User provides a path or uploads a `.md` file | **Mode B** — use that file directly       |

---

### Mode A — Active session

#### Step A1 — Derive output filename

From the conversation, form a slug: `<topic-slug>-<YYYY-MM-DD>.md`
Fallback: `chat-summary-<YYYY-MM-DD>.md`
`TEMP_PATH=/tmp/<slug>.md`

#### Step A2 — Pre-classify

```bash
python scripts/update_memory.py "$TEMP_PATH" --memory "$MEMORY_MD_PATH" --pre-run
```

Parse `topic`, `topic_folder`, `entries`.

#### Step A3 — Dump raw transcript

Write full conversation to `/tmp/chat-transcript.txt`:

```
USER: <message>
ASSISTANT: <message>
```

#### Step A4 — Summarize

```bash
python scripts/summarize_with_ollama.py /tmp/chat-transcript.txt \
    --related "claude-code-and-skills/skill-builder-2026-04-08.md|Built initial skill structure" \
    > "$TEMP_PATH"
```

Omit `--related` if `entries` was empty.

---

### Mode B — External .md file

#### Step B1 — Identify the input file

Get the path from the user's message. If they uploaded a file, use its path.
`INPUT_FILE=/path/to/their-chat-export.md`

#### Step B2 — Derive output filename

Read the first heading or first few lines of the file to infer a topic slug.
`TEMP_PATH=/tmp/<slug>.md`

#### Step B3 — Pre-classify

```bash
python scripts/update_memory.py "$TEMP_PATH" --memory "$MEMORY_MD_PATH" --pre-run
```

Parse `topic`, `topic_folder`, `entries`.

#### Step B4 — Summarize with --md flag

```bash
python scripts/summarize_with_ollama.py "$INPUT_FILE" --md \
    --related "claude-code-and-skills/skill-builder-2026-04-08.md|Built initial skill structure" \
    > "$TEMP_PATH"
```

The `--md` flag tells the summarizer the input is already markdown, not a raw transcript.
Omit `--related` if `entries` was empty.

---

### Step 5 — Commit (both modes)

```bash
python scripts/update_memory.py "$TEMP_PATH" --memory "$MEMORY_MD_PATH"
```

This will:

1. Move the file into `$SUMMARY_OUTPUT_DIR/<topic_folder>/`
2. Insert a new entry under the correct `## Topic` heading in `Memory.md`
3. Back-patch every existing `.md` in the topic folder
4. Add back-references in the new file to all existing ones
5. Print `FINAL_PATH=...`

### Step 6 — Present

Call `present_files` on the final `.md` path.
Confirm how many files were cross-referenced and which topic folder was used.

---

## .env variables

| Variable             | Purpose                                                             |
| -------------------- | ------------------------------------------------------------------- |
| `SUMMARY_OUTPUT_DIR` | Base directory (vault root or notes folder)                         |
| `MEMORY_MD_PATH`     | Full path to `Memory.md` (default: `$SUMMARY_OUTPUT_DIR/Memory.md`) |

## Error handling

- Ollama unreachable → clear message, abort
- `Memory.md` missing → created from template automatically
- Topic folder missing → created automatically
- Back-patch target missing → logged and skipped
- Topic ambiguous → falls back to `## Uncategorized` / `uncategorized/` folder

## Style notes

- Do NOT call the Claude/Anthropic API — all LLM work goes through Ollama
- All links are relative — portable across machines via GitHub sync
- Do not reproduce long code blocks in summaries; reference by name/purpose
