import json
import sqlite3
from datetime import datetime

import pytest

from imsg import analyze, db, gcs
from imsg.reactions import is_reaction_message, looks_like_reaction_text, strip_reaction_prefix


class TestReactions:
    def test_detects_tapback_prefixes(self):
        assert looks_like_reaction_text('Loved "hello there"')
        assert looks_like_reaction_text("Liked something")
        assert looks_like_reaction_text("Laughed at joke")
        assert not looks_like_reaction_text("I loved that movie")

    def test_associated_message_type(self):
        assert is_reaction_message("hello", associated_message_type=2000)
        assert is_reaction_message("hello", associated_message_type=3003)
        assert not is_reaction_message("hello", associated_message_type=0)

    def test_strip_reaction_prefix(self):
        assert strip_reaction_prefix('Loved "pizza tonight"') == "pizza tonight"
        assert strip_reaction_prefix("Emphasized important") == "important"


class TestAnalyzeReactions:
    def test_reaction_words_excluded_from_top_keywords(self):
        texts = [
            'Loved "hey how are you"',
            'Loved "see you tomorrow"',
            'Liked "sounds good"',
            'Emphasized "important meeting"',
            "hey what are you doing tonight",
            "loved that idea",
            'Laughed at "haha funny"',
        ]
        words = analyze.top_words(texts)
        word_set = {item["word"] for item in words}
        assert "loved" not in word_set
        assert "liked" not in word_set
        assert "emphasized" not in word_set
        assert "hey" in word_set

    def test_reaction_only_messages_do_not_count(self):
        texts = ['Loved "hello"', 'Liked "world"']
        summary = analyze.summarize_topics(texts)
        assert summary["message_count"] == 2
        assert not any(item["word"] in {"loved", "liked"} for item in summary["words"])


class TestDatabaseReactions:
    def _make_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE handle (
                ROWID INTEGER PRIMARY KEY,
                id TEXT NOT NULL
            );
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY,
                chat_identifier TEXT,
                display_name TEXT
            );
            CREATE TABLE chat_handle_join (
                chat_id INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER,
                message_id INTEGER
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                text TEXT,
                attributedBody BLOB,
                is_from_me INTEGER,
                date INTEGER,
                handle_id INTEGER,
                associated_message_type INTEGER
            );
            """
        )
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES (1, '+15551234567', 'Alex')"
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1)")
        return conn

    def _insert_message(self, conn, rowid, text, associated_type=0, is_from_me=0):
        conn.execute(
            """
            INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (rowid, text, is_from_me, 700_000_000, associated_type),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)",
            (rowid,),
        )

    def test_reaction_rows_excluded_from_stats(self):
        conn = self._make_db()
        self._insert_message(conn, 1, "hey there", 0)
        self._insert_message(conn, 2, 'Loved "hey there"', 2000)
        self._insert_message(conn, 3, "see you soon", 0)

        stats = db.contact_stats(conn)
        assert stats[0]["total"] == 2

    def test_group_chat_stats_sql(self):
        conn = self._make_db()
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (2, '+15559876543')")
        conn.execute(
            """
            INSERT INTO chat (ROWID, chat_identifier, display_name)
            VALUES (2, 'group-chat', 'Friends')
            """
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (2, 1), (2, 2)")
        self._insert_message(conn, 10, "hello group", 0)
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (2, 10)")

        stats = db.group_chat_stats(conn)
        assert len(stats) == 1
        assert stats[0]["contact"] == "Friends"
        assert stats[0]["total"] == 1

    def test_reaction_rows_excluded_from_iter_messages(self):
        conn = self._make_db()
        self._insert_message(conn, 1, "real message", 0)
        self._insert_message(conn, 2, 'Liked "real message"', 2001)

        texts = [msg.text for msg in db.iter_messages(conn, dm_only=True)]
        assert texts == ["real message"]

    def test_duplicate_join_rows_count_once(self):
        conn = self._make_db()
        self._insert_message(conn, 1, "babes", 0)
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, 1)"
        )
        texts = db.messages_for_contact(conn, "+15551234567", limit=None)
        assert texts.count("babes") == 1
        assert analyze.top_words(texts)[0] == {"word": "babes", "count": 1}


