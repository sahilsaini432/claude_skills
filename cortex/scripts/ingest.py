#!/usr/bin/env python3
"""
ingest.py — Ingest any source file into the cortex.

This is a two-phase tool driven entirely by Claude Code (no local LLM).

Usage:
    python scripts/ingest.py <source_file>          # phase 1
    python scripts/ingest.py <source_file> --yes \\  # phase 2
        --page-content-file <path> --entities-file <path> \\
        --topic '...' --slug '...' --description '...'

    python scripts/ingest.py --raw-chats-path       # print raw/chats/ path

Supported types (auto-detected by extension):
    .md .txt .html             → Article / Note / Chat
    .pdf                       → PDF (requires pymupdf)
    .jpg .jpeg .png .webp .gif → Image (Claude Code reads via its Read tool)
    .transcript .srt .vtt      → Transcript

Phase 1:
    Reads the source, prints a structured "PHASE 1" block (source content,
    Memory.md excerpt, an existing-page hint if a slug collides), and exits
    with code 2. Claude Code reads the block, classifies the source, writes
    the wiki page + entities to temp files, and re-runs in phase 2.

Phase 2:
    Takes the synthesized page (--page-content-file), entities
    (--entities-file), and classification (--topic / --slug / --description).
    Writes the wiki page, archives the source, updates Memory.md, the topic
    _overview.md, log.md, the entity registry, and links everything together.
"""

import argparse
import json
import re
import shutil
import sys
import sys, io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from wiki_index import (
    append_log,
    get_topic_entries_local,
    insert_topic_entry,
    ensure_master_has_topic,
    load_memory,
    slugify,
    posix_rel,
)
from entities import (
    process_entities,
    link_entity_pages_to_source,
    link_source_to_entity_pages,
)


# ── Source readers ─────────────────────────────────────────────────────────────


def read_source(source_path: Path) -> tuple[str, str]:
    """Returns (content_text, source_type).

    For images, returns a placeholder string — Claude Code reads the image
    file directly via its Read tool in phase 1.
    """
    ext = source_path.suffix.lower()

    if ext in {".md", ".txt", ".html"}:
        text = source_path.read_text(encoding="utf-8", errors="replace")
        if ext in {".txt", ".md"}:
            sample = text[:2000]
            if (
                ("USER:" in sample and "ASSISTANT:" in sample)
                or ("**User**" in sample and "**Assistant**" in sample)
                or ("**Human**" in sample and "**Claude**" in sample)
            ):
                return text, "Chat"
        return text, "Note" if ext == ".txt" else "Article"

    if ext == ".pdf":
        return _read_pdf(source_path), "PDF"

    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return (
            f"[Image file at {source_path} — Claude Code: read this file with your Read tool "
            f"to extract its content, then synthesize the wiki page.]",
            "Image",
        )

    if ext in {".transcript", ".srt", ".vtt"}:
        return _clean_transcript(source_path.read_text(encoding="utf-8", errors="replace")), "Transcript"

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
        print("pymupdf not installed. Run:  pip install pymupdf", file=sys.stderr)
        sys.exit(1)


def _clean_transcript(text: str) -> str:
    text = re.sub(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}", "", text)
    text = re.sub(r"\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}\.\d{3}", "", text)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^WEBVTT.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_url(url: str) -> str:
    """Fetch URL and return raw HTML string."""
    import urllib.request as _ur

    req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _ur.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _strip_html(html: str) -> str:
    """Strip HTML tags; return clean readable text."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self._skip = False
            self.parts: list[str] = []

        def handle_starttag(self, tag, _attrs):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    s = _Stripper()
    s.feed(html)
    return re.sub(r"\s+", " ", " ".join(s.parts)).strip()


def _url_to_filename(url: str, today: str) -> str:
    """Derive a filesystem-safe archive filename from a URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "").replace(".", "-")
    path_slug = re.sub(r"[^a-z0-9]+", "-", parsed.path.lower()).strip("-") or "index"
    name = f"{domain}-{path_slug}-{today}"
    return name[:120] + ".html"


