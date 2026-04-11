---
name: chat-summarizer
description: Summarizes the current chat session and saves it as a structured .md file. Trigger this skill whenever the user asks to "summarize this chat", "save this conversation", "export session notes", "generate a chat summary", "make a markdown of this session", or anything implying they want a written record or recap of the current conversation. Even for casual phrasings like "wrap up our chat" or "save what we talked about", use this skill. Always produce a .md file as output — never just an inline summary if the user wants a file.
---

# Chat Summarizer Skill

Produces a structured `.md` summary using **gemma4:31b via Ollama** (no Claude API credits used),
cross-references it with related past sessions, and links everything into `Memory.md`.

## Prerequisites

```bash
ollama serve
ollama pull gemma4:31b
```

---

## Workflow

### Step 1 — Derive output filename

From the conversation, identify the dominant topic and form a slug:
`<topic-slug>-<YYYY-MM-DD>.md`  
Examples: `skill-builder-workflow-2026-04-10.md`, `battleship-sdl2-arch-2026-04-10.md`  
Fallback: `chat-summary-<YYYY-MM-DD>.md`

Determine the output directory from `.env` (`SUMMARY_OUTPUT_DIR`). The full output path is:
`$SUMMARY_OUTPUT_DIR/<slug>.md`

### Step 2 — Pre-classify to find related sessions

Before summarizing, ask `update_memory.py` which topic the new file will belong to,
and get the list of existing sessions in that topic:

```bash
python scripts/update_memory.py "$SUMMARY_OUTPUT_DIR/<slug>.md" \
    --memory "$MEMORY_MD_PATH" \
    --pre-run
```

This prints JSON like:

```json
{
  "topic": "Claude Code & Skills",
  "entries": [
    {"slug": "claude-code-setup-2026-03-22", "path": "notes/claude-code-setup-2026-03-22.md", "description": "..."},
    ...
  ]
}
```

Parse the `entries` array — these are the related sessions to pass to the summarizer.

### Step 3 — Dump raw transcript

Write the full conversation to `/tmp/chat-transcript.txt`, one turn per line:

```
USER: <message>
ASSISTANT: <message>
```

### Step 4 — Summarize with local model

Build `--related` flags from the entries returned in Step 2:

```bash
python scripts/summarize_with_ollama.py /tmp/chat-transcript.txt \
    --related "notes/claude-code-setup-2026-03-22.md|Configured Claude Code custom skills" \
    --related "notes/skill-builder-workflow-2026-04-10.md|Built chat-summarizer skill" \
    > "$SUMMARY_OUTPUT_DIR/<slug>.md"
```

If there are no related entries, omit the `--related` flags.

The output file will contain a `## Related Sessions` section with contextual links
to the related files already written in.

### Step 5 — Commit to Memory.md and back-patch

```bash
python scripts/update_memory.py "$SUMMARY_OUTPUT_DIR/<slug>.md" \
    --memory "$MEMORY_MD_PATH"
```

This will:

1. Insert the new entry under the correct topic heading in `Memory.md`
2. Open every existing `.md` file in that topic and add the new session to _their_ `## Related Sessions`
3. Add back-references in the new file pointing to all existing sessions
4. Update the `*Last updated:*` footer in `Memory.md`

### Step 6 — Copy and present

```bash
python scripts/save_to_env_dir.py "$SUMMARY_OUTPUT_DIR/<slug>.md"
```

Then call `present_files` on the summary `.md`.
Confirm Memory.md was updated and how many files were cross-referenced.

---

## Memory.md structure

```markdown
# Memory

> Auto-maintained index of all chat sessions, grouped by topic.

---

## Claude Code & Skills

- [skill-builder-workflow-2026-04-10](./notes/skill-builder-workflow-2026-04-10.md) — Built chat-summarizer with Ollama and Memory index
- [claude-code-setup-2026-03-22](./notes/claude-code-setup-2026-03-22.md) — Configured Claude Code custom skills directory

## Python & Data Science

- [ppo-frozenlake-report-2026-04-01](./notes/ppo-frozenlake-report-2026-04-01.md) — Implemented PPO training loop for FrozenLake

---

_Last updated: 2026-04-10_
```

## Individual summary file structure

```markdown
# Chat Summary: <title>

...

## Related Sessions

- [claude-code-setup-2026-03-22](../claude-code-setup-2026-03-22.md) — Earlier session where Claude Code was first configured; this session built on that setup
- [skill-builder-workflow-2026-04-10](../skill-builder-workflow-2026-04-10.md) — Continued here; added Ollama summarization to the skill created in this session
  ...
```

---

## .env variables used

| Variable             | Purpose                                           |
| -------------------- | ------------------------------------------------- |
| `SUMMARY_OUTPUT_DIR` | Directory where summary `.md` files are saved     |
| `MEMORY_MD_PATH`     | Full path to `Memory.md` (default: `./Memory.md`) |

## Error handling

- Ollama unreachable → clear message to run `ollama serve`, abort
- `Memory.md` missing → created from template automatically
- Back-patch target file missing → logged and skipped (graceful)
- Topic classification ambiguous → falls back to `## Uncategorized`

## Style notes

- Do NOT call the Claude/Anthropic API at any point — all LLM work goes through Ollama
- Relative links only — keeps the vault portable across machines
- Do not reproduce long code blocks in the summary; reference them by name/purpose
