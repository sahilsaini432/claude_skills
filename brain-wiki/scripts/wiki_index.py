#!/usr/bin/env python3
"""
wiki_index.py — Read/write Memory.md and log.md.

Used by ingest, query, and lint operations.
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path

MEMORY_TEMPLATE = """\
# Memory

> Auto-maintained index of all wiki pages, grouped by topic.
> Managed by brain-wiki — do not edit manually.

---

---
*Last updated: {date}*
"""

LOG_TEMPLATE = """\
# Log

> Append-only record of all brain-wiki operations.

---

"""

BACKPATCH_SYSTEM = """\
You are editing a markdown wiki page.
You will receive:
1. The current content of the page
2. A new "## Related Pages" entry to add

Rules:
- If "## Related Pages" already exists, append the new entry (no duplicates by slug)
- If it does not exist, add it before "## Action Items" or before the final "---"
- Return the COMPLETE updated file — no truncation, no fences, no commentary
"""


# ── Memory.md ─────────────────────────────────────────────────────────────────


def load_memory(memory_path: Path) -> str:
    if memory_path.exists():
        return memory_path.read_text(encoding="utf-8")
    today = date.today().isoformat()
    text = MEMORY_TEMPLATE.format(date=today)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(text, encoding="utf-8")
    return text


def get_topic_entries(memory_text: str, topic: str) -> list[dict]:
    heading = f"## {topic}"
    entries, in_section = [], False
    for line in memory_text.splitlines():
        if line.strip() == heading:
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") or line.strip() == "---":
                break
            m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)\s+[—-]+\s+(.*)", line)
            if m:
                entries.append(
                    {
                        "slug": m.group(1),
                        "path": m.group(2),
                        "description": m.group(3).strip(),
                    }
                )
    return entries


def insert_entry(memory_text: str, topic: str, entry_line: str, today: str) -> str:
    heading = f"## {topic}"
    lines = memory_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == heading:
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, entry_line)
            return _update_footer("\n".join(lines), today)
    # New topic
    new_block = [f"\n{heading}", entry_line]
    footer_idx = next(
        (i for i in range(len(lines) - 1, len(lines) // 2, -1) if lines[i].strip() == "---"),
        None,
    )
    if footer_idx is not None:
        for j, l in enumerate(new_block):
            lines.insert(footer_idx + j, l)
    else:
        lines.extend(new_block)
    return _update_footer("\n".join(lines), today)


def _update_footer(text: str, today: str) -> str:
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("*Last updated:"):
            lines[i] = f"*Last updated: {today}*"
            return "\n".join(lines) + "\n"
    return text + f"\n*Last updated: {today}*\n"


def slugify(topic: str) -> str:
    t = topic.lower().replace("&", "and")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


# ── log.md ────────────────────────────────────────────────────────────────────


def append_log(log_path: Path, operation: str, detail: str):
    """Append one line to log.md.
    Format: ## [YYYY-MM-DD HH:MM] operation | detail
    """
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(LOG_TEMPLATE, encoding="utf-8")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## [{ts}] {operation} | {detail}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── Back-patching ─────────────────────────────────────────────────────────────


def backpatch_file(target_path: Path, new_entry_line: str, call_local_fn, timeout: int = 600) -> bool:
    """Add new_entry_line to target_path's Related Pages section.
    Returns True if file was modified.
    """
    if not target_path.exists():
        print(f"  Skipping backpatch (not found): {target_path}", file=sys.stderr)
        return False
    current = target_path.read_text(encoding="utf-8")
    slug_m = re.search(r"\[([^\]]+)\]", new_entry_line)
    if slug_m and slug_m.group(1) in current:
        return False  # already linked
    prompt = f"Current file:\n\n{current}\n\n" f"New related page entry to add:\n{new_entry_line}"
    updated = call_local_fn(prompt, BACKPATCH_SYSTEM, timeout=timeout)
    target_path.write_text(updated, encoding="utf-8")
    print(f"  Back-patched: {target_path.name}")
    return True
