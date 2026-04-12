#!/usr/bin/env python3
"""
ingest.py — Ingest any source file into the brain-wiki.

Usage:
    python scripts/ingest.py <source_file>

Supported types (auto-detected by extension):
    .md .txt .html     → ingest_markdown
    .pdf               → ingest_pdf
    .jpg .jpeg .png .webp .gif → ingest_image
    .transcript .srt .vtt      → ingest_transcript

Flow:
    1. Detect source type and extract text
    2. Classify topic (local model)
    3. Summarize and generate wiki page (local model)
    4. Show summary to user for approval
    5. Write wiki page to wiki/<topic_folder>/<slug>.md
    6. Update / create topic _overview.md (local model)
    7. Update Memory.md and log.md
    8. Back-patch related pages in the same topic
"""

import argparse
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

# Add scripts dir to path so sibling imports work
sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from llm import call_local
from wiki_index import (
    append_log, backpatch_file, get_topic_entries,
    insert_entry, load_memory, slugify,
)

# ── Prompts ───────────────────────────────────────────────────────────────────

CLASSIFY_SYSTEM = """\
You are a precise topic classifier. Given source content and the current Memory.md index, decide:
- Which existing topic group best fits (or propose a short new one, Title Case, 2–4 words)
- A one-line description of this source (max 12 words)
- A 2–5 word kebab-case filename slug

Return ONLY valid JSON, no fences:
{
  "topic": "Topic Name",
  "is_new_topic": true or false,
  "description": "One-line description",
  "slug": "kebab-case-slug"
}
"""

WIKI_PAGE_SYSTEM = """\
You are building a personal knowledge wiki. Given source content, write a structured wiki page.

Return ONLY markdown — no fences, no preamble.

Use this structure:

# <Title>

**Source:** <filename or URL>
**Date ingested:** <today>
**Type:** <Article | PDF | Image | Transcript | Note | Chat>

---

## Summary
<3–5 sentence synthesis of the key ideas>

## Key Points
- <point 1>
- <point 2>

## Concepts & Entities
<Notable people, tools, frameworks, ideas mentioned — one line each>

## Quotes / Highlights
<1–3 notable direct quotes or data points worth preserving. Omit if none.>

## Connections
<How this source relates to things you likely already know — written speculatively if wiki context is provided>

## Related Pages
<Leave blank — will be filled by back-patching>

---
*Ingested by brain-wiki*
"""

WIKI_PAGE_WITH_RELATED_SYSTEM = """\
You are building a personal knowledge wiki. Given source content and related existing pages, write a structured wiki page.

Return ONLY markdown — no fences, no preamble.

Use this structure:

# <Title>

**Source:** <filename or URL>
**Date ingested:** <today>
**Type:** <Article | PDF | Image | Transcript | Note | Chat>

---

## Summary
<3–5 sentence synthesis of the key ideas>

## Key Points
- <point 1>
- <point 2>

## Concepts & Entities
<Notable people, tools, frameworks, ideas mentioned — one line each>

## Quotes / Highlights
<1–3 notable direct quotes or data points worth preserving. Omit if none.>

## Connections
<How this source relates to things you likely already know — written speculatively if wiki context is provided>

## Related Pages
<Use the related pages list to write markdown links. For each:
- [page-slug](relative/path.md) — one sentence on the connection
Order chronologically if dates are available.>

---
*Ingested by brain-wiki*
"""

OVERVIEW_SYSTEM = """\
You are maintaining a topic overview page in a personal knowledge wiki.
You will receive the current _overview.md content and a new page that was just added to this topic.
Update the overview to reflect the new addition — revise the synthesis, add to the page list, note any new contradictions or connections.
Return the COMPLETE updated _overview.md — no fences, no preamble.
"""

OVERVIEW_INIT_SYSTEM = """\
You are creating a new topic overview page for a personal knowledge wiki.
You will receive the first wiki page added to this topic.
Write a concise _overview.md that introduces the topic and lists this first page.
Return ONLY markdown — no fences, no preamble.

Structure:
# <Topic Name>

## What this topic covers
<2–3 sentence description of the topic and why it exists in the wiki>

## Pages
- [slug](filename.md) — one-line description

## Evolving Thesis
<Running synthesis — update this as more pages are added. Leave a stub for now.>

---
*Managed by brain-wiki*
"""


# ── Source readers ─────────────────────────────────────────────────────────────

