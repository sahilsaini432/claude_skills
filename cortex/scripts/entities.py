#!/usr/bin/env python3
"""
entities.py — Entity registry and page management for cortex.

Tracks how many times each entity/concept has been seen across ingests.
Creates or updates entity pages in wiki/_entities/ on second appearance.

Registry file: <vault_root>/entity_registry.json
Entity pages:  <vault_root>/wiki/_entities/<entity-slug>.md

Registry format:
{
  "sdl2": {
    "name": "SDL2",
    "description": "Cross-platform multimedia library for C/C++",
    "count": 2,
    "sources": ["battleship-sdl2-2026-04-14.md", "audio-engine-2026-04-15.md"]
  },
  ...
}
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from llm import call_local

ENTITY_DIR_NAME = "_entities"

EXTRACT_SYSTEM = """\
You are an entity extractor for a personal knowledge wiki.
Given source content, identify the most significant entities and concepts worth tracking.

Only extract things that are SUBSTANTIVE — tools, frameworks, algorithms, key people,
core concepts, methodologies. Do NOT extract: generic programming terms, common words,
file names, error messages, or anything mentioned only in passing.

Be conservative — 3 to 8 entities per source is typical. Quality over quantity.

Return ONLY valid JSON, no fences:
{
  "entities": [
    {
      "name": "SDL2",
      "slug": "sdl2",
      "description": "Cross-platform multimedia library for C/C++ games",
      "type": "tool"
    },
    {
      "name": "SDL2_mixer",
      "slug": "sdl2-mixer",
      "description": "SDL2 extension library for audio playback",
      "type": "tool"
    }
  ]
}

Types: tool, framework, algorithm, concept, person, library, language, methodology
"""

ENTITY_PAGE_SYSTEM = """\
You are creating an entity reference page for a personal knowledge wiki.
This page will be updated every time a new source mentions this entity.

Return ONLY markdown — no fences, no preamble. Temperature: be factual and consistent.

# <Entity Name>

**Type:** <tool | framework | algorithm | concept | person | library | language | methodology>
**First seen:** <date>
**Times referenced:** <count>

---

## What it is
<2–4 sentence factual description of what this entity is>

## Key Facts
- <fact 1>
- <fact 2>

## How it's been used
<Based on the sources, describe how this entity appears in the context of this wiki>

## Related Pages
<Leave blank — filled by back-patching>

---
*Managed by cortex*
"""

ENTITY_UPDATE_SYSTEM = """\
You are updating an entity reference page in a personal knowledge wiki with new information.

Rules:
1. Update "Times referenced" count
2. Expand "What it is" if the new source adds new factual information
3. Add new bullet points to "Key Facts" — never remove existing ones
4. Update "How it's been used" to reflect the new source context
5. PRESERVE "## Related Pages" EXACTLY as-is
6. Return the COMPLETE updated file — no truncation, no fences, no commentary
Temperature: be factual and consistent with the existing content.
"""

ENTITY_BACKFILL_SYSTEM = """\
You are creating an entity reference page for a personal knowledge wiki.
This entity has appeared in multiple sources — use all provided source excerpts.

Return ONLY markdown — no fences, no preamble. Be factual and consistent.

# <Entity Name>

**Type:** <tool | framework | algorithm | concept | person | library | language | methodology>
**First seen:** <earliest date>
**Times referenced:** <count>

---

## What it is
<2–4 sentence factual description synthesized from all sources>

## Key Facts
- <fact 1 from any source>
- <fact 2 from any source>

## How it's been used
<Synthesize how this entity appears across all provided sources>

## Related Pages
<Leave blank — filled by back-patching>

