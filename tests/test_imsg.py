import json
import sqlite3
from datetime import datetime

import pytest

from imsg import analyze, db, gcs
from imsg.contacts import ContactDirectory
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

    def test_message_text_falls_back_to_attributed_body(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                is_from_me INTEGER, date INTEGER, handle_id INTEGER,
                associated_message_type INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO message (ROWID, text, attributedBody, is_from_me, associated_message_type) VALUES (1, 'kimmessage', ?, 1, 0)",
            (b"ignored",),
        )
        row = conn.execute("SELECT * FROM message WHERE ROWID = 1").fetchone()
        monkeypatch.setattr(db, "decode_attributed_body", lambda _blob: "hello from blob")
        assert db.message_text(row) == "hello from blob"

    def test_handles_for_contact_merges_same_name(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            """
        )
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551111111'), (2, 'emma@example.com')")
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES (1, '+15551111111', 'Emma Cruz'), (2, 'emma@example.com', 'Emma Cruz')"
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1), (2, 2)")
        directory = ContactDirectory(conn)
        handles = db.handles_for_contact(conn, directory, "+15551111111")
        assert set(handles) == {"+15551111111", "emma@example.com"}

    def test_person_message_counts_skips_decoded_when_disabled(self):
        conn = self._make_db()
        for i in range(1, 6):
            self._insert_message(conn, i, f"message {i}", 0)
        directory = ContactDirectory(conn)
        quick = db.person_message_counts(
            conn, directory, "+15551234567", count_decoded=False
        )
        assert quick["total"] == 5
        assert quick["decoded"] is None
        deep = db.person_message_counts(
            conn, directory, "+15551234567", count_decoded=True
        )
        assert deep["decoded"] == 5

    def test_messages_for_person_respects_limit(self):
        conn = self._make_db()
        for i in range(1, 11):
            self._insert_message(conn, i, f"word{i}", 0)
        directory = ContactDirectory(conn)
        quick = db.messages_for_person(conn, directory, "+15551234567", limit=3)
        assert len(quick) == 3
        full = db.messages_for_person(conn, directory, "+15551234567", limit=None)
        assert len(full) == 10


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


class TestAttributedBodyDecoding(TestDatabaseReactions):
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

    def test_keyed_archive_blob_returns_none(self):
        # NSKeyedArchiver payloads leak $archiver/$objects/$top/$version tokens.
        blob = (
            b"bplist00\xd4\x01\x02\x03\x04$version$archiver$top$objects"
            b"NSKeyedArchiver good morning ems"
        )
        assert db.decode_attributed_body(blob) is None

    def test_archiver_tokens_filtered_from_text_check(self):
        assert not db.looks_like_message_text("versiony archivert topx objects")
        assert not db.looks_like_message_text("$archiver")
        assert db.looks_like_message_text("good morning babes")

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


class TestContactTimeline(TestDatabaseReactions):
    def _insert_at(self, conn, rowid, when, associated_type=0, chat_id=1, handle_id=1):
        ts = int((when - db.APPLE_EPOCH).total_seconds())
        conn.execute(
            """
            INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type)
            VALUES (?, ?, 0, ?, ?, ?)
            """,
            (rowid, f"message {rowid}", ts, handle_id, associated_type),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat_id, rowid),
        )

    def test_groups_by_month(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2024, 1, 15))
        self._insert_at(conn, 2, datetime(2024, 1, 20))
        self._insert_at(conn, 3, datetime(2024, 2, 5))

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="month")
        assert buckets == {"2024-01": 2, "2024-02": 1}

    def test_groups_by_year(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2023, 11, 1))
        self._insert_at(conn, 2, datetime(2024, 1, 15))
        self._insert_at(conn, 3, datetime(2024, 12, 20))

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="year")
        assert buckets == {"2023": 1, "2024": 2}

    def test_groups_by_day(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2024, 1, 15))
        self._insert_at(conn, 2, datetime(2024, 1, 15))
        self._insert_at(conn, 3, datetime(2024, 1, 16))

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="day")
        assert buckets == {"2024-01-15": 2, "2024-01-16": 1}

    def test_groups_by_week(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2024, 1, 15))  # Mon, ISO week 3
        self._insert_at(conn, 2, datetime(2024, 1,17))   # Wed, same week
        self._insert_at(conn, 3, datetime(2024, 1, 22))  # Mon, ISO week 4

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="week")
        assert buckets == {"2024-W03": 2, "2024-W04": 1}

    def test_dedupes_duplicate_chat_message_join_rows(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2024, 3, 1))
        # A stray extra join row for the same message shouldn't double-count it.
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, 1)")

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="month")
        assert buckets == {"2024-03": 1}

    def test_excludes_reaction_rows(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        self._insert_at(conn, 1, datetime(2024, 4, 1), associated_type=0)
        self._insert_at(conn, 2, datetime(2024, 4, 1), associated_type=2000)  # tapback

        buckets = db.contact_timeline(conn, directory, "+15551234567", bucket="month")
        assert buckets == {"2024-04": 1}

    def test_unknown_contact_returns_empty(self):
        conn = self._make_db()
        directory = ContactDirectory(conn)
        buckets = db.contact_timeline(conn, directory, "+15559999999", bucket="month")
        assert buckets == {}


class TestTimelineEndpoint:
    def _make_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
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
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551111111'), (2, '+15552222222')")
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES "
            "(1, '+15551111111', 'Alex'), (2, '+15552222222', 'Sam')"
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1), (2, 2)")

        ts = int((datetime(2024, 5, 1) - db.APPLE_EPOCH).total_seconds())
        for rowid in (1, 2, 3):
            conn.execute(
                "INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type) "
                "VALUES (?, ?, 0, ?, 1, 0)",
                (rowid, f"alex {rowid}", ts),
            )
            conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (rowid,))
        conn.execute(
            "INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type) "
            "VALUES (4, 'sam 1', 0, ?, 2, 0)",
            (ts,),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (2, 4)")
        return conn

    def _patch(self, monkeypatch):
        import app as app_module

        conn = self._make_db()
        monkeypatch.setattr(app_module.db, "connect", lambda *a, **kw: conn)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        app_module.gcs._index_cache = None
        return app_module.app.test_client()

    def test_respects_limit(self, monkeypatch):
        client = self._patch(monkeypatch)
        response = client.get("/api/timeline?limit=1&bucket=month")
        assert response.status_code == 200
        data = response.get_json()
        assert data["bucket"] == "month"
        assert len(data["contacts"]) == 1
        assert data["contacts"][0]["name"] == "Alex"
        assert data["contacts"][0]["total"] == 3
        assert data["contacts"][0]["series"] == [{"period": "2024-05", "count": 3}]

    def test_stable_ordering_by_total_desc(self, monkeypatch):
        client = self._patch(monkeypatch)
        response = client.get("/api/timeline?limit=25&bucket=month")
        data = response.get_json()
        names = [c["name"] for c in data["contacts"]]
        assert names == ["Alex", "Sam"]

    def test_yearly_bucket_param(self, monkeypatch):
        client = self._patch(monkeypatch)
        response = client.get("/api/timeline?limit=25&bucket=year")
        data = response.get_json()
        assert data["bucket"] == "year"
        alex = next(c for c in data["contacts"] if c["name"] == "Alex")
        assert alex["series"] == [{"period": "2024", "count": 3}]

    def test_daily_bucket_param(self, monkeypatch):
        client = self._patch(monkeypatch)
        response = client.get("/api/timeline?limit=25&bucket=day")
        data = response.get_json()
        assert data["bucket"] == "day"
        alex = next(c for c in data["contacts"] if c["name"] == "Alex")
        assert alex["series"] == [{"period": "2024-05-01", "count": 3}]

    def test_weekly_bucket_param(self, monkeypatch):
        client = self._patch(monkeypatch)
        response = client.get("/api/timeline?limit=25&bucket=week")
        data = response.get_json()
        assert data["bucket"] == "week"
        alex = next(c for c in data["contacts"] if c["name"] == "Alex")
        assert alex["series"] == [{"period": "2024-W18", "count": 3}]


class TestGroupDeepScan:
    def _make_db(self, n_messages=25):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
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
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551111111'), (2, '+15552222222')")
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES (1, 'group-chat', 'Friends')"
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1), (1, 2)")
        for i in range(1, n_messages + 1):
            conn.execute(
                "INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type) "
                "VALUES (?, ?, 0, ?, 1, 0)",
                (i, f"group message {i}", 700_000_000 + i),
            )
            conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (i,))
        # A reaction row (newest by date) and a stray duplicate join — neither should count.
        reaction_id = n_messages + 1
        conn.execute(
            "INSERT INTO message (ROWID, text, is_from_me, date, handle_id, associated_message_type) "
            "VALUES (?, ?, 0, ?, 1, 2000)",
            (reaction_id, 'Loved "group message 1"', 700_000_000 + reaction_id),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (reaction_id,))
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, 1)")
        return conn

    def test_group_message_counts_excludes_reactions_and_dedupes(self):
        conn = self._make_db(n_messages=5)
        assert db.group_message_counts(conn, "group-chat") == {"total": 5}

    def test_group_message_counts_matches_display_name(self):
        conn = self._make_db(n_messages=3)
        assert db.group_message_counts(conn, "Friends") == {"total": 3}

    def _client(self, monkeypatch, n_messages=25):
        import app as app_module

        conn = self._make_db(n_messages=n_messages)
        monkeypatch.setattr(app_module.db, "connect", lambda *a, **kw: conn)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        app_module.gcs._index_cache = None
        return app_module.app.test_client(), app_module.QUICK_MESSAGE_LIMIT

    def test_quick_preview_reports_real_total_with_limited_decode(self, monkeypatch):
        client, quick_limit = self._client(monkeypatch, n_messages=25)
        response = client.get("/api/contact/group-chat")
        data = response.get_json()
        assert data["is_group"] is True
        assert data["deep"] is False
        assert data["topics"]["messages_total"] == 25
        assert data["topics"]["messages_decoded"] == quick_limit

    def test_deep_scan_returns_every_group_message(self, monkeypatch):
        client, _ = self._client(monkeypatch, n_messages=25)
        response = client.get("/api/contact/group-chat?deep=1")
        data = response.get_json()
        assert data["deep"] is True
        assert data["topics"]["messages_total"] == 25
        assert data["topics"]["messages_decoded"] == 25


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


class TestTimeEstimate:
    def test_estimate_uses_sent_and_received_rates(self):
        from imsg.time_estimate import estimate_seconds, format_duration

        assert estimate_seconds(sent=10, received=10) == 10 * 20 + 10 * 12
        assert format_duration(3600) == "1 hr"
        assert format_duration(90 * 60) == "1.5 hr"

    def test_database_summary_includes_time(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                is_from_me INTEGER, date INTEGER, handle_id INTEGER,
                associated_message_type INTEGER
            );
            """
        )
        conn.execute("INSERT INTO message (ROWID, text, is_from_me) VALUES (1, 'hi', 1)")
        conn.execute("INSERT INTO message (ROWID, text, is_from_me) VALUES (2, 'hey', 0)")
        conn.execute("INSERT INTO message (ROWID, text, is_from_me) VALUES (3, 'yo', 0)")

        summary = db.database_summary(conn)
        assert summary["messages"] == 3
        assert summary["sent"] == 1
        assert summary["received"] == 2
        assert summary["time_seconds"] == 1 * 20 + 2 * 12
        assert summary["time_display"] == "1 min"
        assert "20s sent" in summary["time_note"]


class TestBackgroundMessages:
    def test_random_background_messages(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                is_from_me INTEGER, date INTEGER, handle_id INTEGER,
                associated_message_type INTEGER, service TEXT
            );
            """
        )
        conn.execute("INSERT INTO message (ROWID, text, is_from_me, service) VALUES (1, 'hello there', 1, 'iMessage')")
        conn.execute("INSERT INTO message (ROWID, text, is_from_me, service) VALUES (2, 'see you tonight', 0, 'SMS')")
        conn.execute("INSERT INTO message (ROWID, text, is_from_me, service) VALUES (3, 'Loved \"x\"', 0, 'iMessage')")

        msgs = db.random_background_messages(conn, limit=10)
        texts = {m["text"] for m in msgs}
        types = {m["type"] for m in msgs}
        assert "hello there" in texts
        assert "see you tonight" in texts
        assert "Loved" not in " ".join(texts)
        assert "sent" in types
        assert "sms" in types