def read_source(source_path: Path) -> tuple[str, str]:
    """Returns (content_text, source_type)."""
    ext = source_path.suffix.lower()

    if ext in {".md", ".txt", ".html"}:
        return source_path.read_text(encoding="utf-8", errors="replace"), "Note" if ext == ".txt" else "Article"

    if ext == ".pdf":
        return _read_pdf(source_path), "PDF"

    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return _read_image(source_path), "Image"

    if ext in {".transcript", ".srt", ".vtt"}:
        return _clean_transcript(source_path.read_text(encoding="utf-8", errors="replace")), "Transcript"

    # Fallback — try reading as text
    try:
        return source_path.read_text(encoding="utf-8", errors="replace"), "Note"
    except Exception as e:
        print(f"Error reading {source_path}: {e}", file=sys.stderr)
        sys.exit(1)


def _read_pdf(path: Path) -> str:
    try:
        import pymupdf  # type: ignore
        doc = pymupdf.open(str(path))
        return "\n\n".join(page.get_text() for page in doc)
    except ImportError:
        print(
            "pymupdf not installed. Install it with:\n  pip install pymupdf",
            file=sys.stderr,
        )
        sys.exit(1)


def _read_image(path: Path) -> str:
    """Send image to gemma4:31b vision via Ollama and return description."""
    import base64
    import json as _json
    import urllib.request as _req

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}
    mime = "image/" + ext_map.get(path.suffix.lower(), "jpeg")

    payload = {
        "model": "gemma4:31b",
        "prompt": (
            "Describe this image in detail. Extract any text visible in the image. "
            "Note key concepts, entities, data, or information present."
        ),
        "images": [b64],
        "stream": False,
    }
    data = _json.dumps(payload).encode()
    request = _req.Request(
        "http://localhost:11434/api/generate", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with _req.urlopen(request, timeout=120) as resp:
        return _json.loads(resp.read())["response"].strip()


def _clean_transcript(text: str) -> str:
    """Strip SRT/VTT timestamps, leaving just the spoken text."""
    # Remove SRT timestamps: 00:00:01,000 --> 00:00:03,000
    text = re.sub(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}", "", text)
    # Remove VTT timestamps
    text = re.sub(r"\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}\.\d{3}", "", text)
    # Remove sequence numbers
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    # Remove WEBVTT header
    text = re.sub(r"^WEBVTT.*$", "", text, flags=re.MULTILINE)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Classify ──────────────────────────────────────────────────────────────────

def classify(content: str, memory_text: str, source_name: str) -> dict:
    prompt = (
        f"Source filename: {source_name}\n\n"
        f"Source content (first 3000 chars):\n{content[:3000]}\n\n"
        f"Current Memory.md:\n{memory_text}"
    )
    raw = call_local(prompt, CLASSIFY_SYSTEM, timeout=120)
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
            "description": source_name,
            "slug": re.sub(r"[^a-z0-9]+", "-", source_name.lower())[:40],
        }


# ── Write wiki page ───────────────────────────────────────────────────────────

def write_wiki_page(
    content: str,
    source_type: str,
    source_name: str,
    related_entries: list[dict],
    wiki_page_path: Path,
) -> str:
    today = date.today().isoformat()

    if related_entries:
        related_block = "\n".join(
            f"  {e['path']}|{e['description']}" for e in related_entries
        )
        prompt = (
            f"Source type: {source_type}\n"
            f"Source name: {source_name}\n"
            f"Today: {today}\n\n"
            f"Related existing pages (path|description):\n{related_block}\n\n"
            f"Source content:\n{content[:8000]}"
        )
        system = WIKI_PAGE_WITH_RELATED_SYSTEM
    else:
        prompt = (
            f"Source type: {source_type}\n"
            f"Source name: {source_name}\n"
            f"Today: {today}\n\n"
            f"Source content:\n{content[:8000]}"
        )
        system = WIKI_PAGE_SYSTEM

    return call_local(prompt, system, timeout=300)


# ── Overview ──────────────────────────────────────────────────────────────────