# ── Duplicate detection ───────────────────────────────────────────────────────


def find_existing_page(topic_dir: Path, slug: str) -> Path | None:
    """Return the first existing page whose filename starts with slug-, or None."""
    if not topic_dir.exists():
        return None
    for f in topic_dir.glob(f"{slug}-*.md"):
        if f.name != "_overview.md":
            return f
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _add_overview_link(page_path: Path):
    """Prepend [_overview](_overview.md) to Related Pages if not already present.

    Creates a parent-link from source page → topic overview, forming hub-and-spoke
    topology per topic so Obsidian's graph shows distinct clusters.
    """
    content = page_path.read_text(encoding="utf-8")
    if "_overview" in content or "## Related Pages" not in content:
        return
    new_content = content.replace(
        "## Related Pages\n",
        "## Related Pages\n- [_overview](_overview.md) — topic index\n",
        1,
    )
    if new_content != content:
        page_path.write_text(new_content, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Ingest a source into cortex")
    parser.add_argument("source", nargs="?", help="Path to source file")
    parser.add_argument("--raw-chats-path", action="store_true", help="Print the raw/chats/ path and exit")
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt and auto-approve (for Claude Code)"
    )
    parser.add_argument(
        "--page-content-file",
        help="Path to a file containing the wiki page markdown (written by Claude Code in phase 2)",
    )
    parser.add_argument(
        "--entities-file",
        help="Path to a JSON file with extracted entities [{name,slug,description,type},...] (phase 2)",
    )
    parser.add_argument("--topic", help="Topic name (required in phase 2)")
    parser.add_argument("--slug", help="Page slug (required in phase 2)")
    parser.add_argument("--description", help="One-line description (required in phase 2)")
    args = parser.parse_args()

    cfg.ensure_dirs()

    if args.raw_chats_path:
        print(cfg.raw_dir / "chats")
        sys.exit(0)

    if not args.source:
        parser.print_help()
        sys.exit(1)

    today = date.today().isoformat()
    is_url = str(args.source or "").startswith(("http://", "https://"))

    if is_url:
        url = args.source
        print(f"\n[ingest] Fetching URL: {url}", file=sys.stderr)
        _raw_html = _fetch_url(url)
        _prefetched_content = _strip_html(_raw_html)
        _prefetched_type = "Article"
        _url_fname = _url_to_filename(url, today)
        _url_raw_dest = cfg.raw_dir / "articles" / _url_fname
        _url_raw_dest.parent.mkdir(parents=True, exist_ok=True)
        _url_raw_dest.write_text(_raw_html, encoding="utf-8")
        print(f"  [ok] Archived to raw/articles/{_url_fname}", file=sys.stderr)
        source_path = _url_raw_dest
        source_name = url
    else:
        source_path = Path(args.source).resolve()
        if not source_path.exists():
            print(f"Error: source file not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        source_name = source_path.name
        _prefetched_content = None
        _prefetched_type = None

    # ── PHASE 1 ───────────────────────────────────────────────────────────────
    # No LLM. Read the source, print a structured synthesis prompt for Claude
    # Code, and exit 2 so Claude Code knows to proceed to phase 2.
    if not args.page_content_file:
        print(f"\n[ingest] Phase 1 — reading source: {source_name}")
        if _prefetched_content is not None:
            content, source_type = _prefetched_content, _prefetched_type
        else:
            content, source_type = read_source(source_path)
        memory_text = load_memory(cfg.memory_md)

        # Heuristic slug from filename (Claude Code may override)
        auto_slug = re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower())[:40]
        auto_slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", auto_slug) or auto_slug

        # Check if a page with this slug already exists (merge hint)
        existing_page_hint = None
        for topic_dir in cfg.wiki_dir.iterdir():
            if topic_dir.is_dir():
                hit = find_existing_page(topic_dir, auto_slug)
                if hit:
                    existing_page_hint = str(hit.relative_to(cfg.vault_root))
                    break

        print("\n" + "=" * 70)
        print("cortex PHASE 1")
        print("=" * 70)
        print(f"SOURCE_NAME: {source_name}")
        print(f"SOURCE_TYPE: {source_type}")
        print(f"SOURCE_PATH: {source_path}")
        print(f"TODAY: {today}")
        print(f"AUTO_SLUG: {auto_slug}")
        print(f"EXISTING_PAGE: {existing_page_hint or 'none'}")
        print("MEMORY_MD_EXCERPT:")
        print(memory_text[:3000])
        print("SOURCE_CONTENT:")
        print(content[:12000])
        print("=" * 70)
        print(
            "\nINSTRUCTIONS FOR CLAUDE CODE:\n"
            "1. Read SOURCE_CONTENT above. If SOURCE_TYPE is Image, use your Read\n"
            "   tool on SOURCE_PATH to view the image directly.\n"
            "2. Classify: pick or propose a topic from MEMORY_MD_EXCERPT, write a\n"
            "   one-line description (<=12 words), and confirm or revise AUTO_SLUG.\n"
            "3. If EXISTING_PAGE is set, merge new content into that page.\n"
            "   Otherwise write a fresh wiki page using this schema:\n"
            "     # Title\n"
            "     **Source:** ... | **Date ingested:** ... | **Type:** ...\n"
            "     ---\n"
            "     ## Summary                  ← 1–2 paragraphs; readable cold by someone new to the topic\n"
            "     ## Background / Context     ← OPTIONAL — prerequisites/jargon. Omit if topic is common knowledge.\n"
            "     ## Key Points\n"
            "     ## Detailed Notes           ← OPTIONAL — preserve source structure verbatim:\n"
            "                                   tables → markdown tables, code → fenced blocks with language,\n"
            "                                   numbered steps/tutorials → numbered lists in order,\n"
            "                                   diagrams/charts → short text description.\n"
            "                                   Omit entirely if source has no structured content.\n"
            "     ## Concepts & Entities\n"
            "     ## Quotes / Highlights\n"
            "     ## Connections\n"
            "     ## Related Pages\n"
            "     - [_overview](_overview.md) — topic index\n"
            "     ---\n"
            "     *Ingested by cortex*\n"
            "   Goal: the page should stand alone as an explainer — a reader unfamiliar with\n"
            "   the topic should be able to pick it up cold and understand without re-reading\n"
            "   the source.\n"
            "4. Extract 3-8 significant entities (tools, frameworks, people, concepts).\n"
            "5. Write the wiki page markdown to a temp file.\n"
            "6. Write the entities as JSON to a temp file:\n"
            '   [{"name": ..., "slug": ..., "description": ..., "type": ...}, ...]\n'
            "7. Re-run ingest.py with:\n"
            "     python3 ingest.py SOURCE_PATH --yes \\\n"
            "       --page-content-file <wiki_page_tmp> \\\n"
            "       --entities-file <entities_tmp> \\\n"
            "       --topic 'Topic Name' \\\n"
            "       --slug 'your-slug' \\\n"
            "       --description 'one-line description'\n"
        )
        print("=" * 70)
        sys.exit(2)  # Sentinel: Claude Code must proceed to phase 2

    # ── PHASE 2 ───────────────────────────────────────────────────────────────
    if not args.topic or not args.slug or not args.description:
        print(
            "Error: phase 2 requires --topic, --slug, and --description",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n[ingest] Phase 2 — finalizing: {source_name}")

    # Read source again to determine source_type for raw/ archival
    if _prefetched_content is not None:
        content, source_type = _prefetched_content, _prefetched_type
    else:
        content, source_type = read_source(source_path)

    topic = args.topic
    slug = args.slug
    description = args.description
    topic_folder = slugify(topic)
    topic_dir = cfg.wiki_dir / topic_folder

    print(f"  → Topic: {topic!r}")
    print(f"  → Slug:  {slug}")

    # Read the wiki page content from the file Claude Code wrote
    page_content_path = Path(args.page_content_file).resolve()
    if not page_content_path.exists():
        print(f"Error: --page-content-file not found: {page_content_path}", file=sys.stderr)
        sys.exit(1)
    wiki_page_content = page_content_path.read_text(encoding="utf-8").strip()

    # Determine if this is a merge (existing page with same slug)
    existing_page = find_existing_page(topic_dir, slug)
    is_merge = existing_page is not None
    if is_merge:
        print(f"  → Existing page found: {existing_page.name} — treating as merge (Claude Code pre-merged)")
        wiki_page_path = existing_page
    else:
        print(f"  → New page: wiki/{topic_folder}/{slug}-{today}.md")
        wiki_page_path = topic_dir / f"{slug}-{today}.md"

    existing_entries = get_topic_entries_local(topic_dir, cfg.vault_root)
    if is_merge:
        existing_entries = [
            e for e in existing_entries if not Path(e["path"]).name.startswith(slug + "-")
        ]

    # Preview
    print("\n" + "─" * 60)
    print(f"[preview] WIKI PAGE {'MERGE' if is_merge else 'PREVIEW'}")
    print("─" * 60)
    preview_lines = wiki_page_content.splitlines()[:40]
    print("\n".join(preview_lines))
    if len(wiki_page_content.splitlines()) > 40:
        print(f"\n  ... ({len(wiki_page_content.splitlines()) - 40} more lines)")
    print("─" * 60)
    print(f"\nTopic:   {topic}")
    print(f"File:    {wiki_page_path.relative_to(cfg.vault_root)}")
    print(f"Mode:    {'merge into existing' if is_merge else 'create new'}")
    print(f"Summary: {description}")

    if args.yes:
        print("\n[auto-approved via --yes]")
    else:
        answer = input("\nLooks good? [Y/n]: ").strip().lower()
        if answer == "n":
            print("Aborted -- nothing written.")
            sys.exit(0)

    # Write page
    topic_dir.mkdir(parents=True, exist_ok=True)
    wiki_page_path.write_text(wiki_page_content, encoding="utf-8")
    print(f"\n  [ok] {'Merged' if is_merge else 'Written'}: {wiki_page_path.name}")

    # Add parent link to _overview.md in Related Pages (hub-and-spoke topology)
    if not is_merge:
        _add_overview_link(wiki_page_path)

    # Copy source to raw/ if not already there
    raw_type_map = {
        "Article": "articles",
        "Note": "notes",
        "PDF": "pdfs",
        "Image": "images",
        "Transcript": "transcripts",
        "Chat": "chats",
    }
    raw_subtype = raw_type_map.get(source_type, "notes")
    try:
        source_path.relative_to(cfg.raw_dir)
        already_in_raw = True
    except ValueError:
        already_in_raw = False

    if not already_in_raw:
        raw_dest = cfg.raw_dir / raw_subtype / source_path.name
        if raw_dest.exists():
            raw_dest = cfg.raw_dir / raw_subtype / f"{source_path.stem}-{today}{source_path.suffix}"
        shutil.copy2(source_path, raw_dest)
        print(f"  [ok] Source copied to raw/{raw_subtype}/")
    else:
        print("  [ok] Source already in raw/ — no copy needed")

    # Update _overview.md (append-only — no LLM)
    overview_path = topic_dir / "_overview.md"
    rel_page = posix_rel(wiki_page_path.relative_to(cfg.vault_root))
    if not overview_path.exists():
        overview_path.write_text(
            f"# {topic}\n\n"
            f"## What this topic covers\n"
            f"<!-- stub — update manually or run lint --fix -->\n\n"
            f"## Pages\n"
            f"- [{slug}]({rel_page}) — {description}\n\n"
            f"## Evolving Thesis\n"
            f"<!-- stub -->\n\n"
            f"---\n*Managed by cortex*\n",
            encoding="utf-8",
        )
        print("  [ok] _overview.md created (stub)")
    else:
        current = overview_path.read_text(encoding="utf-8")
        entry = f"- [{slug}]({rel_page}) — {description}"
        if slug not in current:
            if "## Pages" in current:
                current = current.replace(
                    "## Pages\n",
                    f"## Pages\n{entry}\n",
                    1,
                )
            else:
                current += f"\n## Pages\n{entry}\n"
            overview_path.write_text(current, encoding="utf-8")
        print("  [ok] _overview.md updated (append-only)")

    # Update per-topic Memory.md and ensure master has a link to it
    if not is_merge:
        local_path = posix_rel(wiki_page_path.relative_to(topic_dir))
        topic_entry = f"- [{slug}]({local_path}) — {description}"
        insert_topic_entry(topic_dir, topic_entry, today)
        topic_mem_rel = posix_rel((topic_dir / "Memory.md").relative_to(cfg.vault_root))
        ensure_master_has_topic(cfg.memory_md, topic, topic_mem_rel, today)
        print("  [ok] Topic Memory.md updated")
    else:
        print("  [ok] Topic Memory.md unchanged (merge)")

    # Update log.md
    action = "merge" if is_merge else "ingest"
    append_log(cfg.log_md, action, f"{source_name} → {wiki_page_path.name}")

    # Cross-references (deterministic — append to Related Pages, no LLM)
    if not is_merge and existing_entries:
        print(f"\n  Cross-referencing {len(existing_entries)} existing page(s)...")
        from wiki_index import backpatch_file

        for ex in existing_entries:
            ex_path = (cfg.vault_root / ex["path"]).resolve()
            try:
                rel = wiki_page_path.relative_to(ex_path.parent)
            except ValueError:
                rel = wiki_page_path
            entry_for_old = f"- [{slug}]({posix_rel(rel)}) — {description}"
            backpatch_file(ex_path, entry_for_old)

        for ex in existing_entries:
            ex_path = (cfg.vault_root / ex["path"]).resolve()
            try:
                rel = ex_path.relative_to(wiki_page_path.parent)
            except ValueError:
                rel = ex_path
            ex_slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", Path(ex["path"]).stem)
            entry_for_new = f"- [{ex_slug}]({posix_rel(rel)}) — {ex['description']}"
            backpatch_file(wiki_page_path, entry_for_new)

    # Entity processing
    entities = []
    if args.entities_file:
        entities_path = Path(args.entities_file).resolve()
        if entities_path.exists():
            try:
                entities = json.loads(entities_path.read_text(encoding="utf-8"))
                print(f"\n  Loaded {len(entities)} entities from --entities-file")
            except Exception as e:
                print(f"  [warn] Could not parse --entities-file: {e}", file=sys.stderr)
        else:
            print(f"  [warn] --entities-file not found: {entities_path}", file=sys.stderr)
    else:
        print("\n  No --entities-file provided — skipping entity tracking")

    if entities:
        print(f"  Found {len(entities)} entities: {', '.join(e['name'] for e in entities)}")
        entity_pages = process_entities(entities, slug, today)
        if entity_pages:
            print(f"  Linking {len(entity_pages)} entity page(s)...")
            link_entity_pages_to_source(wiki_page_path, entity_pages)
            link_source_to_entity_pages(
                wiki_page_path,
                slug,
                description,
                entity_pages,
                topic_overview_path=topic_dir / "_overview.md",
            )
    else:
        print("  No significant entities provided.")

    print(f"\n[done] Done — {topic} / {wiki_page_path.name}")


if __name__ == "__main__":
    main()
