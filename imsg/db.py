"""Read and parse the macOS iMessage SQLite database."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from imsg.contacts import ContactDirectory
from imsg.reactions import is_reaction_message

APPLE_EPOCH = datetime(2001, 1, 1)
DEFAULT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Substrings from NSArchiver / iMessage attributedBody metadata (not real message text).
_GARBAGE_MARKERS = (
    "kimmessage", "kimbreadcrumb", "kimfile", "kimballoon", "kimdate",
    "nsattributed", "nsdictionary", "nsmutable", "nsstring", "nsnumber",
    "nsvalue", "nsobject", "attribute", "optionflags", "objectversion",
    "ddscanner", "nskeyedarchiver", "$classname", "$classes", "streamtyped",
    "archiver", "$archiver", "$objects", "$top", "$version", "$null",
)

# Markers that identify an NSKeyedArchiver / binary-plist payload (no plain text).
_KEYED_ARCHIVE_MARKERS = (b"$archiver", b"NSKeyedArchiver", b"bplist00")
_GARBAGE_TOKEN_RE = re.compile(
    r"\d{1,3}kim[a-z]|kim(?:message|breadcrumb|file|balloon|date)|"
    r"ns(?:attributed|string|dictionary|mutable|number|value|object)|"
    r"attribute(?:name)?|optionflags|objectversion",
    re.I,
)


@dataclass(frozen=True)
class Message:
    rowid: int
    text: str
    is_from_me: bool
    date: datetime | None
    handle_id: int | None
    contact: str | None
    chat_id: int | None
    chat_name: str | None


class DatabaseError(Exception):
    pass


class AccessDeniedError(DatabaseError):
    pass


def default_db_path() -> Path:
    return Path(os.environ.get("IMESSAGE_DB_PATH", DEFAULT_DB_PATH))


def apple_timestamp_to_datetime(ts: int | float | None) -> datetime | None:
    if ts is None:
        return None
    # Apple stores nanoseconds since 2001-01-01; older rows may be seconds.
    if ts > 1e15:
        seconds = ts / 1e9
    elif ts > 1e12:
        seconds = ts / 1e6
    else:
        seconds = float(ts)
    return APPLE_EPOCH + timedelta(seconds=seconds)


def looks_like_message_text(text: str) -> bool:
    """Reject plist/archiver keys that leak out of attributedBody blobs."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if any(marker in lower for marker in _GARBAGE_MARKERS):
        return False
    if _GARBAGE_TOKEN_RE.search(stripped):
        return False
    # One long blob with no spaces is almost always metadata, not a message.
    if " " not in stripped and len(stripped) > 35:
        return False
    # Require at least one letter; reject pure symbol runs.
    if not any(c.isalpha() for c in stripped):
        return False
    return True


def _streamtyped_string(blob: bytes) -> str | None:
    """Parse the first length-prefixed NSString value out of a streamtyped blob."""
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
    length = blob[p]
    p += 1
    if length == 0x81:
        if p + 2 > len(blob):
            return None
        length = int.from_bytes(blob[p:p + 2], "little")
        p += 2
    elif length == 0x82:
        if p + 4 > len(blob):
            return None
        length = int.from_bytes(blob[p:p + 4], "little")
        p += 4
    raw = blob[p:p + length]
    if len(raw) < length:
        return None
    return raw.decode("utf-8", errors="replace")


def _is_keyed_archive(blob: bytes) -> bool:
    """True for NSKeyedArchiver / binary-plist blobs whose text isn't inline."""
    head = blob[:4096]
    return any(marker in head for marker in _KEYED_ARCHIVE_MARKERS)