def update_overview(overview_path: Path, new_page_content: str, topic: str):
    if overview_path.exists():
        current = overview_path.read_text(encoding="utf-8")
        prompt = (
            f"Current _overview.md:\n\n{current}\n\n"
            f"New page just added to topic '{topic}':\n\n{new_page_content[:3000]}"
        )
        updated = call_local(prompt, OVERVIEW_SYSTEM, timeout=180)
    else:
        prompt = (
            f"Topic name: {topic}\n\n"
            f"First page content:\n\n{new_page_content[:3000]}"
        )
        updated = call_local(prompt, OVERVIEW_INIT_SYSTEM, timeout=180)
    overview_path.write_text(updated, encoding="utf-8")
    print(f"  _overview.md updated")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest a source into brain-wiki")
    parser.add_argument("source", help="Path to source file")
    args = parser.parse_args()

    cfg.ensure_dirs()
    today = date.today().isoformat()
    source_path = Path(args.source).resolve()

    if not source_path.exists():
        print(f"Error: source file not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n📥 Ingesting: {source_path.name}")

    # 1. Read source
    print("  Reading source...")
    content, source_type = read_source(source_path)

    # 2. Load memory and classify
    memory_text = load_memory(cfg.memory_md)
    print("  Classifying topic...")
    classification = classify(content, memory_text, source_path.name)

    topic        = classification.get("topic", "Uncategorized")
    description  = classification.get("description", source_path.stem)
    is_new       = classification.get("is_new_topic", False)
    slug         = classification.get("slug", re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower())[:40])
    topic_folder = slugify(topic)

    print(f"  → Topic:  {topic!r} ({'new' if is_new else 'existing'})")
    print(f"  → Folder: wiki/{topic_folder}/")

    # 3. Get existing entries for cross-referencing
    existing_entries = get_topic_entries(memory_text, topic)

    # 4. Generate wiki page
    print("  Generating wiki page (local model)...")
    wiki_page_content = write_wiki_page(
        content, source_type, source_path.name, existing_entries, None
    )

    # 5. Show summary to user for approval
    print("\n" + "─" * 60)
    print("📄 WIKI PAGE PREVIEW")
    print("─" * 60)
    # Show first ~40 lines as preview
    preview_lines = wiki_page_content.splitlines()[:40]
    print("\n".join(preview_lines))
    if len(wiki_page_content.splitlines()) > 40:
        print(f"\n  ... ({len(wiki_page_content.splitlines()) - 40} more lines)")
    print("─" * 60)
    print(f"\nTopic:   {topic}")
    print(f"File:    wiki/{topic_folder}/{slug}-{today}.md")
    print(f"Summary: {description}")

    answer = input("\nLooks good? [Y/n/edit]: ").strip().lower()
    if answer == "n":
        print("Aborted — nothing written.")
        sys.exit(0)
    if answer == "edit":
        print("Open the temp file to edit, then re-run:")
        tmp = Path(f"/tmp/{slug}-draft.md")
        tmp.write_text(wiki_page_content, encoding="utf-8")
        print(f"  {tmp}")
        sys.exit(0)

    # 6. Write wiki page to topic folder
    topic_dir = cfg.wiki_dir / topic_folder
    topic_dir.mkdir(parents=True, exist_ok=True)
    wiki_page_path = topic_dir / f"{slug}-{today}.md"
    wiki_page_path.write_text(wiki_page_content, encoding="utf-8")
    print(f"\n  ✓ Written: {wiki_page_path}")

    # Copy source to raw dir if not already there
    raw_type_map = {
        "Article": "articles", "Note": "notes", "PDF": "pdfs",
        "Image": "images", "Transcript": "transcripts", "Chat": "chats",
    }
    raw_subdir = cfg.raw_dir / raw_type_map.get(source_type, "notes")
    raw_dest = raw_subdir / source_path.name
    if source_path.resolve() != raw_dest.resolve() and not raw_dest.exists():
        shutil.copy2(source_path, raw_dest)
        print(f"  ✓ Source copied to raw/{raw_type_map.get(source_type, 'notes')}/")

    # 7. Update _overview.md
    overview_path = topic_dir / "_overview.md"
    update_overview(overview_path, wiki_page_content, topic)

    # 8. Update Memory.md
    rel_from_memory = wiki_page_path.relative_to(cfg.vault_root)
    memory_entry = f"- [{slug}-{today}]({rel_from_memory}) — {description}"
    updated_memory = insert_entry(memory_text, topic, memory_entry, today)
    cfg.memory_md.write_text(updated_memory, encoding="utf-8")
    print(f"  ✓ Memory.md updated")

    # 9. Update log.md
    append_log(cfg.log_md, "ingest", f"{source_path.name} → wiki/{topic_folder}/{slug}-{today}.md")

    # 10. Back-patch existing pages ↔ new page
    if existing_entries:
        print(f"\n  Cross-referencing {len(existing_entries)} existing page(s)...")
        for ex in existing_entries:
            ex_path = (cfg.vault_root / ex["path"]).resolve()
            try:
                rel = wiki_page_path.relative_to(ex_path.parent)
            except ValueError:
                rel = wiki_page_path
            entry_for_old = f"- [{slug}-{today}]({rel}) — {description}"
            backpatch_file(ex_path, entry_for_old, call_local)

        for ex in existing_entries:
            ex_path = (cfg.vault_root / ex["path"]).resolve()
            try:
                rel = ex_path.relative_to(wiki_page_path.parent)
            except ValueError:
                rel = ex_path
            entry_for_new = f"- [{Path(ex['path']).stem}]({rel}) — {ex['description']}"
            backpatch_file(wiki_page_path, entry_for_new, call_local)

    print(f"\n✅ Done — {topic} / {slug}-{today}.md")


if __name__ == "__main__":
    main()
