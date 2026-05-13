#!/usr/bin/env python3
"""
entities.py — Entity registry and page management for cortex.

Tracks how many times each entity/concept has been seen across ingests.
Creates a stub entity page in wiki/_entities/ on first appearance and bumps
the count on every subsequent appearance. Use the --backfill flow to enrich
stubs into full entity pages via Claude Code.

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

ENTITY_DIR_NAME = "_entities"


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


def process_entities(
    entities: list[dict],
    source_slug: str,
    today: str,
) -> list[Path]:
    """
    Update entity registry. Create a stub page on first appearance; bump count
    only on subsequent appearances. Use `entities.py --backfill` to enrich
    stubs into full pages via Claude Code.

    Returns list of entity page paths that were created or already exist.
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
            if source_slug not in registry[slug]["sources"]:
                registry[slug]["count"] += 1
                registry[slug]["sources"].append(source_slug)
                if desc and len(desc) > len(registry[slug].get("description", "")):
                    registry[slug]["description"] = desc
            count = registry[slug]["count"]
            print(f"  [entity] Seen {count}x: {name}")

        entity_page = entity_dir / f"{slug}.md"
        if not entity_page.exists():
            _write_stub_page(entity_page, registry[slug], today)
            print(f"  [entity] Stub created: _entities/{slug}.md (run --backfill to enrich)")
        touched_pages.append(entity_page)

    save_registry(registry)
    return touched_pages


def _write_stub_page(page_path: Path, reg_entry: dict, today: str):
    """Write a minimal stub entity page. Enrich later with --backfill."""
    name = reg_entry.get("name", page_path.stem)
    etype = reg_entry.get("type", "concept")
    description = reg_entry.get("description", "")
    first = reg_entry.get("first_seen", today)
    count = reg_entry.get("count", 1)

    content = (
        f"# {name}\n\n"
        f"**Type:** {etype}\n"
        f"**First seen:** {first}\n"
        f"**Times referenced:** {count}\n\n"
        f"---\n\n"
        f"## What it is\n"
        f"{description if description else '<!-- stub — run entities.py --backfill to enrich -->'}\n\n"
        f"## Key Facts\n"
        f"<!-- stub -->\n\n"
        f"## How it's been used\n"
        f"<!-- stub -->\n\n"
        f"## Related Pages\n\n"
        f"---\n"
        f"*Managed by cortex*\n"
    )
    page_path.write_text(content, encoding="utf-8")


def backfill_missing_entity_pages(today: str, pages_json: str | None = None) -> int:
    """
    Enrich stub entity pages (or create missing ones) via Claude Code.

    Phase 1 (no pages_json): print a CLAUDE-CHAT BACKFILL block describing every
    entity that needs a page (missing or stub) and exit with code 2.
    Phase 2 (pages_json set): read {"slug": "markdown content"} and commit pages
    to disk, overwriting any existing stub.

    Returns count of pages written.
    """
    registry = load_registry()
    entity_dir = _entity_dir()

    # ── Phase 2: commit pages written by Claude Code ─────────────────────────
    if pages_json is not None:
        data = json.loads(Path(pages_json).read_text(encoding="utf-8"))
        written = 0
        for slug, content in data.items():
            page_path = entity_dir / f"{slug}.md"
            page_path.write_text(content, encoding="utf-8")
            print(f"  [backfill] Committed: _entities/{slug}.md")
            written += 1
        print(f"  [backfill] Done — {written} entity page(s) written.")
        return written

    # ── Phase 1: collect entities needing pages, print prompt, exit 2 ────────
    missing = []
    for slug, entry in registry.items():
        page_path = entity_dir / f"{slug}.md"
        if page_path.exists():
            existing = page_path.read_text(encoding="utf-8")
            if "<!-- stub" not in existing and "<!-- stub -->" not in existing:
                continue  # already enriched
        sources = entry.get("sources", [])
        excerpts = _gather_source_excerpts(sources)
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
        print("  [backfill] No stub or missing entity pages found.")
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
    print("1. Generate one wiki page per entity using the schema above.")
    print('2. Write all pages as a single JSON file: {"slug": "full page markdown", ...}')
    print("3. Re-run to commit:")
    print("   python3 ~/.claude/skills/cortex/scripts/entities.py --backfill --pages-json <path>")
    print(f"{'=' * 70}")
    sys.exit(2)


def _gather_source_excerpts(source_slugs: list[str]) -> str:
    """Gather content from known source wiki pages for back-filling."""
    excerpts = []
    for slug in source_slugs:
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


def link_entity_pages_to_source(source_page_path: Path, entity_pages: list[Path]):
    """Add entity page links to the source wiki page's Related Pages section."""
    from wiki_index import backpatch_file

    for ep in entity_pages:
        slug = ep.stem
        entry = f"- [{slug}](../{ENTITY_DIR_NAME}/{ep.name}) — entity page"
        backpatch_file(source_page_path, entry)


def link_source_to_entity_pages(
    source_page_path: Path,
    source_slug: str,
    source_description: str,
    entity_pages: list[Path],
    topic_overview_path: Path | None = None,
):
    """Add a topic-overview link to each entity page's Related Pages section.

    Links entity → topic _overview.md (not individual source page) so that
    entity nodes bridge topic clusters rather than individual pages.
    Falls back to linking entity → source page when topic_overview_path is
    not provided.
    """
    from wiki_index import backpatch_file

    if topic_overview_path is not None and topic_overview_path.exists():
        link_target = topic_overview_path
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
        backpatch_file(ep, entry)


if __name__ == "__main__":
    import argparse
    from datetime import date

    parser = argparse.ArgumentParser(description="Entity registry tools")
    parser.add_argument(
        "--backfill", action="store_true", help="Enrich stub or missing entity pages via Claude Code"
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
