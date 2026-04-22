#!/usr/bin/env python3
"""
migrate_to_topic_memory.py — Migrate flat master Memory.md to per-topic Memory.md files.

Dry-run by default. Pass --apply to write.
Idempotent — skips topics that already have a Memory.md.
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg
from wiki_index import load_memory, slugify, posix_rel


def main():
    parser = argparse.ArgumentParser(description="Migrate to per-topic Memory.md files")
    parser.add_argument("--apply", action="store_true", help="Write changes to disk")
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print("[dry-run] No files will be modified. Pass --apply to write.\n")

    today = date.today().isoformat()
    memory_text = load_memory(cfg.memory_md)

    # Parse master Memory.md: collect ## Topic sections
    topics: dict[str, list[tuple[str, str, str]]] = {}  # name -> [(slug, vault_rel, desc)]
    current_topic: str | None = None

    for line in memory_text.splitlines():
        if line.startswith("## "):
            current_topic = line[3:].strip()
            topics[current_topic] = []
        elif current_topic:
            m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+)\)\s+[—-]+\s+(.*)", line)
            if m:
                topics[current_topic].append((m.group(1), m.group(2).strip(), m.group(3).strip()))

    if not topics:
        print("No ## topic sections found in master Memory.md.")
        print("Already migrated, or Memory.md is empty.")
        return

    master_entries: list[str] = []

    for topic, pages in topics.items():
        topic_folder = slugify(topic)
        topic_dir = cfg.wiki_dir / topic_folder
        topic_mem_path = topic_dir / "Memory.md"
        topic_mem_rel = posix_rel((topic_dir / "Memory.md").relative_to(cfg.vault_root))

        master_entries.append(f"- [{topic}]({topic_mem_rel})")

        if topic_mem_path.exists():
            print(f"[skip] {topic}: Memory.md already exists")
            continue

        # Build per-topic Memory.md with local (folder-relative) page paths
        entry_lines: list[str] = []
        for slug, vault_rel, desc in pages:
            page_path = cfg.vault_root / vault_rel
            try:
                local_path = page_path.relative_to(topic_dir).as_posix()
            except ValueError:
                local_path = vault_rel
                print(f"  [warn] {slug}: path outside topic_dir, keeping vault-relative")
            entry_lines.append(f"- [{slug}]({local_path}) — {desc}")

        entries_block = "\n".join(entry_lines)
        topic_mem_content = (
            f"# {topic}\n\n"
            f"> Pages in this topic. Managed by brain-wiki — do not edit manually.\n\n"
            f"---\n\n"
            f"{entries_block}\n\n"
            f"---\n"
            f"*Last updated: {today}*\n"
        )

        if dry_run:
            print(f"[topic] {topic} ({len(pages)} page(s)) -> {topic_mem_rel}")
            for line in entry_lines[:3]:
                print(f"  {line}")
            if len(pages) > 3:
                print(f"  ... and {len(pages) - 3} more")
        else:
            topic_dir.mkdir(parents=True, exist_ok=True)
            topic_mem_path.write_text(topic_mem_content, encoding="utf-8")
            print(f"  [created] {topic_mem_rel}")

    # Rewrite master Memory.md as flat topic list
    new_master = (
        "# Memory\n\n"
        "> Personal knowledge wiki index. Each topic has its own Memory.md with its pages.\n"
        "> Managed by brain-wiki — do not edit manually.\n\n"
        "---\n\n"
        + "\n".join(master_entries)
        + "\n\n---\n"
        + f"*Last updated: {today}*\n"
    )

    if dry_run:
        print(f"\n[master] New Memory.md will have {len(master_entries)} topic link(s).")
        print("Run with --apply to write changes.")
    else:
        cfg.memory_md.write_text(new_master, encoding="utf-8")
        print(f"\n[ok] Master Memory.md rewritten ({len(master_entries)} topics).")
        print("[ok] Migration complete.")


if __name__ == "__main__":
    main()
