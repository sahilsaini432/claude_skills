#!/usr/bin/env python3
"""
lint.py — Health-check the cortex. Read-only by default.

Usage:
    python3 scripts/lint.py [--fix]

    --fix   Performs safe structural fixes:
            - Creates missing _overview.md stubs (just a placeholder)
            - Adds orphan pages to Memory.md index

    Everything else is report-only. lint.py never touches ## Related Pages sections.
    Cross-reference fixing is the ingest pipeline's job.

Checks:
    1. Dead links       — Memory.md entries pointing to missing files
    2. Orphan pages     — wiki/ files not indexed in Memory.md
    3. Missing overviews — topic folders with pages but no _overview.md
    4. Missing cross-refs — pages in same topic not linked to each other
    5. Entity registry   — entities seen 2+ times with no page, or pages with no registry entry
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from wiki_index import append_log, load_memory, slugify, posix_rel, insert_topic_entry, ensure_master_has_topic


# ── Checks ────────────────────────────────────────────────────────────────────


def check_dead_links(memory_text: str) -> list[str]:
    """Check master Memory.md topic links and each per-topic Memory.md page links."""
    issues = []
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if not m:
            continue
        topic_mem_path = cfg.vault_root / Path(m.group(2))
        if not topic_mem_path.exists():
            issues.append(f"  Dead topic link: [{m.group(1)}]({m.group(2)})")
            continue
        topic_dir = topic_mem_path.parent
        topic_mem_text = topic_mem_path.read_text(encoding="utf-8")
        for tline in topic_mem_text.splitlines():
            tm = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", tline)
            if tm:
                page_path = topic_dir / tm.group(2)
                if not page_path.exists():
                    issues.append(
                        f"  Dead link in {topic_dir.name}/Memory.md: "
                        f"[{tm.group(1)}]({tm.group(2)})"
                    )
    return issues


def check_orphans(memory_text: str) -> list[Path]:
    """Find wiki pages not indexed in any per-topic Memory.md."""
    indexed = set()

    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if not m:
            continue
        topic_mem_path = cfg.vault_root / Path(m.group(2))
        if not topic_mem_path.exists():
            continue
        indexed.add(topic_mem_path.resolve())  # Memory.md itself is tracked
        topic_dir = topic_mem_path.parent
        topic_mem_text = topic_mem_path.read_text(encoding="utf-8")
        for tline in topic_mem_text.splitlines():
            tm = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", tline)
            if tm:
                indexed.add((topic_dir / tm.group(2)).resolve())

    orphans = []
    for wiki_file in cfg.wiki_dir.rglob("*.md"):
        if wiki_file.name.startswith("_") or wiki_file.name == "Memory.md":
            continue
        if wiki_file.resolve() not in indexed:
            orphans.append(wiki_file)
    return orphans


def check_missing_overviews() -> list[Path]:
    issues = []
    if not cfg.wiki_dir.exists():
        return issues
    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir() or topic_dir.name.startswith("_"):
            continue
        pages = [f for f in topic_dir.glob("*.md")
                 if not f.name.startswith("_") and f.name != "Memory.md"]
        if pages and not (topic_dir / "_overview.md").exists():
            issues.append(topic_dir)
    return issues


def check_missing_crossrefs(memory_text: str) -> list[str]:
    issues = []
    if not cfg.wiki_dir.exists():
        return issues
    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir() or topic_dir.name.startswith("_"):
            continue
        pages = [f for f in topic_dir.glob("*.md")
                 if not f.name.startswith("_") and f.name != "Memory.md"]
        if len(pages) < 2:
            continue
        for page in pages:
            content = page.read_text(encoding="utf-8", errors="replace")
            for other in pages:
                if other == page:
                    continue
                other_slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", other.stem)
                if other_slug not in content and other.stem not in content:
                    issues.append(f"  {topic_dir.name}/{page.name} " f"does not link to {other.name}")
    return issues


def check_entity_registry() -> list[str]:
    issues = []
    registry_path = cfg.vault_root / "entity_registry.json"
    if not registry_path.exists():
        return ["  No entity_registry.json yet — will be created on first ingest"]

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entity_dir = cfg.wiki_dir / "_entities"

    for slug, entry in registry.items():
        count = entry.get("count", 0)
        page = entity_dir / f"{slug}.md"
        if count >= 2 and not page.exists():
            issues.append(f"  Missing entity page: {slug}.md (seen {count}x)")
        if count < 2 and page.exists():
            issues.append(f"  Entity page exists but seen <2 times: {slug}.md")

    # Check for entity pages not in registry
    if entity_dir.exists():
        for ep in entity_dir.glob("*.md"):
            if ep.stem not in registry:
                issues.append(f"  Entity page not in registry: {ep.name}")

    return issues


# ── Fix helpers ───────────────────────────────────────────────────────────────


def fix_missing_overviews(missing: list[Path]):
    """Create a blank _overview.md stub — just a placeholder."""
    today = date.today().isoformat()
    for topic_dir in missing:
        topic_name = topic_dir.name.replace("-", " ").title()
        stub = f"""\