---
*Managed by cortex*
"""


def _registry_path() -> Path:
    return cfg.vault_root / "entity_registry.json"


def _entity_dir() -> Path:
    d = cfg.wiki_dir / ENTITY_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_registry() -> dict:
    p = _registry_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_registry(registry: dict):
    _registry_path().write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def slugify_entity(name: str) -> str:
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def extract_entities(content: str, source_name: str) -> list[dict]:
    """Ask local LLM to extract entities from source content."""
    prompt = f"Source: {source_name}\n\n" f"Content (first 4000 chars):\n{content[:4000]}"
    raw = call_local(prompt, EXTRACT_SYSTEM, timeout=cfg.timeout_short, label="extract entities")
    raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
    try:
        result = json.loads(raw)
        return result.get("entities", [])
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group()).get("entities", [])
            except Exception:
                pass
    print("  [warn] Could not parse entity extraction response", file=sys.stderr)
    return []


def process_entities(
    entities: list[dict],
    source_content: str,
    source_slug: str,
    source_path: Path,
    today: str,
) -> list[Path]:
    """
    Update entity registry. Create or update entity pages on every appearance.
    Returns list of entity page paths that were created or updated.
    """
    registry = load_registry()
    entity_dir = _entity_dir()
    touched_pages = []

    for entity in entities:
        name = entity.get("name", "").strip()
        slug = entity.get("slug", "").strip() or slugify_entity(name)
        desc = entity.get("description", "").strip()
        etype = entity.get("type", "concept")

        if not name or not slug:
            continue

        # Update registry
        if slug not in registry:
            registry[slug] = {
                "name": name,
                "description": desc,
                "type": etype,
                "count": 1,
                "first_seen": today,
                "sources": [source_slug],
            }
            print(f"  [entity] First seen: {name}")
        else:
            # Avoid duplicate source entries
            if source_slug not in registry[slug]["sources"]:
                registry[slug]["count"] += 1
                registry[slug]["sources"].append(source_slug)
                # Update description if we have a better one
                if desc and len(desc) > len(registry[slug].get("description", "")):
                    registry[slug]["description"] = desc
            count = registry[slug]["count"]
            print(f"  [entity] Seen {count}x: {name}")

        # Always create or update entity page
        entity_page = entity_dir / f"{slug}.md"
        if entity_page.exists():
            _update_entity_page(entity_page, name, source_content, source_slug, today, registry[slug])
        else:
            _create_entity_page(
                entity_page, name, slug, etype, source_content, source_slug, today, registry[slug]
            )
        touched_pages.append(entity_page)

    save_registry(registry)
    return touched_pages


def backfill_missing_entity_pages(today: str, pages_json: str | None = None) -> int:
    """
    Create entity pages for all registry entries that don't have a page yet.

    Phase 1 (no pages_json): prints a CLAUDE-CHAT BACKFILL block and exits with code 2.
    Phase 2 (pages_json set): reads {"slug": "markdown content"} and commits pages to disk.
    Returns count of pages created.
    """
    registry = load_registry()
    entity_dir = _entity_dir()

    # ── Phase 2: commit pages written by Claude Code ─────────────────────────
    if pages_json is not None:
        data = json.loads(Path(pages_json).read_text(encoding="utf-8"))
        created = 0
        for slug, content in data.items():
            page_path = entity_dir / f"{slug}.md"
            if not page_path.exists():
                page_path.write_text(content, encoding="utf-8")
                print(f"  [backfill] Committed: _entities/{slug}.md")
                created += 1
        print(f"  [backfill] Done — {created} entity page(s) created.")
        return created

    # ── Phase 1: collect missing entities, print synthesis prompt, exit 2 ────
    missing = []
    for slug, entry in registry.items():
        if (entity_dir / f"{slug}.md").exists():
            continue
        sources = entry.get("sources", [])
        excerpts = _gather_source_excerpts(sources, "", sources[0] if sources else slug)
        missing.append(
            {
                "slug": slug,
                "name": entry.get("name", slug),
                "type": entry.get("type", "concept"),
                "description": entry.get("description", ""),
                "count": entry.get("count", 1),
                "first_seen": entry.get("first_seen", today),
                "sources": sources,
                "excerpts": excerpts[:1500],
            }
        )

    if not missing:
        print("  [backfill] No missing entity pages found.")
        return 0

    print(f"\n{'=' * 70}")
    print("cortex CLAUDE-CHAT BACKFILL PHASE 1")
    print(f"{'=' * 70}")
    print(f"MISSING_COUNT: {len(missing)}")
    print(f"TODAY: {today}")
    print(f"ENTITY_DIR: {entity_dir}")
    print()
    print("ENTITIES_JSON:")
    print(json.dumps(missing, indent=2))
    print()
    print("SCHEMA (use for every page):")
    print(
        """
