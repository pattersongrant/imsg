# Claude Code Task — Fix `attributedBody` decoding (only ~60 of 18,894 messages analyzed)

## Repository
`pattersongrant/imsg` — a local Flask web app that reads macOS `~/Library/Messages/chat.db` and shows top contacts, keywords/topics, activity, and per-contact drill-downs. All processing is local. Python 3.10+, Flask, vanilla JS frontend.

Work on branch `fix/reactions-and-gcs-indexing` (or a new branch off it).

---

## The bug

When you open a contact and click **"Deep index — scan all N messages"**, the UI reports something like:

> TOP WORDS (ALL MESSAGES) · **60 of 18,894 messages analyzed**
> Deep scan complete — every message analyzed.

Even in deep mode (no limit), only ~60 of ~18,894 messages produce any text. The word counts are therefore tiny and wrong (e.g. `6 babes`, `4 app`), when the real conversation has thousands of words.

This is **not** an intentional cap — the deep path already passes `limit=None`. The messages are being dropped during **text extraction**.

### Why this happens

On modern macOS, most iMessage rows store their text in the binary `message.attributedBody` column (an Apple `streamtyped` / `NSArchiver` blob), and the plain `message.text` column is `NULL`. Only a small number of (usually recent) rows have `message.text` populated.

The current decoder in `imsg/db.py` (`decode_attributed_body` / `_candidates_from_blob`) tries to extract text with regexes like:

```python
re.finditer(rb"NSString\x00+(.+?)\x00", blob, ...)
re.finditer(rb"[\x01\x02]\x0b([\x20-\x7E\u00A0-\uFFFF]{2,}?)\x00", blob)
# plus a "printable runs" last resort filtered by looks_like_message_text()
```

These patterns do **not** match the real `streamtyped` layout. In the real format the text is a **length-prefixed `NSString`**, e.g. the bytes after the `NSString` class marker are roughly `... NSString\x01\x94\x84\x01+<LEN><UTF-8 BYTES>`, where `<LEN>` is a variable-length integer (1 byte if `< 0x80`; `0x81` followed by 2 little-endian bytes for longer strings; `0x82` + 4 bytes for very long strings). There are no reliable `\x00` terminators to anchor on, so the regex approach fails and the only messages that survive are the handful with a populated `text` column plus a few that happen to pass the "printable runs" heuristic. That's the ~60.

### Root-cause summary
`decode_attributed_body()` cannot parse `streamtyped`-encoded `NSString` payloads, so ~99% of messages return `None` from `message_text()` and are skipped everywhere (counts, keywords, topics, deep scan).

---

## The fix

Replace the regex-based extraction with a real length-prefixed `streamtyped`/`NSArchiver` parser. Two acceptable approaches:

### Option A (preferred, no new deps): hand-rolled `streamtyped` parser
Add a robust parser that:
1. Locates the first `NSString` class marker in the blob.
2. Skips the class/version bytes to the `+` (`0x2b`) marker that precedes the length.
3. Reads the variable-length integer length:
   - byte `< 0x80` → that byte is the length
   - byte `== 0x81` → next **2** bytes, little-endian
   - byte `== 0x82` → next **4** bytes, little-endian
4. Decodes `length` UTF-8 bytes as the message text.
5. Handles the case where the string has multiple attribute runs (a message with links/mentions/styling can contain more than one `NSString`; take the **first/base** string, which is the full visible text).

Reference implementation (adapt/verify against real blobs — do not assume it is byte-perfect):

```python
def _streamtyped_string(blob: bytes) -> str | None:
    marker = b"NSString"
    idx = blob.find(marker)
    if idx == -1:
        return None
    p = idx + len(marker)
    plus = blob.find(b"\x2b", p, p + 12)  # '+' precedes the length
    if plus == -1:
        return None
    p = plus + 1
    if p >= len(blob):
        return None
    length = blob[p]; p += 1
    if length == 0x81:
        if p + 2 > len(blob):
            return None
        length = int.from_bytes(blob[p:p + 2], "little"); p += 2
    elif length == 0x82:
        if p + 4 > len(blob):
            return None
        length = int.from_bytes(blob[p:p + 4], "little"); p += 4
    raw = blob[p:p + length]
    if len(raw) < length:
        return None
    return raw.decode("utf-8", errors="replace")
```

Keep the existing printable-runs logic ONLY as a last-resort fallback if the structured parse returns `None`.

### Option B: use a maintained typedstream library
If the hand-rolled parser proves fragile on real data, add a dependency such as `pytypedstream` (imports as `typedstream`) and use it to decode the archive, then pull the `NSMutableAttributedString` / `NSString` value. If you add a dependency, add it to `requirements.txt` and guard the import so the app still runs if it's missing (fall back to Option A).

### Wiring
- Update `decode_attributed_body(blob)` in `imsg/db.py` to try the structured parse first, then fall back to the current heuristic.
- Keep the reaction/tapback filtering intact: `message_text()` must still return `None` for tapback rows (`associated_message_type` 2000–3006 and `Loved "…"`-style prefixes).
- `message_text()` already prefers a clean `text` column and falls back to `attributedBody`; keep that order.
- Do NOT change the SQL — the rows are already being selected; only extraction is broken.

### Performance
Deep scan now decodes ~18k blobs per contact. Make sure `_streamtyped_string` is allocation-light (slicing + one decode). If deep scans feel slow, that's acceptable for now (it's behind the explicit button), but avoid O(n²) scans of each blob.

---

