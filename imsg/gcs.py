"""Index iMessage data from Google Cloud Storage."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from imsg.contacts import ContactDirectory

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover - optional dependency
    storage = None  # type: ignore[assignment,misc]


class GCSConfigError(Exception):
    pass


class GCSUnavailableError(Exception):
    pass


@dataclass
class GCSMessage:
    handle: str
    text: str
    is_from_me: bool
    date: datetime | None = None
    name: str | None = None


@dataclass
class GCSContactStats:
    handle: str
    name: str | None = None
    total: int = 0
    sent: int = 0
    received: int = 0
    last_date: datetime | None = None
    source: str = "gcs"
    texts: list[str] = field(default_factory=list)


@dataclass
class GCSIndex:
    contacts: list[dict] = field(default_factory=list)
    texts_by_handle: dict[str, list[str]] = field(default_factory=dict)
    loaded_at: datetime | None = None
    files_indexed: int = 0
    db_files: int = 0
    json_files: int = 0
    error: str | None = None


_index_cache: GCSIndex | None = None
_index_lock = threading.Lock()


def gcs_configured() -> bool:
    return bool(os.environ.get("GCS_BUCKET", "").strip())


def _require_storage():
    if storage is None:
        raise GCSUnavailableError(
            "google-cloud-storage is not installed. "
            "Run: pip install google-cloud-storage"
        )


def _bucket_name() -> str:
    name = os.environ.get("GCS_BUCKET", "").strip()
    if not name:
        raise GCSConfigError("Set GCS_BUCKET to the export bucket name.")
    return name


def _prefix() -> str:
    return os.environ.get("GCS_PREFIX", "").strip()


def _refresh_seconds() -> int:
    raw = os.environ.get("GCS_REFRESH_SECONDS", "900").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 900


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _coerce_message(raw: dict[str, Any]) -> GCSMessage | None:
    handle = (
        raw.get("handle")
        or raw.get("contact")
        or raw.get("phone")
        or raw.get("id")
    )
    text = raw.get("text") or raw.get("message") or raw.get("body")
    if not handle or not text:
        return None
    handle = str(handle).strip()
    text = str(text).strip()
    if not handle or not text:
        return None
    is_from_me = bool(raw.get("is_from_me", raw.get("from_me", False)))
    return GCSMessage(
        handle=handle,
        text=text,
        is_from_me=is_from_me,
        date=_parse_date(raw.get("date") or raw.get("timestamp")),
        name=(raw.get("name") or raw.get("display") or None),
    )


def _iter_json_records(payload: Any) -> Iterator[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("messages", "rows", "data", "exports"):
            items = payload.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        yield item
                return
        if any(k in payload for k in ("handle", "contact", "text", "message", "body")):
            yield payload


def parse_export_bytes(data: bytes) -> list[GCSMessage]:
    """Parse JSON or JSONL export payloads."""
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return []

    messages: list[GCSMessage] = []
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            for row in _iter_json_records(payload):
                msg = _coerce_message(row)
                if msg:
                    messages.append(msg)
            return messages

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            msg = _coerce_message(row)
            if msg:
                messages.append(msg)
    return messages


def aggregate_messages(messages: list[GCSMessage]) -> list[GCSContactStats]:
    by_handle: dict[str, GCSContactStats] = {}
    for msg in messages:
        stats = by_handle.get(msg.handle)
        if not stats:
            stats = GCSContactStats(handle=msg.handle, name=msg.name)
            by_handle[msg.handle] = stats
        if msg.name and not stats.name:
            stats.name = msg.name
        stats.total += 1
        if msg.is_from_me:
            stats.sent += 1
        else:
            stats.received += 1
        if msg.date and (stats.last_date is None or msg.date > stats.last_date):
            stats.last_date = msg.date
        stats.texts.append(msg.text)
    return sorted(by_handle.values(), key=lambda s: s.total, reverse=True)


def _stats_to_contacts(stats: list[GCSContactStats]) -> tuple[list[dict], dict[str, list[str]]]:
    contacts: list[dict] = []
    texts_by_handle: dict[str, list[str]] = {}
    for item in stats:
        contacts.append(
            {
                "handle": item.handle,
                "contact": item.handle,
                "name": item.name or item.handle,
                "display": item.name or item.handle,
                "total": item.total,
                "sent": item.sent,
                "received": item.received,
                "last_date": item.last_date,
                "source": "gcs",
                "is_group": False,
            }
        )
        texts_by_handle[item.handle] = list(item.texts)
    return contacts, texts_by_handle


def _merge_stats(existing: dict[str, GCSContactStats], incoming: list[GCSContactStats]) -> None:
    for item in incoming:
        stats = existing.get(item.handle)
        if not stats:
            existing[item.handle] = GCSContactStats(
                handle=item.handle,
                name=item.name,
                total=item.total,
                sent=item.sent,
                received=item.received,
                last_date=item.last_date,
                texts=list(item.texts),
            )
            continue
        if item.name and not stats.name:
            stats.name = item.name
        stats.total += item.total
        stats.sent += item.sent
        stats.received += item.received
        if item.last_date and (stats.last_date is None or item.last_date > stats.last_date):
            stats.last_date = item.last_date
        stats.texts.extend(item.texts)


def index_chat_db(db_path: Path, *, text_limit: int = 800) -> list[GCSContactStats]:
    """Index a chat.db file the same way the local app does."""
    from imsg import db

    conn = db.connect(db_path)
    directory = ContactDirectory(conn)
    stats: list[GCSContactStats] = []
    try:
        for row in db.contact_stats(conn, directory):
            handle = row["handle"]
            texts = db.messages_for_contact(conn, handle, limit=text_limit, dm_only=True)
            stats.append(
                GCSContactStats(
                    handle=handle,
                    name=row.get("name") or row.get("display"),
                    total=row["total"],
                    sent=row["sent"],
                    received=row["received"],
                    last_date=row.get("last_date"),
                    texts=texts,
                )
            )
    finally:
        conn.close()
    return stats


def list_index_blobs(client: Any, bucket_name: str, prefix: str) -> list[Any]:
    bucket = client.bucket(bucket_name)
    json_suffixes = (".json", ".jsonl", ".ndjson")
    blobs = []
    for blob in bucket.list_blobs(prefix=prefix or None):
        name = blob.name.lower()
        if name.endswith(json_suffixes) or name.endswith(".db"):
            blobs.append(blob)
    return blobs


def build_index(
    *,
    bucket: str | None = None,
    prefix: str | None = None,
) -> GCSIndex:
    """Download and index all supported files from GCS."""
    _require_storage()
    from imsg import db as imsg_db

    bucket_name = bucket or _bucket_name()
    blob_prefix = _prefix() if prefix is None else prefix
    client = storage.Client()

    combined: dict[str, GCSContactStats] = {}
    db_files = 0
    json_files = 0
    files_indexed = 0

    for blob in list_index_blobs(client, bucket_name, blob_prefix):
        name = blob.name.lower()
        temp_path: Path | None = None
        try:
            if name.endswith(".db"):
                fd, temp_name = tempfile.mkstemp(suffix=".db")
                os.close(fd)
                temp_path = Path(temp_name)
                blob.download_to_filename(temp_path)
                _merge_stats(combined, index_chat_db(temp_path))
                db_files += 1
            else:
                messages = parse_export_bytes(blob.download_as_bytes())
                _merge_stats(combined, aggregate_messages(messages))
                json_files += 1
            files_indexed += 1
        except (imsg_db.DatabaseError, OSError, ValueError):
            continue
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    stats = sorted(combined.values(), key=lambda s: s.total, reverse=True)
    contacts, texts_by_handle = _stats_to_contacts(stats)
    return GCSIndex(
        contacts=contacts,
        texts_by_handle=texts_by_handle,
        loaded_at=datetime.now(timezone.utc),
        files_indexed=files_indexed,
        db_files=db_files,
        json_files=json_files,
    )


def get_index(*, force: bool = False) -> GCSIndex | None:
    """Return the cached GCS index, building it on first use."""
    if not gcs_configured():
        return None
    global _index_cache
    with _index_lock:
        stale = False
        if _index_cache and _index_cache.loaded_at and _refresh_seconds() > 0:
            age = (datetime.now(timezone.utc) - _index_cache.loaded_at).total_seconds()
            stale = age >= _refresh_seconds()
        if _index_cache is None or force or stale:
            try:
                _index_cache = build_index()
            except Exception as exc:
                _index_cache = GCSIndex(error=str(exc))
        return _index_cache


def ensure_index(*, background: bool = False) -> None:
    """Warm the GCS index on startup."""
    if not gcs_configured():
        return

    def _run() -> None:
        try:
            get_index(force=True)
        except Exception:
            pass

    if background:
        threading.Thread(target=_run, daemon=True).start()
    else:
        _run()


def contact_stats_from_gcs(
    *,
    bucket: str | None = None,
    prefix: str | None = None,
) -> list[dict]:
    if not gcs_configured() and bucket is None and prefix is None:
        raise GCSConfigError("Set GCS_BUCKET to the export bucket name.")
    if bucket or prefix is not None:
        index = build_index(bucket=bucket, prefix=prefix)
    else:
        index = get_index(force=True)
    if index is None:
        return []
    if index.error:
        raise GCSUnavailableError(index.error)
    return list(index.contacts)


def texts_for_handle(handle: str, *, limit: int | None = None) -> list[str]:
    index = get_index()
    if not index:
        return []
    texts = index.texts_by_handle.get(handle, [])
    if limit is None:
        return list(texts)
    return texts[:limit]


def merge_texts(local: list[str], remote: list[str], *, limit: int | None = 800) -> list[str]:
    if not remote:
        return list(local if limit is None else local[:limit])
    if not local:
        return list(remote if limit is None else remote[:limit])
    # Skip GCS rows already present locally (e.g. synced chat.db backup).
    local_set = set(local)
    deduped_remote = [text for text in remote if text not in local_set]
    combined = local + deduped_remote
    if limit is None:
        return combined
    return combined[:limit]


def merge_contact_stats(local: list[dict], remote: list[dict]) -> list[dict]:
    """Merge local chat.db stats with GCS export stats by handle."""
    merged: dict[str, dict] = {}

    def key(row: dict) -> str:
        return str(row.get("handle") or row.get("contact") or "")

    for row in local:
        k = key(row)
        if not k:
            continue
        item = dict(row)
        item.setdefault("source", "local")
        item["sources"] = ["local"]
        merged[k] = item

    for row in remote:
        k = key(row)
        if not k:
            continue
        if k in merged:
            item = merged[k]
            item["total"] = item.get("total", 0) + row.get("total", 0)
            item["sent"] = item.get("sent", 0) + row.get("sent", 0)
            item["received"] = item.get("received", 0) + row.get("received", 0)
            local_last = item.get("last_date")
            remote_last = row.get("last_date")
            if remote_last and (not local_last or remote_last > local_last):
                item["last_date"] = remote_last
            if row.get("name") and item.get("name") in (None, item.get("handle")):
                item["name"] = row["name"]
                item["display"] = row.get("display") or row["name"]
            item["source"] = "merged"
            sources = set(item.get("sources") or ["local"])
            sources.add("gcs")
            item["sources"] = sorted(sources)
        else:
            item = dict(row)
            item["source"] = "gcs"
            item["sources"] = ["gcs"]
            merged[k] = item

    return sorted(merged.values(), key=lambda r: r.get("total", 0), reverse=True)


def gcs_status() -> dict:
    if not gcs_configured():
        return {"configured": False, "available": storage is not None, "auto_index": False}
    status = {
        "configured": True,
        "available": storage is not None,
        "auto_index": True,
        "bucket": os.environ.get("GCS_BUCKET", ""),
        "prefix": _prefix(),
        "refresh_seconds": _refresh_seconds(),
    }
    if storage is None:
        status["error"] = "google-cloud-storage not installed"
        return status

    index = get_index()
    if index is None:
        return status
    if index.error:
        status["error"] = index.error
        return status

    status.update(
        {
            "files_indexed": index.files_indexed,
            "db_files": index.db_files,
            "json_files": index.json_files,
            "contacts_indexed": len(index.contacts),
            "loaded_at": index.loaded_at.isoformat() if index.loaded_at else None,
        }
    )
    try:
        client = storage.Client()
        blobs = list_index_blobs(client, status["bucket"], status["prefix"])
        status["index_files"] = len(blobs)
    except Exception as exc:  # pragma: no cover - network/credentials dependent
        status["error"] = str(exc)
    return status