def _candidates_from_blob(blob: bytes) -> list[str]:
    """Last-resort fallback: scan printable runs for something message-shaped."""
    candidates: list[str] = []
    decoded = blob.decode("utf-8", errors="ignore")
    for run in re.findall(r"[\x20-\x7E\u00A0-\uFFFF]{2,}", decoded):
        run = run.strip()
        if looks_like_message_text(run):
            candidates.append(run)
    return candidates


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Best-effort extraction of plain text from attributedBody blobs."""
    if not blob:
        return None
    try:
        text = _streamtyped_string(blob)
        if text and looks_like_message_text(text):
            return text
    except Exception:
        pass
    # Keyed-archive payloads have no inline text; scraping them yields plist
    # keys ($archiver/$objects/$top…), so skip the printable-run fallback.
    if _is_keyed_archive(blob):
        return None
    try:
        candidates = _candidates_from_blob(blob)
        if not candidates:
            return None
        # Prefer messages with spaces (natural language) and reasonable length.
        def score(t: str) -> tuple[int, int]:
            has_space = 1 if " " in t else 0
            return (has_space, min(len(t), 500))
        return max(candidates, key=score)
    except Exception:
        pass
    return None


def message_text(row: sqlite3.Row) -> str | None:
    associated_type = None
    try:
        if "associated_message_type" in row.keys():
            associated_type = row["associated_message_type"]
    except Exception:
        associated_type = None

    text = row["text"]
    if text and str(text).strip():
        cleaned = str(text).strip()
        if looks_like_message_text(cleaned):
            if is_reaction_message(cleaned, associated_message_type=associated_type):
                return None
            return cleaned
    body = row["attributedBody"]
    if body:
        decoded = decode_attributed_body(body if isinstance(body, bytes) else bytes(body))
        if decoded and is_reaction_message(decoded, associated_message_type=associated_type):
            return None
        return decoded
    return None


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    if not path.exists():
        raise DatabaseError(f"Database not found at {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Probe read access.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.Error as exc:
        msg = str(exc).lower()
        if "authorization denied" in msg or "unable to open" in msg:
            raise AccessDeniedError(
                "Cannot read iMessage database. Grant Full Disk Access to Terminal "
                "(or your IDE) in System Settings → Privacy & Security → Full Disk Access."
            ) from exc
        raise DatabaseError(str(exc)) from exc


_DM_CHAT_SQL = """
    SELECT chj.chat_id, chj.handle_id
    FROM chat_handle_join chj
    GROUP BY chj.chat_id
    HAVING COUNT(*) = 1
