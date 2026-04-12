# Operations Reference

## ingest

**Script:** `scripts/ingest.py <source_file>`

### Flow
1. **Read source** — detected by file extension, routed to the right reader
2. **Classify** — `gemma4:31b` reads the content + `Memory.md` and returns:
   - `topic` — existing or new topic group name
   - `description` — one-line summary (≤12 words)
   - `slug` — 2–5 word kebab-case filename base
3. **Generate wiki page** — `gemma4:31b` writes a structured page including Summary,
   Key Points, Concepts & Entities, Quotes/Highlights, Connections, Related Pages
4. **Show preview** — first 40 lines shown, user confirms [Y/n/edit]
5. **Write to disk** — `wiki/<topic-folder>/<slug>-YYYY-MM-DD.md`
6. **Copy source to raw/** — source file copied to `raw/<type>/` (immutable archive)
7. **Update `_overview.md`** — topic synthesis page created or revised
8. **Update `Memory.md`** — new entry under correct `## Topic` heading
9. **Append to `log.md`** — `## [YYYY-MM-DD HH:MM] ingest | filename → wiki/...`
10. **Back-patch** — existing pages in the same topic get the new page added to their
    `## Related Pages`; new page gets back-references to all existing ones

---

## query

**Script:** `scripts/query.py "question" [--save <answer_file>]`

### Flow
1. **Find relevant topics** — `gemma4:31b` reads `Memory.md` and identifies which
   topic sections are likely to contain the answer
2. **Load pages** — all pages from relevant topics loaded (capped at ~2000 chars each)
   plus topic `_overview.md` files
4. **Print answer** — displayed in terminal
5. **Optionally save** — prompted to save; if yes, writes to `wiki/queries-and-synthesis/`
   and updates `Memory.md`

### Flags
```

--save     skip the prompt and always save the answer
```

### When the wiki can't answer
If no relevant pages are found, the script says so clearly and suggests:
- Which sources to ingest to fill the gap
- Whether a web search could help (you decide, the script doesn't search)

---

## lint

**Script:** `scripts/lint.py [--fix]`

### Checks
1. **Dead links** — entries in `Memory.md` pointing to missing files
2. **Orphan pages** — files in `wiki/` not indexed in `Memory.md`
3. **Missing `_overview.md`** — topic folders with pages but no overview
4. **Missing cross-references** — pages in the same topic not linked to each other
5. **Contradiction scan** — `gemma4:31b` reads all pages per topic and flags:
   - Factual contradictions between pages
   - Claims superseded by newer pages
   - Important concepts mentioned but lacking their own page
   - Knowledge gaps worth filling

### Flags
```
--fix    auto-fix: creates missing _overview.md files,
         back-patches missing cross-references
```

Contradictions and knowledge gaps are reported only — you decide what to do.
