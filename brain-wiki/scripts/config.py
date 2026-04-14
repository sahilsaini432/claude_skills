#!/usr/bin/env python3
"""
config.py — Load .env and resolve vault paths and LLM settings.

Looks for .env at a fixed location:
    ~/.claude/skills/.env

Exposes:
    cfg.vault_root      → Path   (BRAIN_VAULT_ROOT)
    cfg.memory_md       → Path   (BRAIN_VAULT_ROOT/Memory.md)
    cfg.log_md          → Path   (BRAIN_VAULT_ROOT/log.md)
    cfg.raw_dir         → Path   (BRAIN_VAULT_ROOT/raw)
    cfg.wiki_dir        → Path   (BRAIN_VAULT_ROOT/wiki)
    cfg.llm_url         → str    (LOCAL_LLM_URL)
    cfg.llm_model       → str    (LOCAL_LLM_MODEL)
    cfg.timeout_short   → int    seconds for classify/relevance calls
    cfg.timeout_medium  → int    seconds for overview/merge/backpatch calls
    cfg.timeout_long    → int    seconds for full page generation calls

.env keys:
    BRAIN_VAULT_ROOT=E:\brain                           # required
    LOCAL_LLM_URL=http://localhost:11434/api/generate   # optional
    LOCAL_LLM_MODEL=gemma4:31b                          # optional
    LLM_TIMEOUT_SHORT=300                               # optional, default 300s
    LLM_TIMEOUT_MEDIUM=600                              # optional, default 600s
    LLM_TIMEOUT_LONG=900                                # optional, default 900s

Increase timeouts if you are accessing Ollama over a network (e.g. Tailscale).
"""

import sys
from pathlib import Path

ENV_PATH = Path.home() / ".claude" / "skills" / ".env"

_DEFAULT_LLM_URL = "http://localhost:11434/api/generate"
_DEFAULT_LLM_MODEL = "gemma4:31b"
_DEFAULT_TIMEOUT_SHORT = 300  # classify, relevance, image
_DEFAULT_TIMEOUT_MEDIUM = 600  # overview, merge, backpatch, save-page
_DEFAULT_TIMEOUT_LONG = 900  # full wiki page generation


def _load_env(p: Path) -> dict:
    env = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Config:
    def __init__(self):
        if not ENV_PATH.exists():
            print(
                f"Error: .env not found at {ENV_PATH}\n"
                f"Create it with at minimum:\n"
                f"  BRAIN_VAULT_ROOT=E:\\brain",
                file=sys.stderr,
            )
            sys.exit(1)

        env = _load_env(ENV_PATH)

        root_str = env.get("BRAIN_VAULT_ROOT", "").strip()
        if not root_str:
            print(
                f"Error: BRAIN_VAULT_ROOT not set in {ENV_PATH}\n" f"Add:  BRAIN_VAULT_ROOT=E:\\brain",
                file=sys.stderr,
            )
            sys.exit(1)

        self.vault_root: Path = Path(root_str).expanduser().resolve()
        self.memory_md: Path = self.vault_root / "Memory.md"
        self.log_md: Path = self.vault_root / "log.md"
        self.raw_dir: Path = self.vault_root / "raw"
        self.wiki_dir: Path = self.vault_root / "wiki"
        self.llm_url: str = env.get("LOCAL_LLM_URL", _DEFAULT_LLM_URL).strip()
        self.llm_model: str = env.get("LOCAL_LLM_MODEL", _DEFAULT_LLM_MODEL).strip()

        def _int(key, default):
            try:
                return int(env.get(key, default))
            except ValueError:
                return default

        self.timeout_short: int = _int("LLM_TIMEOUT_SHORT", _DEFAULT_TIMEOUT_SHORT)
        self.timeout_medium: int = _int("LLM_TIMEOUT_MEDIUM", _DEFAULT_TIMEOUT_MEDIUM)
        self.timeout_long: int = _int("LLM_TIMEOUT_LONG", _DEFAULT_TIMEOUT_LONG)

    def ensure_dirs(self):
        for d in [
            self.raw_dir / "articles",
            self.raw_dir / "pdfs",
            self.raw_dir / "transcripts",
            self.raw_dir / "images",
            self.raw_dir / "notes",
            self.raw_dir / "chats",
            self.wiki_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


cfg = Config()