"""

_MESSAGE_FILTER = "(m.text IS NOT NULL OR m.attributedBody IS NOT NULL)"
_REACTION_FILTER = (
    "(m.associated_message_type IS NULL OR m.associated_message_type = 0 "
    "OR m.associated_message_type < 2000 OR m.associated_message_type > 3006)"
)


def _message_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(message)")}


def _content_filter(conn: sqlite3.Connection) -> str:
    parts = [_MESSAGE_FILTER, _REACTION_FILTER]
    if "associated_message_type" not in _message_columns(conn):
        parts = [_MESSAGE_FILTER]
    return " AND ".join(f"({part})" for part in parts)


def _dm_chat_cte() -> str:
    return f"dm_chat AS ({_DM_CHAT_SQL})"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def iter_messages(
    conn: sqlite3.Connection,
    *,
    contact: str | None = None,
    dm_only: bool = False,
    limit: int | None = None,
) -> Iterator[Message]:
    """Yield messages with resolved contact and chat metadata."""
    params: list[object] = []
    where_parts = [_content_filter(conn)]

    if contact:
        where_parts.append(
            "(h.id = ? OR c.chat_identifier = ? OR c.display_name = ?)"
        )
        params.extend([contact, contact, contact])

    where_sql = " AND ".join(where_parts)
    limit_sql = f" LIMIT {int(limit)}" if limit else ""

    dm_join = ""
    if dm_only:
        dm_join = """
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN dm_chat d ON d.chat_id = cmj.chat_id
        JOIN handle h ON d.handle_id = h.ROWID
        """
        with_clause = f"WITH {_dm_chat_cte()} "
    else:
        with_clause = ""
        dm_join = """
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        """

    sql = f"""
        {with_clause}
        SELECT
            m.ROWID AS rowid,
            m.text,
            m.attributedBody,
            m.is_from_me,
            m.date,
            m.handle_id,
            h.id AS contact_id,
            c.ROWID AS chat_id,
            COALESCE(NULLIF(c.display_name, ''), c.chat_identifier) AS chat_name,
            c.chat_identifier
        FROM message m
        {dm_join}
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE {where_sql}
        ORDER BY m.date DESC
        {limit_sql}
    """

    seen_rowids: set[int] = set()
    for row in conn.execute(sql, params):
        rowid = row["rowid"]
        if rowid in seen_rowids:
            continue
        seen_rowids.add(rowid)
        txt = message_text(row)
        if not txt:
            continue
        contact_label = row["contact_id"] or row["chat_identifier"] or row["chat_name"]
        yield Message(
            rowid=row["rowid"],
            text=txt,
            is_from_me=bool(row["is_from_me"]),
            date=apple_timestamp_to_datetime(row["date"]),
            handle_id=row["handle_id"],
            contact=contact_label,
            chat_id=row["chat_id"],
            chat_name=row["chat_name"],
        )


def contact_stats(conn: sqlite3.Connection, directory: ContactDirectory | None = None) -> list[dict]:
    """Per-contact DM counts — received only from 1:1 threads, not group chats."""
    sql = f"""
        WITH {_dm_chat_cte()}
        SELECT
            h.id AS handle,
            COUNT(DISTINCT m.ROWID) AS total,
            COUNT(DISTINCT CASE WHEN m.is_from_me = 1 THEN m.ROWID END) AS sent,
            COUNT(DISTINCT CASE WHEN m.is_from_me = 0 THEN m.ROWID END) AS received,
            MAX(m.date) AS last_date
        FROM dm_chat d
        JOIN handle h ON d.handle_id = h.ROWID
        JOIN chat_message_join cmj ON cmj.chat_id = d.chat_id
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE {_content_filter(conn)}
        GROUP BY h.ROWID
        ORDER BY total DESC
    """
    rows = []
    for row in conn.execute(sql):
        item = {
            "handle": row["handle"],
            "contact": row["handle"],
            "total": row["total"],
            "sent": row["sent"],
            "received": row["received"],
            "last_date": apple_timestamp_to_datetime(row["last_date"]),
        }
        if directory:
            directory.enrich(item)
        rows.append(item)
    return rows


def group_chat_stats(conn: sqlite3.Connection, directory: ContactDirectory | None = None) -> list[dict]:
    """Per-group-chat message counts."""
    if not _has_table(conn, "chat"):
        return []
    sql = f"""
        SELECT
            COALESCE(NULLIF(c.display_name, ''), c.chat_identifier) AS contact,
            c.chat_identifier,
            COUNT(DISTINCT m.ROWID) AS total,
            COUNT(DISTINCT CASE WHEN m.is_from_me = 1 THEN m.ROWID END) AS sent,
            COUNT(DISTINCT CASE WHEN m.is_from_me = 0 THEN m.ROWID END) AS received,
            MAX(m.date) AS last_date,
            (SELECT COUNT(*) FROM chat_handle_join chj2 WHERE chj2.chat_id = c.ROWID) AS participants
        FROM chat c
        JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE {_content_filter(conn)}
        GROUP BY c.ROWID
        HAVING participants > 1
        ORDER BY total DESC
    """
    rows = []
    for row in conn.execute(sql):
        item = {
            "contact": row["contact"],
            "handle": row["chat_identifier"],
            "chat_identifier": row["chat_identifier"],
            "total": row["total"],
            "sent": row["sent"],
            "received": row["received"],
            "last_date": apple_timestamp_to_datetime(row["last_date"]),
            "participants": row["participants"],
            "is_group": True,
        }
        if directory:
            directory.enrich(item)
        rows.append(item)
    return rows


def monthly_activity(conn: sqlite3.Connection) -> list[dict]:
    sql = f"""
        SELECT m.date
        FROM message m
        WHERE {_content_filter(conn)}
    """
    buckets: dict[str, int] = {}
    for (raw_date,) in conn.execute(sql):
        dt = apple_timestamp_to_datetime(raw_date)
        if not dt:
            continue
        key = dt.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + 1
    return [{"month": k, "count": buckets[k]} for k in sorted(buckets)]


def _group_chat_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            c.chat_identifier,
            COALESCE(NULLIF(c.display_name, ''), c.chat_identifier) AS label
        FROM chat c
        WHERE (c.chat_identifier = ? OR c.display_name = ?)
          AND (SELECT COUNT(*) FROM chat_handle_join chj WHERE chj.chat_id = c.ROWID) > 1
        LIMIT 1
        """,
        (identifier, identifier),
    ).fetchone()


