#!/usr/bin/env python3
"""
save_to_env_dir.py — Copy a generated summary file to the directory
specified by SUMMARY_OUTPUT_DIR in the .env file.

Usage:
    python scripts/save_to_env_dir.py <path-to-generated-md-file>

.env format (place in skill root or working directory):
    SUMMARY_OUTPUT_DIR=/path/to/your/notes/folder
"""

import os
import shutil
import sys
from pathlib import Path


def find_env_file() -> Path | None:
    """Walk up from CWD looking for a .env file."""
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env(env_path: Path) -> dict:
    """Parse a simple KEY=VALUE .env file (no shell variable expansion)."""
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/save_to_env_dir.py <path-to-md-file>")
        sys.exit(1)

    src = Path(sys.argv[1]).resolve()
    if not src.exists():
        print(f"Error: file not found: {src}")
        sys.exit(1)

    # Locate .env
    env_path = find_env_file()
    if env_path is None:
        print("Warning: No .env file found — skipping copy to output directory.")
        sys.exit(0)

    env = load_env(env_path)
    output_dir_str = env.get("SUMMARY_OUTPUT_DIR", "").strip()

    if not output_dir_str:
        print("Warning: SUMMARY_OUTPUT_DIR not set in .env — skipping copy.")
        sys.exit(0)

    dest_dir = Path(output_dir_str).expanduser().resolve()

    if not dest_dir.exists():
        print(f"Creating output directory: {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    print(f"Saved: {dest}")


if __name__ == "__main__":
    main()