# <Entity Name>

**Type:** <type>
**First seen:** <first_seen date>
**Times referenced:** <count>

---

## What it is
<2-4 sentence factual description based on description field and source excerpts>

## Key Facts
- <key fact 1>
- <key fact 2>

## How it's been used
<how this entity appears in the context of the sources listed>

## Related Pages

---
*Managed by cortex*
"""
    )
    print("INSTRUCTIONS FOR CLAUDE CODE:")
    print(f"1. Generate one wiki page per entity using the schema above.")
    print(f'2. Write all pages as a single JSON file: {{"slug": "full page markdown", ...}}')
    print(f"3. Re-run to commit:")
    print(f"   python3 ~/.claude/skills/cortex/scripts/entities.py --backfill --pages-json <path>")
    print(f"{'=' * 70}")
    sys.exit(2)


def _create_entity_page(
    page_path: Path,
    name: str,
    slug: str,
    etype: str,
    source_content: str,
    source_slug: str,
    today: str,
    reg_entry: dict,
):
    """Create a new entity page, back-filling from all known sources."""
    sources = reg_entry.get("sources", [source_slug])
    count = reg_entry.get("count", 1)
    first = reg_entry.get("first_seen", today)

    # Gather content excerpts from source files we can find on disk
    source_excerpts = _gather_source_excerpts(sources, source_content, source_slug)

    prompt = (
        f"Entity name: {name}\n"
        f"Entity type: {etype}\n"
        f"First seen: {first}\n"
        f"Times referenced: {count}\n"
        f"Today: {today}\n\n"
        f"Source excerpts:\n{source_excerpts}"
    )
    content = call_local(prompt, ENTITY_BACKFILL_SYSTEM, timeout=cfg.timeout_long, label="entity page")
    page_path.write_text(content, encoding="utf-8")
    print(f"  [entity] Created entity page: _entities/{slug}.md")


def _extract_key_section(text: str, heading: str, max_chars: int = 500) -> str:
    """Extract a specific section from a markdown page, capped at max_chars."""
    lines = text.splitlines()
    in_section = False
    result = []
    for line in lines:
        if line.strip() == f"## {heading}":
            in_section = True
            result.append(line)
            continue
        if in_section:
            if line.startswith("## "):
                break
            result.append(line)
    return "\n".join(result)[:max_chars]


def _update_entity_page(
    page_path: Path,
    name: str,
    source_content: str,
    source_slug: str,
    today: str,
    reg_entry: dict,
):
    """Merge new source info into existing entity page."""
    existing = page_path.read_text(encoding="utf-8")
    count = reg_entry.get("count", 2)

    # Trim source content — extract only the Summary and Key Points sections
    # from the wiki page if available, otherwise use raw content
    # This avoids sending huge raw transcripts to the model
    summary_section = _extract_key_section(source_content, "Summary", 800)
    keypoints_section = _extract_key_section(source_content, "Key Points", 600)
    if summary_section or keypoints_section:
        trimmed_source = f"{summary_section}\n\n{keypoints_section}".strip()
    else:
        # Raw content — cap tightly
        trimmed_source = source_content[:1000]

    prompt = (
        f"Entity: {name}\n"
        f"New source: {source_slug}\n"
        f"Updated reference count: {count}\n"
        f"Today: {today}\n\n"
        f"Existing entity page:\n{existing}\n\n"
        f"New source excerpt:\n{trimmed_source}"
    )

    # Retry once on empty/short response
    for attempt in range(2):
        updated = call_local(
            prompt,
            ENTITY_UPDATE_SYSTEM,
            timeout=cfg.timeout_long,
            label=f"entity update (attempt {attempt+1})",
        )
        if len(updated.strip()) > 100:
            break
        print(
            f"  [warn] Entity update returned short response on attempt {attempt+1}, retrying...",
            file=sys.stderr,
        )
    else:
        print(f"  [warn] Entity update failed after 2 attempts — keeping existing page", file=sys.stderr)
        return

    # Safety check — make sure Related Pages wasn't wiped
    if "## Related Pages" in existing and "## Related Pages" not in updated:
        # Preserve it by appending from original
        import re

        rel_match = re.search(r"(## Related Pages.*?)(?=\n## |\Z)", existing, re.DOTALL)
        if rel_match:
            updated = updated.rstrip() + "\n\n" + rel_match.group(1).strip() + "\n"

    page_path.write_text(updated, encoding="utf-8")
    print(f"  [entity] Updated entity page: _entities/{page_path.name}")


def _gather_source_excerpts(
    source_slugs: list[str],
    current_content: str,
    current_slug: str,
) -> str:
    """Gather content from known source wiki pages for back-filling."""
    excerpts = [f"--- {current_slug} (current source) ---\n{current_content[:2000]}"]

    for slug in source_slugs:
        if slug == current_slug:
            continue
        # Try to find the wiki page for this source slug
        for wiki_file in cfg.wiki_dir.rglob(f"{slug}*.md"):
            if wiki_file.name == "_overview.md":
                continue
            try:
                text = wiki_file.read_text(encoding="utf-8")
                excerpts.append(f"--- {slug} ---\n{text[:2000]}")
            except Exception:
                pass
            break  # only first match

    return "\n\n".join(excerpts)


def link_entity_pages_to_source(
    source_page_path: Path,
    entity_pages: list[Path],
    call_local_fn,
    timeout: int = 600,
):
    """Add entity page links to the source wiki page's Related Pages section."""
    from wiki_index import backpatch_file

    for ep in entity_pages:
        slug = ep.stem
        entry = f"- [{slug}](../{ENTITY_DIR_NAME}/{ep.name}) — entity page"
        backpatch_file(source_page_path, entry, call_local_fn, timeout=timeout)