def is_group_chat(conn: sqlite3.Connection, identifier: str) -> bool:
    return _group_chat_row(conn, identifier) is not None


def resolve_group_chat(conn: sqlite3.Connection, identifier: str) -> dict | None:
    row = _group_chat_row(conn, identifier)
    if not row:
        return None
    return {"id": row["chat_identifier"], "display": row["label"]}


def handles_for_contact(
    conn: sqlite3.Connection,
    directory: ContactDirectory,
    identifier: str,
) -> list[str]:
    """All handle IDs (phone/email/etc.) for one person."""
    handles: set[str] = set()

    if conn.execute("SELECT 1 FROM handle WHERE id = ?", (identifier,)).fetchone():
        handles.add(identifier)

    target_names = {identifier}
    if handles:
        target_names.add(directory.name_for(identifier))

    for row in conn.execute("SELECT id FROM handle"):
        hid = row["id"]
        name = directory.name_for(hid)
        if hid == identifier or name == identifier or name in target_names:
            handles.add(hid)

    if _has_table(conn, "chat"):
        for row in conn.execute(
            """
            SELECT DISTINCT h.id
            FROM chat c
            JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            JOIN handle h ON chj.handle_id = h.ROWID
            WHERE c.display_name = ? OR c.chat_identifier = ?
            """,
            (identifier, identifier),
        ):
            handles.add(row["id"])

    return sorted(handles) if handles else [identifier]


def _yield_message_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    seen_rowids: set[int],
) -> Message | None:
    rowid = row["rowid"]
    if rowid in seen_rowids:
        return None
    txt = message_text(row)
    if not txt:
        return None
    seen_rowids.add(rowid)
    contact_label = row["contact_id"] or row["chat_identifier"] or row["chat_name"]
    return Message(
        rowid=rowid,
        text=txt,
        is_from_me=bool(row["is_from_me"]),
        date=apple_timestamp_to_datetime(row["date"]),
        handle_id=row["handle_id"],
        contact=contact_label,
        chat_id=row["chat_id"],
        chat_name=row["chat_name"],
    )


def iter_messages_for_person(
    conn: sqlite3.Connection,
    directory: ContactDirectory,
    identifier: str,
    *,
    include_groups: bool = True,
    limit: int | None = None,
) -> Iterator[Message]:
    """All decodable DM + group messages for a person across every handle."""
    handles = handles_for_contact(conn, directory, identifier)
    seen_rowids: set[int] = set()
    yielded = 0

    for handle in handles:
        for msg in iter_messages(conn, contact=handle, dm_only=True, limit=None):
            if msg.rowid in seen_rowids:
                continue
            seen_rowids.add(msg.rowid)
            yield msg
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    if not include_groups or not handles:
        return

    placeholders = ",".join("?" * len(handles))
    sql = f"""
        SELECT
            m.ROWID AS rowid,
            m.text,
            m.attributedBody,
            m.is_from_me,
            m.date,
            m.handle_id,
            h.id AS contact_id,
            c.ROWID AS chat_id,
            COALESCE(NULLIF(c.display_name, ''), c.chat_identifier) AS chat_name,
            c.chat_identifier
        FROM message m
        JOIN handle h ON m.handle_id = h.ROWID
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE h.id IN ({placeholders})
          AND (SELECT COUNT(*) FROM chat_handle_join chj WHERE chj.chat_id = c.ROWID) > 1
          AND {_content_filter(conn)}
        ORDER BY m.date DESC
    """
    for row in conn.execute(sql, handles):
        msg = _yield_message_row(conn, row, seen_rowids)
        if not msg:
            continue
        yield msg
        yielded += 1
        if limit is not None and yielded >= limit:
            return


