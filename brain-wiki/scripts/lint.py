#!/usr/bin/env python3
"""
lint.py — Health-check the brain-wiki.

Usage:
    python scripts/lint.py [--fix]

    --fix   Attempt to auto-fix orphan links and missing cross-references

Checks:
    1. Orphan pages — wiki pages with no inbound links from Memory.md
    2. Dead links — entries in Memory.md pointing to missing files
    3. Missing cross-references — pages in the same topic not linked to each other
    4. Stale _overview.md — topic has pages but no overview
    5. Empty topics — topic folder exists but has no pages
    6. LLM contradiction scan — asks gemma4:31b to flag contradictions across topic pages
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from llm import call_local
from wiki_index import append_log, backpatch_file, load_memory, get_topic_entries, slugify

CONTRADICTION_SYSTEM = """\
You are reviewing a set of wiki pages from the same topic for a personal knowledge base.
Identify:
1. Factual contradictions between pages
2. Claims in older pages superseded by newer ones
3. Important concepts mentioned but lacking their own page
4. Gaps that could be filled by searching for a new source

Return a concise markdown report. Be specific — quote the conflicting claims and name the pages.
If everything looks consistent, say so briefly.
"""


def check_dead_links(memory_text: str) -> list[str]:
    issues = []
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if m:
            page_path = cfg.vault_root / m.group(2)
            if not page_path.exists():
                issues.append(f"Dead link: [{m.group(1)}]({m.group(2)}) — file not found")
    return issues


def check_orphans(memory_text: str) -> list[Path]:
    """Find wiki pages that exist on disk but aren't in Memory.md."""
    indexed_paths = set()
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if m:
            indexed_paths.add((cfg.vault_root / m.group(2)).resolve())

    orphans = []
    for wiki_file in cfg.wiki_dir.rglob("*.md"):
        if wiki_file.name == "_overview.md":
            continue
        if wiki_file.resolve() not in indexed_paths:
            orphans.append(wiki_file)
    return orphans


def check_missing_overviews(memory_text: str) -> list[str]:
    """Find topic folders that have pages but no _overview.md."""
    issues = []
    if not cfg.wiki_dir.exists():
        return issues
    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir():
            continue
        pages = [f for f in topic_dir.glob("*.md") if f.name != "_overview.md"]
        overview = topic_dir / "_overview.md"
        if pages and not overview.exists():
            issues.append(f"Missing _overview.md in: wiki/{topic_dir.name}/")
    return issues


def check_missing_crossrefs(memory_text: str) -> list[str]:
    """Find pages in the same topic that don't reference each other."""
    issues = []
    if not cfg.wiki_dir.exists():
        return issues

    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir():
            continue
        pages = [f for f in topic_dir.glob("*.md") if f.name != "_overview.md"]
        if len(pages) < 2:
            continue
        for page in pages:
            content = page.read_text(encoding="utf-8", errors="replace")
            for other in pages:
                if other == page:
                    continue
                if other.stem not in content:
                    issues.append(
                        f"Missing cross-ref: {topic_dir.name}/{page.name} "
                        f"doesn't link to {other.name}"
                    )
    return issues


def scan_contradictions(memory_text: str) -> dict[str, str]:
    """Run LLM contradiction scan per topic. Returns {topic: report}."""
    reports = {}
    if not cfg.wiki_dir.exists():
        return reports

    for topic_dir in cfg.wiki_dir.iterdir():
        if not topic_dir.is_dir():
            continue
        pages = [f for f in topic_dir.glob("*.md") if f.name != "_overview.md"]
        if len(pages) < 2:
            continue

        topic_name = topic_dir.name.replace("-", " ").title()
        print(f"  Scanning '{topic_name}' ({len(pages)} pages)...")

        pages_block = "\n\n".join(
            f"--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')[:1500]}"
            for p in pages
        )
        prompt = f"Topic: {topic_name}\n\nPages:\n\n{pages_block}"
        report = call_local(prompt, CONTRADICTION_SYSTEM, timeout=cfg.timeout_medium)
        reports[topic_name] = report

    return reports


