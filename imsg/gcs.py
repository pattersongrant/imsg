"""Index exported iMessage rows from Google Cloud Storage."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

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


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Unix timestamp seconds or milliseconds.
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


def list_export_blobs(client: Any, bucket_name: str, prefix: str) -> list[Any]:
    bucket = client.bucket(bucket_name)
    suffixes = (".json", ".jsonl", ".ndjson")
    blobs = []
    for blob in bucket.list_blobs(prefix=prefix or None):
        name = blob.name.lower()
        if name.endswith(suffixes):
            blobs.append(blob)
    return blobs


def load_messages_from_gcs(
    *,
    bucket: str | None = None,
    prefix: str | None = None,
) -> list[GCSMessage]:
    _require_storage()
    bucket_name = bucket or _bucket_name()
    blob_prefix = _prefix() if prefix is None else prefix
    client = storage.Client()
    messages: list[GCSMessage] = []
    for blob in list_export_blobs(client, bucket_name, blob_prefix):
        messages.extend(parse_export_bytes(blob.download_as_bytes()))
    return messages


def contact_stats_from_gcs(
    *,
    bucket: str | None = None,
    prefix: str | None = None,
) -> list[dict]:
    messages = load_messages_from_gcs(bucket=bucket, prefix=prefix)
    rows: list[dict] = []
    for stats in aggregate_messages(messages):
        rows.append(
            {
                "handle": stats.handle,
                "contact": stats.handle,
                "name": stats.name or stats.handle,
                "display": stats.name or stats.handle,
                "total": stats.total,
                "sent": stats.sent,
                "received": stats.received,
                "last_date": stats.last_date,
                "source": "gcs",
                "is_group": False,
            }
        )
    return rows


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
        return {"configured": False, "available": storage is not None}
    status = {
        "configured": True,
        "available": storage is not None,
        "bucket": os.environ.get("GCS_BUCKET", ""),
        "prefix": _prefix(),
    }
    if storage is None:
        status["error"] = "google-cloud-storage not installed"
        return status
    try:
        client = storage.Client()
        bucket = client.bucket(status["bucket"])
        blobs = list_export_blobs(client, status["bucket"], status["prefix"])
        status["export_files"] = len(blobs)
    except Exception as exc:  # pragma: no cover - network/credentials dependent
        status["error"] = str(exc)
    return status