## Files to change
| File | Change |
|------|--------|
| `imsg/db.py` | Add `_streamtyped_string()`; rewrite `decode_attributed_body()` to use it with heuristic fallback |
| `tests/test_imsg.py` | Add decoding tests (below) |
| `requirements.txt` | Only if you choose Option B |

---

## Test cases (add to `tests/test_imsg.py`)

Add a helper that builds a realistic `streamtyped` blob, then assert the decoder recovers the exact text.

```python
def _make_attributed_body(text: str) -> bytes:
    body = text.encode("utf-8")
    n = len(body)
    if n < 0x80:
        length = bytes([n])
    elif n < 0x1_0000:
        length = b"\x81" + n.to_bytes(2, "little")
    else:
        length = b"\x82" + n.to_bytes(4, "little")
    header = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
        b"\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84"
        b"\x0eNSString\x01\x94\x84\x01+"
    )
    trailer = b"\x86\x84\x02iI\x01\x00\x84\x84\x08NSDictionary\x00\x94\x84\x01i\x01\x86"
    return header + length + body + trailer


class TestAttributedBodyDecoding:
    def test_short_message(self):
        blob = _make_attributed_body("hey what are you doing tonight")
        assert db.decode_attributed_body(blob) == "hey what are you doing tonight"

    def test_long_message_two_byte_length(self):
        text = "babes " * 300  # > 127 bytes -> 0x81 + 2-byte length
        blob = _make_attributed_body(text.strip())
        decoded = db.decode_attributed_body(blob)
        assert decoded is not None
        assert decoded.count("babes") == 300

    def test_unicode_and_emoji(self):
        text = "café ☕ let's go 🚗 mañana"
        blob = _make_attributed_body(text)
        assert db.decode_attributed_body(blob) == text

    def test_message_text_prefers_text_column(self):
        # message_text should use text column when it is clean
        class Row(dict):
            def keys(self):  # sqlite3.Row-like
                return list(super().keys())
        row = Row(text="plain text wins", attributedBody=_make_attributed_body("ignored"),
                  associated_message_type=0)
        assert db.message_text(row) == "plain text wins"

    def test_message_text_uses_attributed_body_when_text_null(self):
        class Row(dict):
            def keys(self):
                return list(super().keys())
        row = Row(text=None, attributedBody=_make_attributed_body("from blob body"),
                  associated_message_type=0)
        assert db.message_text(row) == "from blob body"

    def test_tapback_still_filtered_even_if_in_blob(self):
        class Row(dict):
            def keys(self):
                return list(super().keys())
        row = Row(text=None, attributedBody=_make_attributed_body('Loved "dinner?"'),
                  associated_message_type=2000)
        assert db.message_text(row) is None

    def test_non_string_blob_returns_none(self):
        # attachment-only / empty payloads should decode to None, not garbage
        assert db.decode_attributed_body(b"\x04\x0bstreamtyped\x81\xe8\x03") is None

    def test_garbage_metadata_not_returned(self):
        # NSDictionary / archiver keys must never surface as "text"
        blob = b"\x04\x0bstreamtyped\x84\x84\x0eNSDictionary\x00\x94\x84\x01i\x01\x86"
        assert db.decode_attributed_body(blob) is None
```

### Integration test (end to end through the DB layer)
Build an in-memory DB whose messages store text ONLY in `attributedBody`, then confirm the full-history scan decodes all of them:

```python
def test_full_scan_decodes_attributed_body_rows(self):
    conn = self._make_db()  # existing fixture with handle/chat/joins
    sentences = [f"message number {i} about pizza" for i in range(1, 51)]
    for i, s in enumerate(sentences, start=1):
        conn.execute(
            "INSERT INTO message (ROWID, text, attributedBody, is_from_me, date, handle_id, associated_message_type) "
            "VALUES (?, NULL, ?, 0, ?, 1, 0)",
            (i, _make_attributed_body(s), 700000000 + i),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (i,))
    texts = db.messages_for_contact(conn, "+15551234567", limit=None)
    assert len(texts) == 50            # not 0, not a handful
    assert "pizza" in " ".join(texts)
```

> Note: `_make_db()` in the current test file creates `handle`, `chat`, `chat_handle_join`, `chat_message_join`, and `message` tables and inserts handle `+15551234567` in chat 1. Reuse it.

---

## Acceptance criteria
- [ ] `decode_attributed_body()` recovers exact text from `streamtyped` blobs (short, long/2-byte length, unicode/emoji).
- [ ] `message_text()` still prefers the `text` column and still filters tapbacks.
- [ ] Attachment-only / non-string blobs decode to `None` (no archiver keys leak as words).
- [ ] In-memory integration test: 50/50 `attributedBody`-only messages decode.
- [ ] All existing tests in `tests/test_imsg.py` still pass (`pytest tests/ -v`).
- [ ] Manual: run `python app.py`, open a busy contact, click **Deep index** → "X of Y analyzed" where X is now in the thousands (close to Y minus genuine no-text rows like attachments/stickers), and word counts look realistic.

## Verification commands
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
python -m py_compile app.py imsg/*.py
# Manual (needs Full Disk Access + real chat.db):
python app.py  # deep-index a contact and confirm the analyzed count jumps
```

## Constraints
- Keep all processing local; no network, no writes to `chat.db`.
- Match existing style (small functions, minimal comments).
- Don't regress reaction filtering, GCS merging, or the quick-preview/deep-index split already in place.
- If you add a dependency (Option B), guard the import and keep the hand-rolled fallback.
