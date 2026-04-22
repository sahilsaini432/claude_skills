#!/usr/bin/env python3
"""
ingest.py — Ingest any source file into the brain-wiki.

Usage:
    python scripts/ingest.py <source_file>
    python scripts/ingest.py --raw-chats-path   # print raw/chats/ path and exit

Supported types (auto-detected by extension):
    .md .txt .html     → Article / Note / Chat
    .pdf               → PDF (requires pymupdf)
    .jpg .jpeg .png .webp .gif → Image (gemma4 vision)
    .transcript .srt .vtt      → Transcript

Flow:
    1. Detect source type and extract text
    2. Classify topic (local model)
    3. Check for existing page with same slug in topic folder
       - New slug → generate fresh wiki page
       - Existing slug → merge new content into existing page (preserving Related Pages)
    4. Show preview for approval
    5. Write wiki page to wiki/<topic_folder>/<slug>-<date>.md
    6. Update / create topic _overview.md
    7. Update Memory.md and log.md
    8. Back-patch related pages in the same topic
    9. Extract entities → update registry → create/update entity pages
    10. Cross-link source page ↔ entity pages
"""

import argparse
import json
import re
import shutil
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
from llm import call_local
from wiki_index import (
    append_log,
    backpatch_file,
    get_topic_entries,
    insert_entry,
    load_memory,
    slugify,
    posix_rel,
)
from entities import (
    extract_entities,
    process_entities,
    link_entity_pages_to_source,
    link_source_to_entity_pages,
)


# ── Model warm-up ─────────────────────────────────────────────────────────────


def _ollama_base_url() -> str:
    """Derive the Ollama base URL from the generate endpoint."""
    # e.g. http://192.168.1.5:11434/api/generate -> http://192.168.1.5:11434
    url = cfg.llm_url
    if "/api/" in url:
        return url.split("/api/")[0]
    return "http://localhost:11434"


