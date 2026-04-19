#!/usr/bin/env python3
"""
lint.py — Health-check the brain-wiki. Read-only by default.

Usage:
    python3 scripts/lint.py [--fix]

    --fix   Only performs safe, non-LLM structural fixes:
            - Creates missing _overview.md stubs (no LLM, just a placeholder)
            - Adds orphan pages to Memory.md index

    Everything else is report-only. lint.py never touches ## Related Pages sections.
    Cross-reference fixing is the ingest pipeline's job.

Checks:
    1. Dead links       — Memory.md entries pointing to missing files
    2. Orphan pages     — wiki/ files not indexed in Memory.md
    3. Missing overviews — topic folders with pages but no _overview.md
    4. Missing cross-refs — pages in same topic not linked to each other
    5. Entity registry   — entities seen 2+ times with no page, or pages with no registry entry
    6. Contradiction scan — LLM reads each topic's pages and flags inconsistencies (report only)
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from llm import call_local
from wiki_index import append_log, load_memory, insert_entry, slugify

CONTRADICTION_SYSTEM = """\
You are reviewing wiki pages from the same topic for a personal knowledge base.
Identify:
1. Factual contradictions between pages
2. Claims in older pages superseded by newer ones
3. Important concepts mentioned but lacking their own page
4. Knowledge gaps worth filling with new sources

Return a concise markdown report. Be specific — name the pages and quote conflicting claims.
If everything looks consistent, say so briefly.
"""


# ── Checks ────────────────────────────────────────────────────────────────────


def check_dead_links(memory_text: str) -> list[str]:
    issues = []
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if m:
            page_path = cfg.vault_root / m.group(2).replace("/", Path.cwd().drive and "\\" or "/")
            # Normalise slashes for Windows
            page_path = cfg.vault_root / Path(m.group(2))
            if not page_path.exists():
                issues.append(f"  Dead link: [{m.group(1)}]({m.group(2)})")
    return issues


def check_orphans(memory_text: str) -> list[Path]:
    indexed = set()
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if m:
            indexed.add((cfg.vault_root / m.group(2)).resolve())

    orphans = []
    for wiki_file in cfg.wiki_dir.rglob("*.md"):
        if wiki_file.name.startswith("_"):
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
        pages = [f for f in topic_dir.glob("*.md") if not f.name.startswith("_")]
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
        pages = [f for f in topic_dir.glob("*.md") if not f.name.startswith("_")]
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


def scan_contradictions() -> dict[str, str]:
    """Read-only LLM scan per topic. Returns {topic_name: report}."""
    reports = {}
    if not cfg.wiki_dir.exists():
        return reports

    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir() or topic_dir.name.startswith("_"):
            continue
        pages = [f for f in topic_dir.glob("*.md") if not f.name.startswith("_")]
        if len(pages) < 2:
            continue

        topic_name = topic_dir.name.replace("-", " ").title()
        print(f"  Scanning '{topic_name}' ({len(pages)} pages)...")

        pages_block = "\n\n".join(
            f"--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')[:2000]}"
            for p in sorted(pages)
        )
        prompt = f"Topic: {topic_name}\n\nPages:\n\n{pages_block}"
        reports[topic_name] = call_local(
            prompt, CONTRADICTION_SYSTEM, timeout=cfg.timeout_long, label=f"scan {topic_name}"
        )
    return reports


# ── Fix helpers (non-LLM only) ─────────────────────────────────────────────────


def fix_missing_overviews(missing: list[Path]):
    """Create a blank _overview.md stub — no LLM, just a placeholder."""
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

        stub += f"\n---\n*Created by brain-wiki lint on {today}*\n"
        overview = topic_dir / "_overview.md"
        overview.write_text(stub, encoding="utf-8")
        print(f"  [fix] Created stub: {overview.relative_to(cfg.vault_root)}")


def fix_orphans(orphans: list[Path], memory_text: str) -> str:
    """Add orphan pages to Memory.md under an Uncategorized section."""
    today = date.today().isoformat()
    for orphan in orphans:
        try:
            rel = orphan.relative_to(cfg.vault_root).as_posix()
        except ValueError:
            rel = orphan.as_posix()
        slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", orphan.stem)
        entry = f"- [{slug}]({rel}) — [orphan — review and re-ingest if needed]"
        memory_text = insert_entry(memory_text, "Uncategorized", entry, today)
        print(f"  [fix] Added to Memory.md (Uncategorized): {orphan.name}")
    return memory_text


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = __import__("argparse").ArgumentParser(description="Lint the brain-wiki")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe non-LLM fixes: create missing _overview.md stubs, add orphans to Memory.md",
    )
    parser.add_argument("--no-scan", action="store_true", help="Skip the LLM contradiction scan (faster)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    cfg.ensure_dirs()
    memory_text = load_memory(cfg.memory_md)
    all_clear = True
    today = date.today().isoformat()

    print("\nbrain-wiki lint\n" + "─" * 40)

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
            memory_text = fix_orphans(orphans, memory_text)
            cfg.memory_md.write_text(memory_text, encoding="utf-8")
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

    # 6. Contradiction scan (LLM, read-only)
    if not args.no_scan:
        print("\n[scan] Running contradiction scan (read-only, local model)...")
        reports = scan_contradictions()
        if reports:
            print("\nContradiction / gap report:")
            for topic, report in reports.items():
                print(f"\n  [{topic}]")
                for line in report.splitlines():
                    print(f"    {line}")
        else:
            print("  No multi-page topics to scan yet.")
    else:
        print("[skip] Contradiction scan skipped (--no-scan)")

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