# {topic_name}

> Overview stub — run ingest on a new source in this topic to populate.

## Pages
"""
        pages = sorted(f for f in topic_dir.glob("*.md") if not f.name.startswith("_"))
        for p in pages:
            slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", p.stem)
            stub += f"- [{slug}]({p.name})\n"

        stub += f"\n---\n*Created by cortex lint on {today}*\n"
        overview = topic_dir / "_overview.md"
        overview.write_text(stub, encoding="utf-8")
        print(f"  [fix] Created stub: {overview.relative_to(cfg.vault_root)}")


def fix_orphans(orphans: list[Path]) -> str:
    """Add orphan pages to their topic's Memory.md."""
    today = date.today().isoformat()
    for orphan in orphans:
        topic_dir = orphan.parent
        if topic_dir == cfg.wiki_dir:
            continue  # top-level file — skip
        slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", orphan.stem)
        entry = f"- [{slug}]({orphan.name}) — [orphan — review and re-ingest if needed]"
        insert_topic_entry(topic_dir, entry, today)
        topic_mem_rel = posix_rel((topic_dir / "Memory.md").relative_to(cfg.vault_root))
        topic_name = topic_dir.name.replace("-", " ").title()
        ensure_master_has_topic(cfg.memory_md, topic_name, topic_mem_rel, today)
        print(f"  [fix] Added to {topic_dir.name}/Memory.md: {orphan.name}")
    return load_memory(cfg.memory_md)


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = __import__("argparse").ArgumentParser(description="Lint the cortex")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe fixes: create missing _overview.md stubs, add orphans to Memory.md",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    cfg.ensure_dirs()
    memory_text = load_memory(cfg.memory_md)
    all_clear = True
    today = date.today().isoformat()

    print("\ncortex lint\n" + "─" * 40)

    # 1. Dead links
    dead = check_dead_links(memory_text)
    if dead:
        all_clear = False
        print(f"\n[error] Dead links ({len(dead)}) — entries in Memory.md pointing to missing files:")
        for d in dead:
            print(d)
    else:
        print("[ok]  No dead links")

    # 2. Orphan pages
    orphans = check_orphans(memory_text)
    if orphans:
        all_clear = False
        print(f"\n[warn] Orphan pages ({len(orphans)}) — in wiki/ but not indexed in Memory.md:")
        for o in orphans:
            print(f"  {o.relative_to(cfg.vault_root)}")
        if args.fix:
            memory_text = fix_orphans(orphans)
            # fix_orphans writes topic Memory.md files directly; memory_text is refreshed
    else:
        print("[ok]  No orphan pages")

    # 3. Missing overviews
    missing_overviews = check_missing_overviews()
    if missing_overviews:
        all_clear = False
        print(f"\n[warn] Missing _overview.md ({len(missing_overviews)}):")
        for t in missing_overviews:
            print(f"  wiki/{t.name}/")
        if args.fix:
            fix_missing_overviews(missing_overviews)
        else:
            print("  Run with --fix to create stubs")
    else:
        print("[ok]  All topics have _overview.md")

    # 4. Missing cross-references (report only)
    missing_xrefs = check_missing_crossrefs(memory_text)
    if missing_xrefs:
        all_clear = False
        print(f"\n[info] Missing cross-references ({len(missing_xrefs)}) — for information only:")
        for x in missing_xrefs[:15]:
            print(x)
        if len(missing_xrefs) > 15:
            print(f"  ... and {len(missing_xrefs) - 15} more")
        print("  Cross-references are added automatically by ingest — re-ingest sources to fix")
    else:
        print("[ok]  All same-topic pages cross-reference each other")

    # 5. Entity registry
    entity_issues = check_entity_registry()
    if entity_issues:
        all_clear = False
        print(f"\n[warn] Entity registry issues ({len(entity_issues)}):")
        for e in entity_issues:
            print(e)
    else:
        print("[ok]  Entity registry consistent")

    # Summary
    print("\n" + "─" * 40)
    if all_clear:
        print("[done] Wiki looks healthy!")
    else:
        print("[done] Issues found above.")
        if not args.fix:
            print("       Run with --fix to apply safe structural fixes.")
        print("       Cross-reference and entity issues are fixed automatically by ingest.")

    append_log(cfg.log_md, "lint", "health check complete")


if __name__ == "__main__":
    main()
