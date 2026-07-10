# Claude Code Prompt — iMessage Insights Bug Fix + GCS Feature

Use this prompt in Claude Code (or Cursor Agent) to implement the same changes on `pattersongrant/imsg`.

---

## Context

**Repository:** https://github.com/pattersongrant/imsg  
**App:** Local Flask web app that reads macOS `~/Library/Messages/chat.db` and shows top contacts, topics/keywords, activity charts, and per-contact drill-downs. All processing is local.

**Stack:** Python 3.10+, Flask, vanilla JS frontend (Chart.js + D3), SQLite read-only access to `chat.db`.

---

## Task 1 — Fix reaction/tapback pollution in top keywords (BUG)

### Problem
iMessage tapbacks (Loved, Liked, Disliked, Laughed at, Emphasized, Questioned) appear as separate rows in the `message` table. Their decoded text (e.g. `Loved "hey there"`) leaks into keyword/topic analysis, so words like **loved**, **liked**, **emphasized** dominate the "Top words" UI.

### Root cause
1. `imsg/analyze.py` tokenizes all message text without filtering tapback prefixes or reaction vocabulary.
2. `imsg/db.py` includes reaction rows in message counts and topic sampling because it only filters on `text IS NOT NULL OR attributedBody IS NOT NULL`.

### iMessage schema facts
- Reaction messages use `associated_message_type` values **2000–2006** (tapbacks) and **3000–3006** (reaction removals).
- Reaction text often looks like: `Loved "original message"`, `Liked "..."`, etc.

### Required fix

1. **Create `imsg/reactions.py`** with:
   - `is_reaction_type(associated_message_type)` — true for 2000–3006
   - `looks_like_reaction_text(text)` — regex match on tapback prefixes
   - `is_reaction_message(text, associated_message_type=None)`
   - `strip_reaction_prefix(text)` — remove prefix and surrounding quotes
   - `REACTION_WORDS` frozenset: loved, liked, disliked, laughed, emphasized, questioned, reacted

2. **Update `imsg/db.py`:**
   - Add `_content_filter(conn)` that excludes `associated_message_type BETWEEN 2000 AND 3006` when the column exists (graceful fallback for older schemas).
   - Use `_content_filter` in `iter_messages`, `contact_stats`, `group_chat_stats`, `monthly_activity`, `database_summary`.
   - In `message_text()`, return `None` for reaction rows (by type or text prefix).

3. **Update `imsg/analyze.py`:**
   - Skip pure reaction messages in `tokenize()`.
   - Strip reaction prefix before tokenizing when text embeds quoted original content.
   - Exclude `REACTION_WORDS` from token counts as a safety net.

4. **Tests** (`tests/test_imsg.py`):
   - Reaction prefixes are detected.
   - `top_words()` does not rank loved/liked/emphasized from tapback strings.
   - In-memory SQLite fixture: reaction rows excluded from `contact_stats` and `iter_messages`.

### Verification
```bash
python -c "
from imsg import analyze
texts = ['Loved \"hey\"', 'Liked \"ok\"', 'what time is dinner']
assert 'loved' not in {w['word'] for w in analyze.top_words(texts)}
print('ok')
"
pytest tests/test_imsg.py -k reaction
```

---

## Task 2 — GCS indexing for individual text amounts (FEATURE)

### Goal
Index exported iMessage rows stored in **Google Cloud Storage** and merge **per-person message counts** (sent/received/total) with local `chat.db` stats. This supports users who back up message exports to GCS (e.g. from other devices or shared pipelines).

### Required implementation

1. **Create `imsg/gcs.py`** with:
   - Optional import of `google.cloud.storage` (clear error if missing).
   - Env config: `GCS_BUCKET` (required), `GCS_PREFIX` (optional), standard `GOOGLE_APPLICATION_CREDENTIALS`.
   - `parse_export_bytes(data)` — parse `.json`, `.jsonl`, `.ndjson` exports.
   - Expected row shape: `{handle|contact, text, is_from_me, date?, name?}`.
   - `aggregate_messages()` → per-handle `{total, sent, received, last_date}`.
   - `contact_stats_from_gcs()` → list of contact dicts compatible with existing API shape.
   - `merge_contact_stats(local, remote)` — merge by handle, sum counts, track `source` / `sources`.
   - `gcs_status()` — report configured/available/export file count for `/api/status`.

2. **Update `app.py`:**
   - Add `_load_contacts(..., merge_gcs=True)` helper.
   - `/api/contacts?gcs=1` merges local + GCS (default on when configured).
   - `/api/bubbles?gcs=1` uses merged DM stats.
   - `/api/gcs/contacts` — GCS-only endpoint.
   - `/api/status` includes `gcs` status block.

3. **Dependencies:** add `google-cloud-storage>=2.14.0` to `requirements.txt`.

4. **README:** document env vars, export JSON format, and API behavior.

5. **Tests:**
   - Parse JSON array and JSONL fixtures.
   - Aggregate counts per handle.
   - Merge local + remote stats without double-counting structure bugs.

### Export format example
```json
[
  {"handle": "+15551234567", "name": "Alex", "text": "hello", "is_from_me": true, "date": "2024-06-01T12:00:00"},
  {"handle": "+15551234567", "text": "hey!", "is_from_me": false, "date": "2024-06-01T12:01:00"}
]
```

---

## Task 3 — Testing & PR

### Test plan
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
python -m py_compile app.py imsg/*.py
# Manual (requires Full Disk Access):
# python app.py → verify Topics tab has no loved/liked in top words
# With GCS_BUCKET set → verify /api/status shows gcs.configured=true
```

### PR title
`Fix tapback keywords and add GCS export merging`

### PR body bullets
- Filter iMessage tapback/reaction rows from counts and keyword analysis
- Add GCS export indexer to merge per-person text amounts with local stats
- Add unit tests for reactions, DB filtering, and GCS parsing/merge

### Constraints
- Keep all processing local; GCS is opt-in via env vars only.
- Do not write to `chat.db`.
- Match existing code style (minimal comments, small focused modules).
- No unrelated refactors.

---

## Files to touch

| File | Action |
|------|--------|
| `imsg/reactions.py` | Create |
| `imsg/gcs.py` | Create |
| `imsg/analyze.py` | Modify |
| `imsg/db.py` | Modify |
| `app.py` | Modify |
| `tests/test_imsg.py` | Create |
| `requirements.txt` | Add google-cloud-storage |
| `requirements-dev.txt` | Add pytest |
| `README.md` | Document GCS + bug fix behavior |

---

## Acceptance criteria

- [ ] "loved", "liked", "emphasized" no longer appear in top keywords from tapbacks
- [ ] Reaction rows excluded from contact message totals
- [ ] GCS exports parsed and aggregated by handle
- [ ] Local + GCS stats merge correctly in `/api/contacts`
- [ ] All pytest tests pass
- [ ] PR opened against `main` on `pattersongrant/imsg`