def link_source_to_entity_pages(
    source_page_path: Path,
    source_slug: str,
    source_description: str,
    entity_pages: list[Path],
    call_local_fn,
    timeout: int = 600,
    topic_overview_path: Path | None = None,
):
    """Add topic overview link to each entity page's Related Pages section.

    Links entity → topic _overview.md (not individual source page) so that
    entity nodes bridge topic clusters rather than individual pages, producing
    distinct clusters in Obsidian's graph view instead of one blob.

    Falls back to linking entity → source page when topic_overview_path is
    not provided (backwards-compatible).
    """
    from wiki_index import backpatch_file

    if topic_overview_path is not None and topic_overview_path.exists():
        link_target = topic_overview_path
        # Topic folder name as display text — also serves as dedup key.
        # backpatch_file skips if this text already appears in the file.
        topic_display = topic_overview_path.parent.name
        link_desc = "topic overview"
    else:
        link_target = source_page_path
        topic_display = source_slug
        link_desc = source_description

    for ep in entity_pages:
        try:
            rel = link_target.relative_to(ep.parent).as_posix()
        except ValueError:
            rel = (Path("..") / link_target.relative_to(cfg.wiki_dir.parent)).as_posix()
        entry = f"- [{topic_display}]({rel}) — {link_desc}"
        backpatch_file(ep, entry, call_local_fn, timeout=timeout)


if __name__ == "__main__":
    import argparse
    from datetime import date

    parser = argparse.ArgumentParser(description="Entity registry tools")
    parser.add_argument(
        "--backfill", action="store_true", help="Create pages for registry entries missing pages"
    )
    parser.add_argument(
        "--pages-json", help="Phase 2: path to JSON file with {slug: markdown} written by Claude Code"
    )
    parser.add_argument(
        "--date", default=str(date.today()), help="Date to use for created pages (YYYY-MM-DD)"
    )
    args = parser.parse_args()

    if args.backfill:
        cfg.ensure_dirs()
        backfill_missing_entity_pages(args.date, pages_json=args.pages_json)
    else:
        parser.print_help()
