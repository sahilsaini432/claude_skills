#!/usr/bin/env python3
"""
update_memory.py — Classify a new summary into Memory.md, back-patch related files,
and cross-reference within the topic folder.

Folder structure enforced:
    $SUMMARY_OUTPUT_DIR/
    ├── Memory.md
    ├── claude-code-and-skills/
    │   ├── skill-builder-workflow-2026-04-10.md
    │   └── chat-summarizer-evolved-2026-04-09.md
    └── python-and-data-science/
        └── ppo-frozenlake-report-2026-04-01.md

Usage:
    # Step A — pre-run: classify topic and list existing related sessions (no writes)
    python scripts/update_memory.py <stub-title-or-path> --pre-run [--memory Memory.md]

    # Step B — commit: insert into Memory.md, move file to topic folder, back-patch
    python scripts/update_memory.py <summary-file-path> [--memory Memory.md]

--pre-run output (JSON to stdout):
    {
      "topic": "Claude Code & Skills",
      "topic_folder": "claude-code-and-skills",
      "entries": [
        {"slug": "...", "path": "claude-code-and-skills/file.md", "description": "..."}
      ]
    }
"""

import argparse
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
import os

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if _line.startswith("OLLAMA_URL="):
            os.environ.setdefault("OLLAMA_URL", _line.split("=", 1)[1].strip())

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = "gemma4:31b"

MEMORY_TEMPLATE = """\
# Memory

> Auto-maintained index of all chat sessions, grouped by topic.

---

---
*Last updated: {date}*
"""

CLASSIFY_SYSTEM = """\
You are a precise topic classifier. Given:
1. The content of a chat summary (or stub title)
2. The current Memory.md index (existing topic groups)

Decide which existing topic group best fits, or propose a new one.
Also write a one-line description (max 12 words) of what this chat covered.

Return ONLY valid JSON, no markdown fences:
{
  "topic": "Exact Existing Group Name or New Group Name",
  "is_new_topic": true or false,
  "description": "One-line description of this session"
}
"""

BACKPATCH_SYSTEM = """\
You are editing a markdown chat-summary file.
You will receive:
1. The current content of a summary file
2. A new session entry to add to its "## Related Sessions" section

Rules:
- If "## Related Sessions" already exists, append the new entry to that list (no duplicates by slug)
- If it does not exist, insert the section just before "## Action Items / Next Steps",
  or before the final "---" line if that section is absent
- Return the COMPLETE updated file content — no truncation, no fences, no commentary
"""