class TestGCS:
    SAMPLE = [
        {"handle": "+15551234567", "name": "Alex", "text": "hello", "is_from_me": True},
        {"handle": "+15551234567", "name": "Alex", "text": "how are you", "is_from_me": False},
        {"handle": "+15559876543", "name": "Sam", "text": "coffee?", "is_from_me": True},
    ]

    def test_parse_json_array(self):
        payload = json.dumps(self.SAMPLE).encode()
        messages = gcs.parse_export_bytes(payload)
        assert len(messages) == 3
        assert messages[0].handle == "+15551234567"

    def test_parse_jsonl(self):
        payload = "\n".join(json.dumps(row) for row in self.SAMPLE).encode()
        messages = gcs.parse_export_bytes(payload)
        assert len(messages) == 3

    def test_aggregate_by_contact(self):
        messages = [gcs._coerce_message(row) for row in self.SAMPLE]
        messages = [m for m in messages if m]
        stats = gcs.aggregate_messages(messages)
        assert len(stats) == 2
        alex = next(s for s in stats if s.handle == "+15551234567")
        assert alex.total == 2
        assert alex.sent == 1
        assert alex.received == 1

    def test_merge_contact_stats(self):
        local = [
            {
                "handle": "+15551234567",
                "contact": "+15551234567",
                "total": 10,
                "sent": 6,
                "received": 4,
            }
        ]
        remote = [
            {
                "handle": "+15551234567",
                "contact": "+15551234567",
                "total": 5,
                "sent": 2,
                "received": 3,
                "last_date": datetime(2024, 6, 1),
            },
            {
                "handle": "+15550001111",
                "contact": "+15550001111",
                "total": 3,
                "sent": 1,
                "received": 2,
            },
        ]
        merged = gcs.merge_contact_stats(local, remote)
        alex = next(r for r in merged if r["handle"] == "+15551234567")
        assert alex["total"] == 15
        assert alex["source"] == "merged"
        assert set(alex["sources"]) == {"gcs", "local"}

    def test_gcs_status_unconfigured(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        status = gcs.gcs_status()
        assert status["configured"] is False

    def test_merge_texts(self):
        merged = gcs.merge_texts(["local one"], ["remote two"], limit=10)
        assert merged == ["local one", "remote two"]

    def test_merge_texts_dedupes_gcs_overlap(self):
        merged = gcs.merge_texts(["babes", "hello"], ["babes", "world"], limit=None)
        assert merged == ["babes", "hello", "world"]

    def test_index_chat_db(self, tmp_path):
        path = tmp_path / "chat.db"
        tmp = sqlite3.connect(path)
        tmp.row_factory = sqlite3.Row
        tmp.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                is_from_me INTEGER, date INTEGER, handle_id INTEGER,
                associated_message_type INTEGER
            );
            """
        )
        tmp.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
        tmp.execute(
            "INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES (1, '+15551234567', 'Alex')"
        )
        tmp.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1)")
        tmp.execute(
            """
            INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type)
            VALUES (1, 'hello from db', 1, 700000000, 1, 0)
            """
        )
        tmp.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, 1)")
        tmp.commit()
        tmp.close()

        stats = gcs.index_chat_db(path)
        assert len(stats) == 1
        assert stats[0].texts == ["hello from db"]

    def test_texts_for_handle_uses_cache(self, monkeypatch):
        monkeypatch.setenv("GCS_BUCKET", "demo-bucket")
        gcs._index_cache = gcs.GCSIndex(
            texts_by_handle={"+15551234567": ["cached message"]},
            contacts=[{"handle": "+15551234567", "total": 1}],
        )
        assert gcs.texts_for_handle("+15551234567") == ["cached message"]
        gcs._index_cache = None


class TestFlaskAPI:
    def test_gcs_contacts_requires_bucket(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        gcs._index_cache = None
        from app import app

        client = app.test_client()
        response = client.get("/api/gcs/contacts")
        assert response.status_code == 400
        assert "GCS_BUCKET" in response.get_json()["message"]

    def test_status_includes_gcs_block(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        gcs._index_cache = None
        from app import app

        client = app.test_client()
        response = client.get("/api/status")
        data = response.get_json()
        assert "gcs" in data
        assert data["gcs"]["configured"] is False