def unload_model() -> bool:
    """Unload the model from GPU memory via Ollama API (keep_alive=0).
    This forces a clean reload on the next ping.
    Returns True on success, False if the unload call failed (non-fatal).
    """
    import urllib.request, urllib.error

    base = _ollama_base_url()
    url = f"{base}/api/generate"
    payload = json.dumps(
        {
            "model": cfg.llm_model,
            "prompt": "",
            "keep_alive": 0,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print(f"  Model unloaded from GPU memory.")
        return True
    except Exception as e:
        # Non-fatal — model may not have been loaded, or Ollama version doesn't support it
        print(f"  [warn] Could not unload model (non-fatal): {e}", file=sys.stderr)
        return False


def ping_model() -> bool:
    """Unload then reload the model to guarantee a clean warm-up.
    1. Sends keep_alive=0 to evict the model from VRAM
    2. Sends a minimal prompt to force a fresh load
    Blocks until the model responds — may take up to 15 mins on cold start.
    Returns True on success, False on failure.
    """
    import urllib.request, urllib.error

    # Step 1 — unload
    print("  Unloading model from GPU memory...")
    unload_model()

    # Step 2 — reload via ping
    print("  Loading model... (may take up to 15 mins on first load)")
    payload = json.dumps(
        {
            "model": cfg.llm_model,
            "prompt": "ping",
            "system": "Reply with only the word: pong",
            "stream": False,
            "keep_alive": -1,
            "options": {"temperature": 0.0, "num_predict": 5},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        cfg.llm_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_long) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            response = result.get("response", "").strip()
            print(f"  Model ready. (response: {response!r})")
            return True
    except Exception as e:
        print(f"  [error] Ping failed: {e}", file=sys.stderr)
        return False


# ── Prompts ───────────────────────────────────────────────────────────────────

CLASSIFY_SYSTEM = """\
You are a precise topic classifier. Given source content and the current Memory.md index, decide:
- Which existing topic group best fits (or propose a short new one, Title Case, 2–4 words)
- A one-line description of this source (max 12 words)
- A 2–5 word kebab-case filename slug (NO date — just the topic words)

Return ONLY valid JSON, no fences:
{
  "topic": "Topic Name",
  "is_new_topic": true or false,
  "description": "One-line description",
  "slug": "kebab-case-slug"
}
"""

WIKI_PAGE_SYSTEM = """\
You are building a personal knowledge wiki. Given source content, write a structured wiki page.

Return ONLY markdown — no fences, no preamble.

# <Title>

**Source:** <filename or URL>
**Date ingested:** <today>
**Type:** <Article | PDF | Image | Transcript | Note | Chat>

---

## Summary
<3–5 sentence synthesis of the key ideas>

## Key Points
- <point 1>
- <point 2>

## Concepts & Entities
<Notable people, tools, frameworks, ideas — one line each>

## Quotes / Highlights
<1–3 notable direct quotes or data points. Omit if none.>

## Connections
<How this source relates to things you likely already know>

## Related Pages
<Leave blank — will be filled by back-patching>

---
*Ingested by brain-wiki*
"""

WIKI_PAGE_WITH_RELATED_SYSTEM = """\
You are building a personal knowledge wiki. Given source content and related existing pages, write a structured wiki page.

Return ONLY markdown — no fences, no preamble.

# <Title>

**Source:** <filename or URL>
**Date ingested:** <today>
**Type:** <Article | PDF | Image | Transcript | Note | Chat>

---

## Summary
<3–5 sentence synthesis of the key ideas>

## Key Points
- <point 1>
- <point 2>

## Concepts & Entities
<Notable people, tools, frameworks, ideas — one line each>

## Quotes / Highlights
<1–3 notable direct quotes or data points. Omit if none.>

## Connections
<How this source relates to things you likely already know>

## Related Pages
<Use the related pages list to write markdown links. Display text = slug only, no date.
- [slug-without-date](relative/path.md) — one sentence on the connection
Order chronologically if dates are available.>

---
*Ingested by brain-wiki*
"""

MERGE_SYSTEM = """\
You are updating an existing wiki page with new information from a follow-up session.

Rules — strictly follow these:
1. Expand and update ## Summary, ## Key Points, ## Concepts & Entities, ## Connections
   with new information from the new session. Do not remove existing content — only add or refine.
2. Update **Date ingested** to show both dates: "first-date / updated-date"
3. Append new **Source** filenames to the existing Source line (comma-separated)
4. PRESERVE the ## Related Pages section EXACTLY as-is — do not add, remove, or reword any links
5. PRESERVE the ## Quotes / Highlights section — only add new quotes, never remove existing ones
6. Return the COMPLETE updated file — no truncation, no fences, no commentary
"""

OVERVIEW_SYSTEM = """\
You are maintaining a topic overview page in a personal knowledge wiki.
You will receive the current _overview.md and a page just added or updated.
Update the overview: revise the synthesis, update the page list, note new contradictions or connections.
Return the COMPLETE updated _overview.md — no fences, no preamble.
"""

OVERVIEW_INIT_SYSTEM = """\
You are creating a new topic overview page for a personal knowledge wiki.
Return ONLY markdown — no fences, no preamble.

# <Topic Name>

## What this topic covers
<2–3 sentence description>

## Pages
- [slug](filename.md) — one-line description

## Evolving Thesis
<Running synthesis — stub for now, updated as pages are added.>

---
*Managed by brain-wiki*
"""


# ── Source readers ─────────────────────────────────────────────────────────────


def read_source(source_path: Path) -> tuple[str, str]:
    """Returns (content_text, source_type)."""
    ext = source_path.suffix.lower()

    if ext in {".md", ".txt", ".html"}:
        text = source_path.read_text(encoding="utf-8", errors="replace")
        if ext in {".txt", ".md"}:
            sample = text[:2000]
            if (
                ("USER:" in sample and "ASSISTANT:" in sample)
                or ("**User**" in sample and "**Assistant**" in sample)
                or ("**Human**" in sample and "**Claude**" in sample)
            ):
                return text, "Chat"
        return text, "Note" if ext == ".txt" else "Article"

    if ext == ".pdf":
        return _read_pdf(source_path), "PDF"

    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return _read_image(source_path), "Image"

    if ext in {".transcript", ".srt", ".vtt"}:
        return _clean_transcript(source_path.read_text(encoding="utf-8", errors="replace")), "Transcript"

    try:
        return source_path.read_text(encoding="utf-8", errors="replace"), "Note"
    except Exception as e:
        print(f"Error reading {source_path}: {e}", file=sys.stderr)
        sys.exit(1)


def _read_pdf(path: Path) -> str:
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(str(path))
        return "\n\n".join(page.get_text() for page in doc)
    except ImportError:
        print("pymupdf not installed. Run:  pip install pymupdf", file=sys.stderr)
        sys.exit(1)


def _read_image(path: Path) -> str:
    import base64
    import json as _json
    import urllib.request as _req

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}
    mime = "image/" + ext_map.get(path.suffix.lower(), "jpeg")

    payload = {
        "model": cfg.llm_model,
        "prompt": (
            "Describe this image in detail. Extract any visible text. "
            "Note key concepts, entities, data, or information present."
        ),
        "images": [b64],
        "stream": False,
    }
    data = _json.dumps(payload).encode()
    request = _req.Request(
        cfg.llm_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _req.urlopen(request, timeout=cfg.timeout_short) as resp:
        return _json.loads(resp.read())["response"].strip()


def _clean_transcript(text: str) -> str:
    text = re.sub(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}", "", text)
    text = re.sub(r"\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}\.\d{3}", "", text)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^WEBVTT.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_url(url: str) -> str:
    """Fetch URL and return raw HTML string."""
    import urllib.request as _ur

    req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _ur.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _strip_html(html: str) -> str:
    """Strip HTML tags; return clean readable text."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self._skip = False
            self.parts: list[str] = []

        def handle_starttag(self, tag, _attrs):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    s = _Stripper()
    s.feed(html)
    return re.sub(r"\s+", " ", " ".join(s.parts)).strip()


def _url_to_filename(url: str, today: str) -> str:
    """Derive a filesystem-safe archive filename from a URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "").replace(".", "-")
    path_slug = re.sub(r"[^a-z0-9]+", "-", parsed.path.lower()).strip("-") or "index"
    name = f"{domain}-{path_slug}-{today}"
    return name[:120] + ".html"


# ── Classify ──────────────────────────────────────────────────────────────────


def classify(content: str, memory_text: str, source_name: str) -> dict:
    prompt = (
        f"Source filename: {source_name}\n\n"
        f"Source content (first 3000 chars):\n{content[:3000]}\n\n"
        f"Current Memory.md:\n{memory_text}"
    )
    raw = call_local(prompt, CLASSIFY_SYSTEM, timeout=cfg.timeout_short)
    raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {
            "topic": "Uncategorized",
            "is_new_topic": True,
            "description": source_name,
            "slug": re.sub(r"[^a-z0-9]+", "-", source_name.lower())[:40],
        }


# ── Duplicate detection ───────────────────────────────────────────────────────


def find_existing_page(topic_dir: Path, slug: str) -> Path | None:
    """Return the first existing page whose filename starts with slug-, or None."""
    if not topic_dir.exists():
        return None
    for f in topic_dir.glob(f"{slug}-*.md"):
        if f.name != "_overview.md":
            return f
    return None


# ── Write or merge wiki page ──────────────────────────────────────────────────


def write_wiki_page(
    content: str,
    source_type: str,
    source_name: str,
    related_entries: list[dict],
    today: str,
) -> str:
    related_block = "\n".join(f"  {e['path']}|{e['description']}" for e in related_entries)
    base = f"Source type: {source_type}\n" f"Source name: {source_name}\n" f"Today: {today}\n\n"
    if related_entries:
        prompt = (
            base
            + f"Related existing pages (path|description):\n{related_block}\n\n"
            + f"Source content:\n{content[:8000]}"
        )
        return call_local(prompt, WIKI_PAGE_WITH_RELATED_SYSTEM, timeout=cfg.timeout_long)
    else:
        prompt = base + f"Source content:\n{content[:8000]}"
        return call_local(prompt, WIKI_PAGE_SYSTEM, timeout=cfg.timeout_long)


def merge_wiki_page(
    existing_content: str,
    new_content: str,
    source_name: str,
    today: str,
) -> str:
    """Merge new session content into existing page, preserving Related Pages."""
    prompt = (
        f"Existing wiki page:\n\n{existing_content}\n\n"
        f"New session source: {source_name}\n"
        f"Today: {today}\n\n"
        f"New session content:\n{new_content[:8000]}"
    )
    return call_local(prompt, MERGE_SYSTEM, timeout=cfg.timeout_long)


# ── Overview ──────────────────────────────────────────────────────────────────


def update_overview(overview_path: Path, page_content: str, topic: str):
    if overview_path.exists():
        current = overview_path.read_text(encoding="utf-8")
        prompt = (
            f"Current _overview.md:\n\n{current}\n\n"
            f"Page just added/updated in topic '{topic}':\n\n{page_content[:3000]}"
        )
        updated = call_local(prompt, OVERVIEW_SYSTEM, timeout=cfg.timeout_long)
    else:
        prompt = f"Topic name: {topic}\n\nFirst page content:\n\n{page_content[:3000]}"
        updated = call_local(prompt, OVERVIEW_INIT_SYSTEM, timeout=cfg.timeout_long)
    overview_path.write_text(updated, encoding="utf-8")
    print("  [ok] _overview.md updated")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Ingest a source into brain-wiki")
    parser.add_argument("source", nargs="?", help="Path to source file")
    parser.add_argument("--raw-chats-path", action="store_true", help="Print the raw/chats/ path and exit")
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt and auto-approve (for Claude Code)"
    )
    parser.add_argument(
        "--no-ping", action="store_true", help="Skip model warm-up entirely (use if model is already loaded)"
    )
    parser.add_argument(
        "--no-unload", action="store_true", help="Ping to warm up but skip the unload step first"
    )

    # Default mode is claude-chat (Claude Code synthesizes the page, zero Ollama calls).
    # Pass --ollama to use the local LLM pipeline instead (overnight batch, no Claude Code).
    parser.add_argument(
        "--ollama",
        action="store_true",
        help=(
            "Use the local Ollama pipeline for synthesis, merging, entities, and back-patching. "
            "Default mode is claude-chat (zero Ollama calls — Claude Code writes the page)."
        ),
    )
    # Back-compat: --claude-chat is now the default. Accept the flag but it's a no-op.
    parser.add_argument(
        "--claude-chat",
        action="store_true",
        help="[deprecated — now the default] Kept for backward compatibility.",
    )
    parser.add_argument(
        "--page-content-file",
        help="Path to a file containing the wiki page markdown (written by Claude Code in phase 2)",
    )
    parser.add_argument(
        "--entities-file",
        help="Path to a JSON file with extracted entities [{name,slug,description,type},...] (phase 2)",
    )
    parser.add_argument("--topic", help="Topic name (required in claude-chat phase 2)")
    parser.add_argument("--slug", help="Page slug (required in claude-chat phase 2)")
    parser.add_argument("--description", help="One-line description (required in claude-chat phase 2)")
    args = parser.parse_args()

    # claude-chat is the default; --ollama opts out.
    use_claude_chat = not args.ollama

    cfg.ensure_dirs()

    if args.raw_chats_path:
        print(cfg.raw_dir / "chats")
        sys.exit(0)

    if not args.source:
        parser.print_help()
        sys.exit(1)

    today = date.today().isoformat()
    is_url = str(args.source or "").startswith(("http://", "https://"))

    if is_url:
        url = args.source
        print(f"\n[ingest] Fetching URL: {url}", file=sys.stderr)
        _raw_html = _fetch_url(url)
        _prefetched_content = _strip_html(_raw_html)
        _prefetched_type = "Article"
        _url_fname = _url_to_filename(url, today)
        _url_raw_dest = cfg.raw_dir / "articles" / _url_fname
        _url_raw_dest.parent.mkdir(parents=True, exist_ok=True)
        _url_raw_dest.write_text(_raw_html, encoding="utf-8")
        print(f"  [ok] Archived to raw/articles/{_url_fname}", file=sys.stderr)
        source_path = _url_raw_dest
        source_name = url
    else:
        source_path = Path(args.source).resolve()
        if not source_path.exists():
            print(f"Error: source file not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        source_name = source_path.name
        _prefetched_content = None
        _prefetched_type = None

    # ── --claude-chat PHASE 1 ─────────────────────────────────────────────────
    # No Ollama at all. Read the source, print a structured synthesis prompt,
    # and exit with code 2 so Claude Code knows to proceed to phase 2.
    if use_claude_chat and not args.page_content_file:
        print(f"\n[ingest --claude-chat] Phase 1 — reading source: {source_name}")
        if _prefetched_content is not None:
            content, source_type = _prefetched_content, _prefetched_type
        else:
            content, source_type = read_source(source_path)
        memory_text = load_memory(cfg.memory_md)

        # Heuristic slug from filename (Claude Code may override)
        auto_slug = re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower())[:40]
        auto_slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", auto_slug) or auto_slug

        # Check if a page with this slug already exists (merge hint)
        existing_page_hint = None
        for topic_dir in cfg.wiki_dir.iterdir():
            if topic_dir.is_dir():
                hit = find_existing_page(topic_dir, auto_slug)
                if hit:
                    existing_page_hint = str(hit.relative_to(cfg.vault_root))
                    break

        print("\n" + "=" * 70)
        print("BRAIN-WIKI CLAUDE-CHAT PHASE 1")
        print("=" * 70)
        print(f"SOURCE_NAME: {source_name}")
        print(f"SOURCE_TYPE: {source_type}")
        print(f"SOURCE_PATH: {source_path}")
        print(f"TODAY: {today}")
        print(f"AUTO_SLUG: {auto_slug}")
        print(f"EXISTING_PAGE: {existing_page_hint or 'none'}")
        print("MEMORY_MD_EXCERPT:")
        print(memory_text[:3000])
        print("SOURCE_CONTENT:")
        print(content[:12000])
        print("=" * 70)
        print(
            "\nINSTRUCTIONS FOR CLAUDE CODE:\n"
            "1. Read SOURCE_CONTENT above.\n"
            "2. Classify: pick or propose a topic from MEMORY_MD_EXCERPT, write a\n"
            "   one-line description (<=12 words), and confirm or revise AUTO_SLUG.\n"
            "3. If EXISTING_PAGE is set, merge new content into that page.\n"
            "   Otherwise write a fresh wiki page using this schema:\n"
            "     # Title\n"
            "     **Source:** ... | **Date ingested:** ... | **Type:** ...\n"
            "     ---\n"
            "     ## Summary\n"
            "     ## Key Points\n"
            "     ## Concepts & Entities\n"
            "     ## Quotes / Highlights\n"
            "     ## Connections\n"
            "     ## Related Pages  <- leave blank\n"
            "     ---\n"
            "     *Ingested by brain-wiki*\n"
            "4. Extract 3-8 significant entities (tools, frameworks, people, concepts).\n"
            "5. Write the wiki page markdown to a temp file.\n"
            "6. Write the entities as JSON to a temp file:\n"
            '   [{"name": ..., "slug": ..., "description": ..., "type": ...}, ...]\n'
            "7. Re-run ingest.py with:\n"
            "     python3 ingest.py SOURCE_PATH --claude-chat --yes \\\n"
            "       --page-content-file <wiki_page_tmp> \\\n"
            "       --entities-file <entities_tmp> \\\n"
            "       --topic 'Topic Name' \\\n"
            "       --slug 'your-slug' \\\n"
            "       --description 'one-line description'\n"
        )
        print("=" * 70)
        sys.exit(2)  # Sentinel: Claude Code must proceed to phase 2

    # ── --claude-chat PHASE 2 ─────────────────────────────────────────────────
    # Claude Code passes back the synthesized page via --page-content-file.
    # We skip ALL Ollama calls and go straight to writing everything to disk.
    if use_claude_chat and args.page_content_file:
        # These args are required in phase 2
        if not args.topic or not args.slug or not args.description:
            print(
                "Error: --claude-chat phase 2 requires --topic, --slug, and --description",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Normal Ollama warm-up (skipped entirely in --claude-chat) ─────────────
    if not use_claude_chat and not args.no_ping:
        if args.no_unload:
            # Skip eviction — just ping to ensure model is loaded
            print("  Skipping unload — pinging model directly...")
            import urllib.request as _ur

            _payload = json.dumps(
                {
                    "model": cfg.llm_model,
                    "prompt": "ping",
                    "system": "Reply with only the word: pong",
                    "stream": False,
                    "keep_alive": -1,
                    "options": {"temperature": 0.0, "num_predict": 5},
                }
            ).encode("utf-8")
            _req = _ur.Request(
                cfg.llm_url, data=_payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with _ur.urlopen(_req, timeout=cfg.timeout_long) as _resp:
                    _r = json.loads(_resp.read())
                    print(f"  Model ready. (response: {_r.get('response','').strip()!r})")
            except Exception as _e:
                print(f"Error: could not reach local model: {_e}", file=sys.stderr)
                sys.exit(1)
        else:
            if not ping_model():
                print("Error: could not reach local model. Check Ollama is running.", file=sys.stderr)
                sys.exit(1)

    print(f"\n[ingest] Ingesting: {source_name}")

    # 1. Read source
    print("  Reading source...")
    if _prefetched_content is not None:
        content, source_type = _prefetched_content, _prefetched_type
    else:
        content, source_type = read_source(source_path)
    memory_text = load_memory(cfg.memory_md)

    # ── CLASSIFY & GENERATE: branch on --claude-chat ──────────────────────────

    if use_claude_chat:
        # Phase 2: Claude Code has already synthesized the wiki page.
        # Use the values passed via CLI flags — no LLM calls needed.
        topic = args.topic
        slug = args.slug
        description = args.description
        topic_folder = slugify(topic)
        topic_dir = cfg.wiki_dir / topic_folder

        print(f"  → Topic:  {topic!r} (claude-chat, no classify)")
        print(f"  → Slug:   {slug}")

        # Read the wiki page content from the file Claude Code wrote
        page_content_path = Path(args.page_content_file).resolve()
        if not page_content_path.exists():
            print(f"Error: --page-content-file not found: {page_content_path}", file=sys.stderr)
            sys.exit(1)
        wiki_page_content = page_content_path.read_text(encoding="utf-8").strip()

        # Determine if this is a merge (existing page with same slug)
        existing_page = find_existing_page(topic_dir, slug)
        is_merge = existing_page is not None
        if is_merge:
            print(
                f"  → Existing page found: {existing_page.name} — treating as merge (Claude Code pre-merged)"
            )
            wiki_page_path = existing_page
        else:
            print(f"  → New page: wiki/{topic_folder}/{slug}-{today}.md")
            wiki_page_path = topic_dir / f"{slug}-{today}.md"

        existing_entries = get_topic_entries(memory_text, topic)
        if is_merge:
            existing_entries = [
                e for e in existing_entries if not Path(e["path"]).name.startswith(slug + "-")
            ]

    else:
        # Normal Ollama path
        # 2. Classify
        print("  Classifying topic...")
        classification = classify(content, memory_text, source_name)

        topic = classification.get("topic", "Uncategorized")
        description = classification.get("description", source_path.stem)
        is_new_topic = classification.get("is_new_topic", False)
        slug = classification.get("slug", re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower())[:40])
        topic_folder = slugify(topic)
        topic_dir = cfg.wiki_dir / topic_folder

        print(f"  → Topic:  {topic!r} ({'new' if is_new_topic else 'existing'})")
        print(f"  → Slug:   {slug}")

        # 3. Check for existing page with same slug
        existing_page = find_existing_page(topic_dir, slug)
        is_merge = existing_page is not None

        if is_merge:
            print(f"  → Existing page found: {existing_page.name} — will merge")
        else:
            print(f"  → New page: wiki/{topic_folder}/{slug}-{today}.md")

        # 4. Get existing topic entries for cross-referencing
        existing_entries = get_topic_entries(memory_text, topic)
        if is_merge:
            existing_entries = [
                e for e in existing_entries if not Path(e["path"]).name.startswith(slug + "-")
            ]

        # 5. Generate or merge wiki page
        if is_merge:
            print("  Merging with existing page (local model)...")
            existing_content = existing_page.read_text(encoding="utf-8")
            wiki_page_content = merge_wiki_page(existing_content, content, source_name, today)
            wiki_page_path = existing_page
        else:
            print("  Generating wiki page (local model)...")
            wiki_page_content = write_wiki_page(content, source_type, source_name, existing_entries, today)
            wiki_page_path = topic_dir / f"{slug}-{today}.md"

    # 6. Preview
    print("\n" + "─" * 60)
    print(f"[preview] WIKI PAGE {'MERGE' if is_merge else 'PREVIEW'}")
    print("─" * 60)
    preview_lines = wiki_page_content.splitlines()[:40]
    print("\n".join(preview_lines))
    if len(wiki_page_content.splitlines()) > 40:
        print(f"\n  ... ({len(wiki_page_content.splitlines()) - 40} more lines)")
    print("─" * 60)
    print(f"\nTopic:   {topic}")
    print(f"File:    {wiki_page_path.relative_to(cfg.vault_root)}")
    print(f"Mode:    {'merge into existing' if is_merge else 'create new'}")
    print(f"Summary: {description}")

    if args.yes:
        print("\n[auto-approved via --yes]")
    else:
        answer = input("\nLooks good? [Y/n]: ").strip().lower()
        if answer == "n":
            print("Aborted -- nothing written.")
            sys.exit(0)

    # 7. Write page
    topic_dir.mkdir(parents=True, exist_ok=True)
    wiki_page_path.write_text(wiki_page_content, encoding="utf-8")
    print(f"\n  [ok] {'Merged' if is_merge else 'Written'}: {wiki_page_path.name}")

    # 8. Copy source to raw/ if not already there
    raw_type_map = {
        "Article": "articles",
        "Note": "notes",
        "PDF": "pdfs",
        "Image": "images",
        "Transcript": "transcripts",
        "Chat": "chats",
    }
    raw_subtype = raw_type_map.get(source_type, "notes")
    try:
        source_path.relative_to(cfg.raw_dir)
        already_in_raw = True
    except ValueError:
        already_in_raw = False

    if not already_in_raw:
        raw_dest = cfg.raw_dir / raw_subtype / source_path.name
        if raw_dest.exists():
            raw_dest = cfg.raw_dir / raw_subtype / f"{source_path.stem}-{today}{source_path.suffix}"
        shutil.copy2(source_path, raw_dest)
        print(f"  [ok] Source copied to raw/{raw_subtype}/")
    else:
        print("  [ok] Source already in raw/ — no copy needed")

    # 9. Update _overview.md
    if use_claude_chat:
        # No Ollama — do a minimal append-only update to the overview
        overview_path = topic_dir / "_overview.md"
        rel_page = posix_rel(wiki_page_path.relative_to(cfg.vault_root))
        if not overview_path.exists():
            overview_path.write_text(
                f"# {topic}\n\n"
                f"## What this topic covers\n"
                f"<!-- stub — update manually or run lint --fix -->\n\n"
                f"## Pages\n"
                f"- [{slug}]({rel_page}) — {description}\n\n"
                f"## Evolving Thesis\n"
                f"<!-- stub -->\n\n"
                f"---\n*Managed by brain-wiki*\n",
                encoding="utf-8",
            )
            print("  [ok] _overview.md created (stub)")
        else:
            # Append the new page to the Pages section if not already listed
            current = overview_path.read_text(encoding="utf-8")
            entry = f"- [{slug}]({rel_page}) — {description}"
            if slug not in current:
                # Insert after ## Pages line
                if "## Pages" in current:
                    current = current.replace(
                        "## Pages\n",
                        f"## Pages\n{entry}\n",
                        1,
                    )
                else:
                    current += f"\n## Pages\n{entry}\n"
                overview_path.write_text(current, encoding="utf-8")
            print("  [ok] _overview.md updated (append-only, no LLM)")
    else:
        update_overview(topic_dir / "_overview.md", wiki_page_content, topic)

    # 10. Update Memory.md — use slug as display text (no date), keep dated filename
    rel_from_memory = posix_rel(wiki_page_path.relative_to(cfg.vault_root))
    if not is_merge:
        memory_entry = f"- [{slug}]({rel_from_memory}) — {description}"
        updated_memory = insert_entry(memory_text, topic, memory_entry, today)
        cfg.memory_md.write_text(updated_memory, encoding="utf-8")
        print("  [ok] Memory.md updated")
    else:
        print("  [ok] Memory.md unchanged (merge into existing page)")

    # 11. Update log.md
    action = "merge" if is_merge else "ingest"
    append_log(cfg.log_md, action, f"{source_name} → {wiki_page_path.name}")

    # 12. Back-patch cross-references (only for new pages, not merges)
    if not is_merge and existing_entries:
        if use_claude_chat:
            # In --claude-chat mode we skip Ollama-powered back-patching.
            # Instead, print the pages that need cross-refs so Claude Code can
            # manually append the link if desired.
            print(f"\n  [claude-chat] Skipping Ollama back-patch.")
            print(f"  The following existing pages should reference this new page:")
            for ex in existing_entries:
                print(f"    - {ex['path']} — {ex.get('description', '')}")
            print(f"  New page should reference:")
            for ex in existing_entries:
                print(f"    - {ex['path']}")
        else:
            print(f"\n  Cross-referencing {len(existing_entries)} existing page(s)...")
            for ex in existing_entries:
                ex_path = (cfg.vault_root / ex["path"]).resolve()
                try:
                    rel = wiki_page_path.relative_to(ex_path.parent)
                except ValueError:
                    rel = wiki_page_path
                # Display text = slug only, no date
                entry_for_old = f"- [{slug}]({posix_rel(rel)}) — {description}"
                backpatch_file(ex_path, entry_for_old, call_local, timeout=cfg.timeout_long)

            for ex in existing_entries:
                ex_path = (cfg.vault_root / ex["path"]).resolve()
                try:
                    rel = ex_path.relative_to(wiki_page_path.parent)
                except ValueError:
                    rel = ex_path
                # Display text = slug only (strip date from stem)
                ex_slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", Path(ex["path"]).stem)
                entry_for_new = f"- [{ex_slug}]({posix_rel(rel)}) — {ex['description']}"
                backpatch_file(wiki_page_path, entry_for_new, call_local, timeout=cfg.timeout_long)

    # 13. Entity extraction and page management
    if use_claude_chat:
        # Entities were extracted by Claude Code and passed via --entities-file
        entities = []
        if args.entities_file:
            entities_path = Path(args.entities_file).resolve()
            if entities_path.exists():
                try:
                    entities = json.loads(entities_path.read_text(encoding="utf-8"))
                    print(f"\n  Loaded {len(entities)} entities from --entities-file")
                except Exception as e:
                    print(f"  [warn] Could not parse --entities-file: {e}", file=sys.stderr)
            else:
                print(f"  [warn] --entities-file not found: {entities_path}", file=sys.stderr)
        else:
            print("\n  No --entities-file provided — skipping entity tracking")
    else:
        print("\n  Extracting entities (local model)...")
        entities = extract_entities(content, source_name)

    if entities:
        print(f"  Found {len(entities)} entities: {', '.join(e['name'] for e in entities)}")
        entity_pages = process_entities(
            entities,
            content,
            slug,
            wiki_page_path,
            today,
        )
        # Cross-link source page <-> entity pages
        if entity_pages:
            print(f"  Linking {len(entity_pages)} entity page(s)...")
            link_entity_pages_to_source(
                wiki_page_path,
                entity_pages,
                call_local,
                timeout=cfg.timeout_long,
            )
            link_source_to_entity_pages(
                wiki_page_path,
                slug,
                description,
                entity_pages,
                call_local,
                timeout=cfg.timeout_long,
            )
    else:
        print("  No significant entities found.")

    print(f"\n[done] Done — {topic} / {wiki_page_path.name}")

    # Unload model from VRAM now that ingest is complete
    if not args.no_ping:  # only unload if we loaded it ourselves
        print("  Releasing model from VRAM...")
        if unload_model():
            print("  Model unloaded.")


if __name__ == "__main__":
    main()
