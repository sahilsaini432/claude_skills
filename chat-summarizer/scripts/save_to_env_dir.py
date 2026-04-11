#!/usr/bin/env python3
"""
save_to_env_dir.py — Copy a generated summary file into a topic subfolder
under the directory specified by SUMMARY_OUTPUT_DIR in the .env file.

Usage:
    python scripts/save_to_env_dir.py <path-to-md-file> <topic-name>

    topic-name: the classified topic string (e.g. "Claude Code & Skills")
                Will be slugified into a folder name (e.g. "claude-code-and-skills")

.env format:
    SUMMARY_OUTPUT_DIR=/path/to/your/notes/folder
    MEMORY_MD_PATH=/path/to/your/notes/Memory.md   # optional
"""

import re
import shutil
import sys
from pathlib import Path


def find_env_file() -> Path | None:
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env(env_path: Path) -> dict:
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def slugify_topic(topic: str) -> str:
    """Convert a topic name to a safe folder name.
    'Claude Code & Skills' -> 'claude-code-and-skills'
    """
    topic = topic.lower()
    topic = topic.replace("&", "and")
    topic = re.sub(r"[^a-z0-9]+", "-", topic)
    return topic.strip("-")


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/save_to_env_dir.py <path-to-md-file> <topic-name>")
        sys.exit(1)

    src = Path(sys.argv[1]).resolve()
    topic_name = sys.argv[2]

    if not src.exists():
        print(f"Error: file not found: {src}")
        sys.exit(1)

    env_path = find_env_file()
    if env_path is None:
        print("Warning: No .env file found — skipping copy to output directory.")
        sys.exit(0)

    env = load_env(env_path)
    output_dir_str = env.get("SUMMARY_OUTPUT_DIR", "").strip()

    if not output_dir_str:
        print("Warning: SUMMARY_OUTPUT_DIR not set in .env — skipping copy.")
        sys.exit(0)

    base_dir = Path(output_dir_str).expanduser().resolve()
    topic_folder = slugify_topic(topic_name)
    dest_dir = base_dir / topic_folder

    if not dest_dir.exists():
        print(f"Creating topic folder: {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    print(f"Saved: {dest}")
    # Print the final path so the caller can use it
    print(f"DEST_PATH={dest}")


if __name__ == "__main__":
    main()
