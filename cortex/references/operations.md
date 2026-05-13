# Operations Reference

All synthesis is done by Claude Code via two-phase prompts. The Python scripts
do not call any LLM — they only handle file IO, parsing, and deterministic
linking.

## ingest

**Script:** `scripts/ingest.py <source_file>`

### Flow
1. **Phase 1 — read source** — file extension routes to the right reader
   (text/PDF/transcript/HTML; for images, a placeholder tells Claude Code to
   read the file with its Read tool)
2. **Phase 1 — print prompt, exit 2** — prints `cortex PHASE 1` block with
   `SOURCE_NAME`, `SOURCE_TYPE`, `SOURCE_PATH`, `TODAY`, `AUTO_SLUG`,
   `EXISTING_PAGE`, `MEMORY_MD_EXCERPT`, `SOURCE_CONTENT`, and instructions
3. **Claude Code synthesizes** — classifies, writes wiki page + entities to
   temp files
4. **Phase 2 — re-run** — `--page-content-file`, `--entities-file`,
   `--topic`, `--slug`, `--description`
5. **Show preview** — first 40 lines shown; auto-approves with `--yes`
6. **Write to disk** — `wiki/<topic-folder>/<slug>-YYYY-MM-DD.md`
7. **Copy source to raw/** — source file copied to `raw/<type>/` (immutable archive)
8. **Update `_overview.md`** — append-only stub created or page added to ## Pages
9. **Update `Memory.md`** — new entry under correct `## Topic` heading (per-topic),
   master Memory.md gets a topic link if missing
10. **Append to `log.md`** — `## [YYYY-MM-DD HH:MM] ingest | filename → wiki/...`
11. **Back-patch** — existing pages in the same topic get the new page added to their
    `## Related Pages`; new page gets back-references to all existing ones
    (deterministic — no LLM)
12. **Process entities** — registry updated; stub entity page created on first
    appearance; counts bumped on subsequent appearances; entity ↔ source page
    cross-links added

---

## query

**Script:** `scripts/query.py "question" [--save <answer_file>]`

### Flow
1. **Print master Memory.md**
2. **Print every per-topic Memory.md and _overview.md**
3. **Print instructions** — Claude Code reads the relevant pages with its Read
   tool and synthesizes the answer in chat

### Flags
```
--save <file>   File a pre-written answer file (markdown) into
                wiki/queries-and-synthesis/ verbatim
```

### When the wiki can't answer
If no topics are indexed yet, the script says so. If pages don't cover the
question, Claude Code says so in its synthesis.

---

## lint

**Script:** `scripts/lint.py [--fix]`

### Checks
1. **Dead links** — entries in `Memory.md` pointing to missing files
2. **Orphan pages** — files in `wiki/` not indexed in `Memory.md`
3. **Missing `_overview.md`** — topic folders with pages but no overview
4. **Missing cross-references** — pages in the same topic not linked to each other
5. **Entity registry consistency** — entities seen 2+ times with no page,
   pages with no registry entry

### Flags
```
--fix    Auto-fix: creates missing _overview.md stubs and adds orphan pages
         to the right topic Memory.md
```

Cross-reference issues are handled by re-ingesting the source — the ingest
pipeline owns Related Pages.

---

## entities --backfill

**Script:** `scripts/entities.py --backfill [--pages-json <file>]`

### Flow
1. **Phase 1** — collect every registry entry whose page is missing or still a
   stub; print `cortex CLAUDE-CHAT BACKFILL PHASE 1` block listing each entity
   with its sources and excerpts; exit 2
2. **Claude Code synthesizes** one full entity page per entity using the schema
3. **Phase 2** — re-run with `--pages-json <file>` containing
   `{slug: full_markdown, ...}`; the script writes each page to
   `wiki/_entities/<slug>.md`, overwriting any stub