def person_message_counts(
    conn: sqlite3.Connection,
    directory: ContactDirectory,
    identifier: str,
    *,
    include_groups: bool = True,
    count_decoded: bool = True,
) -> dict[str, int]:
    """How many message rows exist in the DB vs how many decode to text.

    ``count_decoded`` walks every message and is expensive; skip it for the
    fast preview and only run it during a deep scan.
    """
    handles = handles_for_contact(conn, directory, identifier)
    placeholders = ",".join("?" * len(handles))

    dm_sql = f"""
        WITH {_dm_chat_cte()}
        SELECT COUNT(DISTINCT m.ROWID)
        FROM dm_chat d
        JOIN handle h ON d.handle_id = h.ROWID
        JOIN chat_message_join cmj ON cmj.chat_id = d.chat_id
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE h.id IN ({placeholders}) AND {_content_filter(conn)}
    """
    total = conn.execute(dm_sql, handles).fetchone()[0]

    if include_groups:
        group_sql = f"""
            SELECT COUNT(DISTINCT m.ROWID)
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE h.id IN ({placeholders})
              AND (SELECT COUNT(*) FROM chat_handle_join chj WHERE chj.chat_id = c.ROWID) > 1
              AND {_content_filter(conn)}
        """
        total += conn.execute(group_sql, handles).fetchone()[0]

    if not count_decoded:
        return {"total": total, "decoded": None}

    decoded = sum(1 for _ in iter_messages_for_person(
        conn, directory, identifier, include_groups=include_groups, limit=None
    ))
    return {"total": total, "decoded": decoded}


def messages_for_person(
    conn: sqlite3.Connection,
    directory: ContactDirectory,
    identifier: str,
    *,
    include_groups: bool = True,
    limit: int | None = None,
) -> list[str]:
    """Full conversation text for a person (all handles, optional group messages)."""
    return [
        msg.text
        for msg in iter_messages_for_person(
            conn,
            directory,
            identifier,
            include_groups=include_groups,
            limit=limit,
        )
    ]


def messages_for_contact(
    conn: sqlite3.Connection,
    contact: str,
    limit: int | None = None,
    *,
    dm_only: bool = True,
) -> list[str]:
    """Return decoded message text for a contact, deduped by message rowid."""
    texts: list[str] = []
    seen_rowids: set[int] = set()
    sql_limit = None if limit is None else limit * 3
    for msg in iter_messages(conn, contact=contact, dm_only=dm_only, limit=sql_limit):
        if msg.rowid in seen_rowids:
            continue
        seen_rowids.add(msg.rowid)
        texts.append(msg.text)
        if limit is not None and len(texts) >= limit:
            break
    return texts


def database_summary(conn: sqlite3.Connection) -> dict:
    total = conn.execute(
        f"SELECT COUNT(*) FROM message m WHERE {_content_filter(conn)}"
    ).fetchone()[0]
    handles = conn.execute("SELECT COUNT(*) FROM handle").fetchone()[0]
    chats = conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0] if _has_table(conn, "chat") else 0
    return {"messages": total, "handles": handles, "chats": chats}
