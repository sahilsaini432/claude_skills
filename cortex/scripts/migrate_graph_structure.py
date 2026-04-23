#!/usr/bin/env python3
"""
migrate_graph_structure.py — Fix existing vault for hub-and-spoke graph topology.

Dry-run by default. Pass --apply to write changes.

Changes made:
  1. Entity pages (_entities/*.md):
     Replace source-page links in Related Pages with topic _overview.md links.
     Deduplicate: multiple sources from same topic → one overview link.

  2. Source pages (wiki/*/*.md, excluding _overview.md):
     Add [_overview](_overview.md) parent link if not already present.

No content, summaries, Memory.md, log.md, or entity_registry.json are touched.
Idempotent — safe to re-run.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg


def main():
    parser = argparse.ArgumentParser(description="Migrate vault to hub-and-spoke graph structure")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk. Default is dry-run (print only).",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print("[dry-run] No files will be modified. Pass --apply to write changes.\n")

    wiki_dir = cfg.wiki_dir
    entity_dir = wiki_dir / "_entities"

    entity_count = 0
    source_count = 0

    # ── 1. Entity pages ───────────────────────────────────────────────────────

    if entity_dir.exists():
        for entity_page in sorted(entity_dir.glob("*.md")):
            changed = _migrate_entity_page(entity_page, wiki_dir, dry_run)
            if changed:
                entity_count += 1

    # ── 2. Source pages ───────────────────────────────────────────────────────

    for topic_dir in sorted(wiki_dir.iterdir()):
        if not topic_dir.is_dir() or topic_dir.name == "_entities":
            continue
        for page in sorted(topic_dir.glob("*.md")):
            if page.name == "_overview.md":
                continue
            changed = _add_overview_link(page, dry_run)
            if changed:
                source_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────

    action = "Would modify" if dry_run else "Modified"
    print(f"\n{action} {entity_count} entity page(s) and {source_count} source page(s).")
    if dry_run:
        print("Run with --apply to write changes.")


def _migrate_entity_page(entity_page: Path, wiki_dir: Path, dry_run: bool) -> bool:
    """Replace source-page links with topic overview links in entity Related Pages."""
    content = entity_page.read_text(encoding="utf-8")

    rel_match = re.search(r"(## Related Pages\s*\n)(.*?)(?=\n---|\n## |\Z)", content, re.DOTALL)
    if not rel_match:
        return False

    section_start = rel_match.start(2)
    section_end = rel_match.end(2)
    rel_body = rel_match.group(2)

    # Parse all links in the Related Pages section
    # Pattern: - [display](path) — description
    link_re = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\).*$", re.MULTILINE)
    links = link_re.findall(rel_body)

    if not links:
        return False

    seen_topics: dict[str, tuple[str, str]] = {}  # topic_folder → (display, rel_path)
    non_source_lines: list[str] = []

    for display, path in links:
        path_obj = Path(path.replace("\\", "/"))
        parts = path_obj.parts

        # Already an overview link — keep but deduplicate
        if path_obj.name == "_overview.md":
            topic_folder = parts[-2] if len(parts) >= 2 else display
            if topic_folder not in seen_topics:
                seen_topics[topic_folder] = (display, path)
            continue

        # Entity-to-entity link — keep as-is
        if "_entities" in parts:
            non_source_lines.append(f"- [{display}]({path}) — entity")
            continue

        # Source page link — find topic folder and replace with overview link
        # Path formats seen: "../topic-folder/page.md" or "wiki/topic-folder/page.md"
        topic_folder = None
        for i, part in enumerate(parts):
            if part not in (".", "..", "wiki", "_entities") and i < len(parts) - 1:
                # This part is a directory component before the filename
                topic_folder = part
                break

        if topic_folder is None or topic_folder == "_entities":
            # Can't determine topic — keep original
            non_source_lines.append(f"- [{display}]({path}) — source page")
            continue

        if topic_folder not in seen_topics:
            # Entity is in wiki/_entities/, overview is in wiki/topic-folder/
            overview_rel = f"../{topic_folder}/_overview.md"
            seen_topics[topic_folder] = (topic_folder, overview_rel)

    # Build new Related Pages body
    new_lines = [
        f"- [{disp}]({rel}) — topic overview" for disp, rel in seen_topics.values()
    ] + non_source_lines

    if not new_lines:
        return False

    new_rel_body = "\n".join(new_lines) + "\n"

    if new_rel_body.strip() == rel_body.strip():
        return False

    new_content = content[:section_start] + new_rel_body + content[section_end:]

    if dry_run:
        print(f"[entity] {entity_page.name}:")
        print(f"  before: {rel_body.strip()!r:.120}")
        print(f"  after:  {new_rel_body.strip()!r:.120}")
    else:
        entity_page.write_text(new_content, encoding="utf-8")
        print(f"  [migrated entity] {entity_page.name} -> {len(seen_topics)} topic overview(s)")

    return True


def _add_overview_link(page: Path, dry_run: bool) -> bool:
    """Prepend [_overview](_overview.md) to Related Pages of a source page."""
    content = page.read_text(encoding="utf-8")

    # Skip if already has an overview link or no Related Pages section
    if "_overview" in content or "## Related Pages" not in content:
        return False

    new_content = content.replace(
        "## Related Pages\n",
        "## Related Pages\n- [_overview](_overview.md) — topic index\n",
        1,
    )

    if new_content == content:
        return False

    if dry_run:
        print(f"[source] {page.parent.name}/{page.name}: add _overview link")
    else:
        page.write_text(new_content, encoding="utf-8")
        print(f"  [migrated source] {page.parent.name}/{page.name}")

    return True


if __name__ == "__main__":
    main()