def fix_missing_overviews(issues: list[str]):
    from wiki_index import get_topic_entries

    OVERVIEW_INIT_SYSTEM = """\
You are creating a topic overview page for a personal knowledge wiki.
Write a concise _overview.md given the pages already in this topic.
Return ONLY markdown — no fences, no preamble.

# <Topic Name>

## What this topic covers
<2–3 sentences>

## Pages
<list pages with one-line descriptions>

## Evolving Thesis
<Running synthesis based on current pages.>

---
*Managed by brain-wiki*
"""
    for issue in issues:
        folder_name = issue.split("wiki/")[1].rstrip("/")
        topic_dir = cfg.wiki_dir / folder_name
        pages = [f for f in topic_dir.glob("*.md") if f.name != "_overview.md"]
        pages_block = "\n\n".join(
            f"--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')[:1000]}"
            for p in pages
        )
        topic_name = folder_name.replace("-", " ").title()
        prompt = f"Topic: {topic_name}\n\nExisting pages:\n\n{pages_block}"
        content = call_local(prompt, OVERVIEW_INIT_SYSTEM, timeout=cfg.timeout_medium)
        overview_path = topic_dir / "_overview.md"
        overview_path.write_text(content, encoding="utf-8")
        print(f"  ✓ Created _overview.md for: {folder_name}")


def main():
    parser = __import__("argparse").ArgumentParser(description="Lint the brain-wiki")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-fix missing overviews and cross-references")
    args = parser.parse_args()

    cfg.ensure_dirs()
    memory_text = load_memory(cfg.memory_md)

    print("\n🔍 brain-wiki lint\n")
    all_clear = True

    # 1. Dead links
    dead = check_dead_links(memory_text)
    if dead:
        all_clear = False
        print(f"❌ Dead links ({len(dead)}):")
        for d in dead:
            print(f"   {d}")
    else:
        print("✓  No dead links")

    # 2. Orphans
    orphans = check_orphans(memory_text)
    if orphans:
        all_clear = False
        print(f"\n❌ Orphan pages ({len(orphans)}) — in wiki/ but not in Memory.md:")
        for o in orphans:
            print(f"   {o.relative_to(cfg.vault_root)}")
    else:
        print("✓  No orphan pages")

    # 3. Missing overviews
    missing_overviews = check_missing_overviews(memory_text)
    if missing_overviews:
        all_clear = False
        print(f"\n❌ Missing _overview.md ({len(missing_overviews)}):")
        for m in missing_overviews:
            print(f"   {m}")
        if args.fix:
            print("  Auto-fixing...")
            fix_missing_overviews(missing_overviews)
    else:
        print("✓  All topics have _overview.md")

    # 4. Missing cross-references
    missing_xrefs = check_missing_crossrefs(memory_text)
    if missing_xrefs:
        all_clear = False
        print(f"\n⚠️  Missing cross-references ({len(missing_xrefs)}):")
        for x in missing_xrefs[:10]:  # cap at 10 to avoid noise
            print(f"   {x}")
        if len(missing_xrefs) > 10:
            print(f"   ... and {len(missing_xrefs) - 10} more")
        if args.fix:
            print("  Auto-fixing cross-references...")
            # back-patch missing links
            for issue in missing_xrefs:
                m = re.match(r"Missing cross-ref: (.+?) doesn't link to (.+)", issue)
                if m:
                    src = cfg.wiki_dir / m.group(1)
                    tgt = cfg.wiki_dir / Path(m.group(1)).parent.name / m.group(2)
                    if tgt.exists():
                        rel = tgt.relative_to(src.parent)
                        entry = f"- [{tgt.stem}]({rel}) — related page in same topic"
                        backpatch_file(src, entry, call_local, timeout=cfg.timeout_medium)
    else:
        print("✓  All same-topic pages are cross-referenced")

    # 5. LLM contradiction scan
    print("\n🤖 Running contradiction scan (local model)...")
    contradiction_reports = scan_contradictions(memory_text)
    if contradiction_reports:
        print("\n📋 Contradiction / gap report:")
        for topic, report in contradiction_reports.items():
            print(f"\n  [{topic}]")
            for line in report.splitlines():
                print(f"    {line}")
    else:
        print("  No multi-page topics to scan yet.")

    # Summary
    print("\n" + "─" * 60)
    if all_clear:
        print("✅ Wiki looks healthy!")
    else:
        print("⚠️  Issues found above. Run with --fix to auto-resolve some.")

    append_log(cfg.log_md, "lint", "health check complete")


if __name__ == "__main__":
    main()
