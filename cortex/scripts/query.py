#!/usr/bin/env python3
"""
query.py — Print wiki pages relevant to a question for Claude Code to read.

Usage:
    python scripts/query.py "your question here" [--save <answer_file>]

This script does NOT call any LLM. It:
  1. Prints the master Memory.md
  2. Prints every per-topic Memory.md and _overview.md
  3. Instructs Claude Code to identify the relevant pages and load them with
     its Read tool to synthesize the answer in chat

To file an answer back into the wiki:
    python scripts/query.py "question" --save answer.md
The contents of <answer_file> are stored verbatim as a wiki page (no LLM
rewriting). Write the file as a complete wiki page beforehand.
"""

import argparse
import re
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
    load_memory,
    slugify,
    posix_rel,
    insert_topic_entry,
    ensure_master_has_topic,
)


def dump_index_and_overviews(memory_text: str) -> int:
    """Print master Memory.md plus every topic Memory.md and _overview.md.

    Returns the number of topics found.
    """
    print("=== master Memory.md ===")
    print(memory_text)
    print()

    topic_count = 0
    for line in memory_text.splitlines():
        m = re.match(r"-\s+\[([^\]]+)\]\(([^)]+\.md)\)", line)
        if not m:
            continue
        topic_name = m.group(1)
        topic_mem_path = cfg.vault_root / m.group(2)
        if not topic_mem_path.exists():
            continue
        topic_count += 1
        topic_dir = topic_mem_path.parent

        print(f"=== {topic_name} :: Memory.md ===")
        print(topic_mem_path.read_text(encoding="utf-8"))
        print()

        overview = topic_dir / "_overview.md"
        if overview.exists():
            print(f"=== {topic_name} :: _overview.md ===")
            print(overview.read_text(encoding="utf-8"))
            print()
    return topic_count


def save_answer(question: str, answer_file: Path):
    """File a pre-written answer file into the wiki verbatim."""
    if not answer_file.exists():
        print(f"Error: answer file not found: {answer_file}", file=sys.stderr)
        sys.exit(1)

    answer_text = answer_file.read_text(encoding="utf-8").strip()
    if not answer_text:
        print(f"Error: answer file is empty: {answer_file}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower())[:40].strip("-")
    slug = f"query-{slug}-{today}"

    topic = "Queries & Synthesis"
    topic_folder = slugify(topic)
    topic_dir = cfg.wiki_dir / topic_folder
    topic_dir.mkdir(parents=True, exist_ok=True)

    page_path = topic_dir / f"{slug}.md"
    page_path.write_text(answer_text, encoding="utf-8")

    local_path = posix_rel(page_path.relative_to(topic_dir))
    memory_entry = f"- [{slug}]({local_path}) — Q: {question[:60]}"
    insert_topic_entry(topic_dir, memory_entry, today)
    topic_mem_rel = posix_rel((topic_dir / "Memory.md").relative_to(cfg.vault_root))
    ensure_master_has_topic(cfg.memory_md, topic, topic_mem_rel, today)

    append_log(cfg.log_md, "query-saved", f"{question[:60]} → {slug}.md")
    print(f"[ok] Answer filed: wiki/{topic_folder}/{slug}.md")


def main():
    parser = argparse.ArgumentParser(description="Print wiki context for a question")
    parser.add_argument("question", nargs="?", help="Your question")
    parser.add_argument(
        "--save",
        metavar="ANSWER_FILE",
        help="File a pre-written answer (markdown) into the wiki under Queries & Synthesis",
    )
    args = parser.parse_args()

    cfg.ensure_dirs()

    if args.save and args.question:
        save_answer(args.question, Path(args.save))
        return

    if not args.question:
        parser.print_help()
        sys.exit(1)

    memory_text = load_memory(cfg.memory_md)

    print("\n── BRAIN WIKI CONTEXT ──────────────────────────────────────")
    print(f"Question: {args.question}")
    print("────────────────────────────────────────────────────────────\n")

    topic_count = dump_index_and_overviews(memory_text)

    if topic_count == 0:
        print("(No topics indexed yet — ingest some sources first.)")

    print("── END BRAIN WIKI CONTEXT ──────────────────────────────────")
    print(
        "Claude Code: identify the topics most relevant to the question above,\n"
        "then use your Read tool on the specific source pages listed in those\n"
        "topic Memory.md files. Synthesize an answer that cites page names.\n"
        "If no pages cover the topic, say so."
    )
    print("────────────────────────────────────────────────────────────")

    append_log(cfg.log_md, "query", args.question[:80])


if __name__ == "__main__":
    main()