def slugify(topic: str) -> str:
    t = topic.lower().replace("&", "and")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def call_ollama(prompt: str, system: str, timeout: int = 120) -> str:
    payload = {"model": MODEL, "prompt": prompt, "system": system, "stream": False}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["response"].strip()
    except urllib.error.URLError as e:
        print(
            f"\nError: Could not reach Ollama at {OLLAMA_URL}\n"
            f"Make sure Ollama is running:  ollama serve\n"
            f"And the model is pulled:      ollama pull {MODEL}\n"
            f"Details: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


def parse_json(raw: str) -> dict:
    raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {
            "topic": "Uncategorized",
            "is_new_topic": True,
            "description": "Session description unavailable",
        }


def classify(summary_text: str, memory_text: str) -> dict:
    prompt = f"New session content:\n\n{summary_text}\n\nCurrent Memory.md:\n\n{memory_text}"
    return parse_json(call_ollama(prompt, CLASSIFY_SYSTEM))


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
                entries.append({"slug": m.group(1), "path": m.group(2), "description": m.group(3).strip()})
    return entries


def make_memory_entry(slug: str, rel_path: str, description: str) -> str:
    return f"- [{slug}]({rel_path}) — {description}"


def insert_entry_in_memory(memory_text: str, topic: str, entry: str, today: str) -> str:
    heading = f"## {topic}"
    lines = memory_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == heading:
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, entry)
            return _update_footer("\n".join(lines), today)
    # New topic — insert before last ---
    new_block = [f"\n{heading}", entry]
    footer_idx = next(
        (i for i in range(len(lines) - 1, len(lines) // 2, -1) if lines[i].strip() == "---"),
        None,
    )
    if footer_idx is not None:
        for j, sec_line in enumerate(new_block):
            lines.insert(footer_idx + j, sec_line)
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


def backpatch_file(target_path: Path, new_entry_line: str) -> None:
    if not target_path.exists():
        print(f"  Skipping backpatch (not found): {target_path}", file=sys.stderr)
        return
    current = target_path.read_text(encoding="utf-8")
    slug_match = re.search(r"\[([^\]]+)\]", new_entry_line)
    if slug_match and slug_match.group(1) in current:
        return  # already linked
    prompt = f"Current file content:\n\n{current}\n\n" f"New related session entry to add:\n{new_entry_line}"
    updated = call_ollama(prompt, BACKPATCH_SYSTEM, timeout=180)
    target_path.write_text(updated, encoding="utf-8")
    print(f"  Back-patched: {target_path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_file", help="Path to the summary .md file (or stub title with --pre-run)")
    parser.add_argument("--memory", default="Memory.md", help="Path to Memory.md")
    parser.add_argument(
        "--pre-run",
        action="store_true",
        help="Classify only — print JSON with topic + related entries, no writes",
    )
    args = parser.parse_args()

    memory_path = Path(args.memory).resolve()
    today = date.today().isoformat()

    memory_text = (
        memory_path.read_text(encoding="utf-8")
        if memory_path.exists()
        else MEMORY_TEMPLATE.format(date=today)
    )

    summary_path = Path(args.summary_file).resolve()

    # ── PRE-RUN ───────────────────────────────────────────────────────────────
    if args.pre_run:
        stub = summary_path.stem if not summary_path.exists() else summary_path.read_text(encoding="utf-8")
        classification = classify(stub, memory_text)
        topic = classification.get("topic", "Uncategorized")
        topic_folder = slugify(topic)
        entries = get_topic_entries(memory_text, topic)
        print(json.dumps({"topic": topic, "topic_folder": topic_folder, "entries": entries}))
        return

    # ── COMMIT ────────────────────────────────────────────────────────────────
    if not summary_path.exists():
        print(f"Error: summary file not found: {summary_path}", file=sys.stderr)
        sys.exit(1)

    summary_text = summary_path.read_text(encoding="utf-8")

    print(f"Classifying session topic using {MODEL}...")
    classification = classify(summary_text, memory_text)
    topic = classification.get("topic", "Uncategorized")
    description = classification.get("description", "No description")
    is_new = classification.get("is_new_topic", False)
    topic_folder = slugify(topic)

    print(f"  → Topic:  {topic!r} ({'new' if is_new else 'existing'})")
    print(f"  → Folder: {topic_folder}/")
    print(f"  → Desc:   {description}")

    # Move the summary file into its topic subfolder (sibling of Memory.md)
    topic_dir = memory_path.parent / topic_folder
    topic_dir.mkdir(parents=True, exist_ok=True)
    final_path = topic_dir / summary_path.name
    if summary_path.resolve() != final_path.resolve():
        shutil.move(str(summary_path), final_path)
        print(f"  → Moved to: {final_path}")
    summary_path = final_path

    # Relative path from Memory.md's directory
    rel_from_memory = summary_path.relative_to(memory_path.parent)

    # Gather existing entries before inserting the new one
    existing_entries = get_topic_entries(memory_text, topic)

    # Insert into Memory.md
    new_memory_entry = make_memory_entry(summary_path.stem, str(rel_from_memory), description)
    updated_memory = insert_entry_in_memory(memory_text, topic, new_memory_entry, today)
    if not memory_path.exists():
        memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(updated_memory, encoding="utf-8")
    print(f"  → Memory.md updated")

    # Back-patch existing files → add new session to their Related Sessions
    if existing_entries:
        print(f"  Back-patching {len(existing_entries)} existing file(s) in '{topic_folder}/'...")
        for ex in existing_entries:
            ex_path = (memory_path.parent / ex["path"]).resolve()
            # Relative path from the existing file's dir to the new file
            try:
                rel = summary_path.relative_to(ex_path.parent)
            except ValueError:
                rel = summary_path
            entry_for_old = f"- [{summary_path.stem}]({rel}) — {description}"
            backpatch_file(ex_path, entry_for_old)

        # Add back-references in the new file pointing to all existing ones
        print(f"  Adding {len(existing_entries)} back-reference(s) to new file...")
        for ex in existing_entries:
            ex_path = (memory_path.parent / ex["path"]).resolve()
            try:
                rel = ex_path.relative_to(summary_path.parent)
            except ValueError:
                rel = ex_path
            entry_for_new = f"- [{Path(ex['path']).stem}]({rel}) — {ex['description']}"
            backpatch_file(summary_path, entry_for_new)
    else:
        print("  No existing sessions in this topic to cross-reference.")

    # Print final path for the caller
    print(f"FINAL_PATH={summary_path}")


if __name__ == "__main__":
    main()
