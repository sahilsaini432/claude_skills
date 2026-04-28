---
name: memory
description: >
  Session memory skill. Summarizes the current conversation and appends a structured
  session log to CLAUDE.md in the current project directory. Use when user says
  "save this session", "update CLAUDE.md", "summarize and save", "save to memory",
  "memory save", "save session history", or invokes /memory.
  For clearing history: "clear session history", "wipe memory", "memory clear", or /memory clear.
---

Two commands: default (summarize + save) and `clear`.

Detect which command from the invocation:
- `/memory clear` or "clear session history" or "wipe memory" → run **Clear command**
- Everything else → run **Save command**

---

## Save command

Synthesize the full conversation into a session entry and update CLAUDE.md in the current working directory.

### Step 1 — Read existing CLAUDE.md

Use the Read tool on `CLAUDE.md` (relative path in cwd).

- If file does not exist: you will create it from scratch (just the session history section)
- If it exists: note all content above `## Session History` — you must preserve it exactly

### Step 2 — Synthesize the session entry

Carefully review the entire conversation and extract:

**Summary** — one sentence: what was the session's main purpose?

**Topics discussed** — bullet list of subjects covered (questions asked, features explained, concepts explored)

**Changes made** — table of every file modified or created. Columns: File, What, Why.
- File: backtick-wrapped path
- What: concise description of the change
- Why: the reason or problem it solved

**Code issues & examples** — concrete bugs, mistakes, or non-obvious patterns encountered. Each entry must include:
- file:line reference when available
- What the problem was
- What the fix/solution was and why

**Key decisions** — any architectural, design, or approach decisions made, with reasoning

**Open items** — anything unfinished, deferred, or explicitly left for a future session. Use `[ ]` checkboxes.

If a category has nothing to report, omit it entirely (don't write "None").

### Step 3 — Format the entry

```
### YYYY-MM-DD

**Summary:** <one sentence>

**Topics discussed:**
- topic 1
- topic 2

**Changes made:**
| File | What | Why |
|------|------|-----|
| `path/file.ts` | description | reason |

**Code issues & examples:**
- `file.ts:42` — problem description. Fix: what was done and why.

**Key decisions:**
- Decision: rationale

**Open items:**
- [ ] item

---
```

Use today's date (from session context or system). Newest entry goes at the TOP of the history section.

### Step 4 — Write CLAUDE.md

**Case A — file does not exist:**
Create CLAUDE.md with this structure:
```
## Session History

<new entry>
```

**Case B — file exists, no `## Session History` section:**
Append to end of file:
```
\n## Session History\n\n<new entry>
```

**Case C — file exists, `## Session History` section exists:**
Insert the new entry immediately after the `## Session History` heading line (before any existing entries). Preserve all existing entries and all content above the heading.

Use Write tool for case A, Edit tool for cases B and C.

### Quality rules

- Every change in "Changes made" must explain the WHY, not just the what
- Code issues must be specific enough that a future Claude can understand without seeing the original conversation
- Summary must be useful to a cold-start Claude with zero context
- Do NOT fabricate file paths or line numbers — only include what actually came up in conversation
- Do NOT include trivial back-and-forth (greetings, confirmations) — only substantive content

---

## Clear command

Remove all session history from CLAUDE.md, preserving project documentation above it.

### Steps

1. Read CLAUDE.md with the Read tool
2. If file does not exist: respond "No CLAUDE.md found in current directory." and stop
3. Find the line containing `## Session History`
4. If not found: respond "No session history found in CLAUDE.md." and stop
5. Truncate everything from `## Session History` to end of file
6. Write the truncated content back (trim trailing whitespace/newlines from end)
7. Confirm: "Session history cleared. Project documentation preserved."

Use Write tool to overwrite the file with cleaned content.

---

## Reminders

- Always use the Read tool before writing — never overwrite blindly
- Preserve project docs (architecture notes, setup instructions, coding conventions) above `## Session History` — never touch them
- If CLAUDE.md has content the user wrote manually, treat it as sacred
- Newest session entry is always first under the heading — reverse chronological order
