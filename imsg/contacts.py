"""Resolve phone numbers / emails to names from macOS Contacts."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_DIGITS_RE = re.compile(r"\D+")


def _normalize_phone(value: str) -> str:
    digits = _DIGITS_RE.sub("", value)
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def _normalize_handle(value: str) -> str:
    value = value.strip()
    if "@" in value:
        return value.lower()
    return _normalize_phone(value)


def _full_name(first: str | None, last: str | None, org: str | None, nick: str | None) -> str | None:
    parts = [p.strip() for p in (first, last) if p and p.strip()]
    if parts:
        return " ".join(parts)
    for candidate in (nick, org):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _addressbook_paths() -> list[Path]:
    paths: list[Path] = []
    roots = [
        Path.home() / "Library/Application Support/AddressBook",
        Path.home() / "Library/Containers/com.apple.AddressBook/Data/Library/Application Support/AddressBook",
    ]
    for base in roots:
        if not base.exists():
            continue
        for pattern in ("AddressBook-v*.abcddb", "Sources/*/AddressBook-v*.abcddb"):
            paths.extend(base.glob(pattern))
        legacy = base / "AddressBook.sqlitedb"
        if legacy.exists():
            paths.append(legacy)
    return paths


def _load_addressbook_names() -> dict[str, str]:
    """Map normalized phone/email -> display name."""
    lookup: dict[str, str] = {}
    for path in _addressbook_paths():
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "ZABCDRECORD" not in tables:
                conn.close()
                continue

            records: dict[int, str] = {}
            for row in conn.execute(
                """
                SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZNICKNAME
                FROM ZABCDRECORD
                """
            ):
                name = _full_name(row["ZFIRSTNAME"], row["ZLASTNAME"], row["ZORGANIZATION"], row["ZNICKNAME"])
                if name:
                    records[row["Z_PK"]] = name

            if "ZABCDPHONENUMBER" in tables:
                for row in conn.execute(
                    "SELECT ZFULLNUMBER, ZOWNER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
                ):
                    owner = row["ZOWNER"]
                    if owner in records:
                        lookup[_normalize_phone(row["ZFULLNUMBER"])] = records[owner]

            if "ZABCDEMAILADDRESS" in tables:
                for row in conn.execute(
                    "SELECT ZADDRESS, ZOWNER FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
                ):
                    owner = row["ZOWNER"]
                    if owner in records:
                        lookup[_normalize_handle(row["ZADDRESS"])] = records[owner]

            conn.close()
            if lookup:
                return lookup
        except sqlite3.Error:
            continue
    return lookup


def _imessage_display_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Names cached on 1:1 iMessage threads."""
    lookup: dict[str, str] = {}
    try:
        rows = conn.execute(
            """
            SELECT h.id AS handle_id, c.display_name
            FROM chat c
            JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            JOIN handle h ON chj.handle_id = h.ROWID
            WHERE c.display_name IS NOT NULL AND TRIM(c.display_name) != ''
              AND (SELECT COUNT(*) FROM chat_handle_join x WHERE x.chat_id = c.ROWID) = 1
            """
        )
        for row in rows:
            lookup[row["handle_id"]] = row["display_name"].strip()
    except sqlite3.Error:
        pass
    return lookup


class ContactDirectory:
    def __init__(self, imsg_conn: sqlite3.Connection):
        self._by_handle: dict[str, str] = {}
        self._addressbook = _load_addressbook_names()
        self._imessage = _imessage_display_names(imsg_conn)

    def name_for(self, handle_id: str) -> str:
        if handle_id in self._by_handle:
            return self._by_handle[handle_id]

        key = _normalize_handle(handle_id)
        name = self._imessage.get(handle_id) or self._addressbook.get(key)
        if not name and key != handle_id:
            name = self._addressbook.get(_normalize_handle(handle_id))
        resolved = name or handle_id
        self._by_handle[handle_id] = resolved
        return resolved

    def enrich(self, row: dict) -> dict:
        if row.get("is_group"):
            label = row.get("contact") or row.get("chat_identifier") or "Group chat"
            row["name"] = label
            row["display"] = label
            row["handle"] = row.get("chat_identifier") or row.get("handle") or label
            return row

        handle = row.get("handle") or row.get("contact")
        if handle:
            row["handle"] = handle
            row["name"] = self.name_for(handle)
            row["display"] = row["name"]
        else:
            row["display"] = row.get("contact", "Unknown")
        return row
