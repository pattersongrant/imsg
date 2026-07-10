#!/usr/bin/env python3
"""iMessage Insights — local web app."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from imsg import analyze, db, gcs
from imsg.contacts import ContactDirectory

app = Flask(__name__)


def _open():
    conn = db.connect()
    return conn, ContactDirectory(conn)


def _resolve_contact(conn, directory: ContactDirectory, identifier: str) -> dict:
    group = db.resolve_group_chat(conn, identifier)
    if group:
        return {"kind": "group", "id": group["id"], "display": group["display"]}
    handle = _resolve_handle(conn, directory, identifier)
    return {"kind": "dm", "id": handle, "display": directory.name_for(handle)}


def _resolve_handle(conn, directory: ContactDirectory, identifier: str) -> str:
    row = conn.execute("SELECT id FROM handle WHERE id = ?", (identifier,)).fetchone()
    if row:
        return identifier
    for handle_row in conn.execute("SELECT id FROM handle"):
        handle_id = handle_row["id"]
        if directory.name_for(handle_id) == identifier:
            return handle_id
    return identifier


def _load_contacts(conn, directory, *, include_groups: bool, merge_gcs: bool) -> list[dict]:
    direct = db.contact_stats(conn, directory)
    for row in direct:
        row["is_group"] = False
        row.setdefault("source", "local")
    groups: list[dict] = []
    combined = direct
    if include_groups:
        groups = db.group_chat_stats(conn, directory)
        combined = sorted(direct + groups, key=lambda r: r["total"], reverse=True)
    if merge_gcs and gcs.gcs_configured():
        try:
            remote = gcs.contact_stats_from_gcs()
            combined = gcs.merge_contact_stats(
                [r for r in combined if not r.get("is_group")],
                remote,
            )
            if include_groups:
                combined = sorted(
                    combined + [g for g in groups if g.get("is_group")],
                    key=lambda r: r["total"],
                    reverse=True,
                )
        except (gcs.GCSConfigError, gcs.GCSUnavailableError) as exc:
            raise db.DatabaseError(str(exc)) from exc
    return combined


def _serialize_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        if row.get("last_date"):
            row["last_date"] = row["last_date"].isoformat()
    return rows


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    path = db.default_db_path()
    payload = {"gcs": gcs.gcs_status()}
    try:
        conn, _ = _open()
        summary = db.database_summary(conn)
        conn.close()
        payload.update({"ok": True, "db_path": str(path), "summary": summary})
        return jsonify(payload)
    except db.AccessDeniedError as exc:
        payload.update({"ok": False, "error": "permission", "message": str(exc), "db_path": str(path)})
        return jsonify(payload), 403
    except db.DatabaseError as exc:
        payload.update({"ok": False, "error": "database", "message": str(exc), "db_path": str(path)})
        return jsonify(payload), 500


@app.route("/api/contacts")
def contacts():
    limit = min(int(request.args.get("limit", 50)), 200)
    include_groups = request.args.get("groups", "1") == "1"
    merge_gcs = request.args.get("gcs", "1") == "1"
    try:
        conn, directory = _open()
        combined = _load_contacts(
            conn, directory, include_groups=include_groups, merge_gcs=merge_gcs
        )
        conn.close()
        return jsonify({"contacts": _serialize_rows(combined[:limit])})
    except (db.AccessDeniedError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 403


@app.route("/api/gcs/contacts")
def gcs_contacts():
    limit = min(int(request.args.get("limit", 50)), 200)
    try:
        rows = gcs.contact_stats_from_gcs()
        return jsonify({"contacts": _serialize_rows(rows[:limit]), "source": "gcs"})
    except (gcs.GCSConfigError, gcs.GCSUnavailableError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.route("/api/bubbles")
def bubbles():
    limit = min(int(request.args.get("limit", 30)), 60)
    merge_gcs = request.args.get("gcs", "1") == "1"
    try:
        conn, directory = _open()
        people = _load_contacts(conn, directory, include_groups=False, merge_gcs=merge_gcs)[:limit]
        conn.close()
        payload = [
            {
                "handle": p["handle"],
                "name": p.get("name", p["handle"]),
                "display": p.get("display", p["handle"]),
                "total": p["total"],
                "sent": p["sent"],
                "received": p["received"],
            }
            for p in people
        ]
        return jsonify({"people": payload})
    except (db.AccessDeniedError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 403


@app.route("/api/activity")
def activity():
    try:
        conn, _ = _open()
        data = db.monthly_activity(conn)
        conn.close()
        return jsonify({"activity": data})
    except (db.AccessDeniedError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 403


@app.route("/api/contact/<path:contact_id>")
def contact_detail(contact_id: str):
    try:
        conn, directory = _open()
        resolved = _resolve_contact(conn, directory, contact_id)
        is_group = resolved["kind"] == "group"
        lookup_id = resolved["id"]
        texts = db.messages_for_contact(
            conn, lookup_id, limit=800, dm_only=not is_group
        )
        topics = analyze.summarize_topics(texts)
        recent = []
        for msg in db.iter_messages(
            conn, contact=lookup_id, dm_only=not is_group, limit=30
        ):
            recent.append(
                {
                    "text": msg.text[:500],
                    "is_from_me": msg.is_from_me,
                    "date": msg.date.isoformat() if msg.date else None,
                }
            )
        conn.close()
        return jsonify(
            {
                "contact": lookup_id,
                "name": resolved["display"],
                "display": resolved["display"],
                "is_group": is_group,
                "topics": topics,
                "recent": recent,
            }
        )
    except (db.AccessDeniedError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 403


@app.route("/api/topics")
def global_topics():
    limit_contacts = min(int(request.args.get("top", 10)), 30)
    try:
        conn, directory = _open()
        all_contacts = db.contact_stats(conn, directory)
        top = all_contacts[:limit_contacts]
        per_contact = []
        all_texts: list[str] = []
        for row in top:
            handle = row["handle"]
            texts = db.messages_for_contact(conn, handle, limit=400, dm_only=True)
            all_texts.extend(texts)
            per_contact.append(
                {
                    "contact": handle,
                    "name": row.get("name", handle),
                    "display": row.get("display", handle),
                    "total": row["total"],
                    "topics": analyze.summarize_topics(texts),
                }
            )
        overall = analyze.summarize_topics(all_texts)
        conn.close()
        return jsonify({"overall": overall, "by_contact": per_contact})
    except (db.AccessDeniedError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 403


def main():
    print("iMessage Insights")
    print(f"Database: {db.default_db_path()}")
    print("Open http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, debug=False)


if __name__ == "__main__":
    main()
