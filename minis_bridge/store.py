"""Durable message queue for the Minis <-> Claude relay.

The prototype kept the queue in a JSON file and rewrote it on every change,
which loses messages when two writers race (the phone runs the bridge, the
keepalive and Claude Code concurrently).  SQLite gives atomic appends, indexed
reads and crash safety with no extra dependency (stdlib).
"""
import json
import os
import sqlite3
import threading
import time
import uuid


class RelayStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY, box TEXT NOT NULL, ts REAL NOT NULL,
                        sender TEXT, recipient TEXT, text TEXT NOT NULL, meta TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_box_ts ON messages(box, ts)")

    def push(self, box, text, sender="minis", recipient="claude", meta=None):
        mid = uuid.uuid4().hex[:12]
        ts = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO messages (id, box, ts, sender, recipient, text, meta)"
                         " VALUES (?,?,?,?,?,?,?)",
                         (mid, box, ts, sender, recipient, text,
                          json.dumps(meta or {}, ensure_ascii=False)))
        return {"id": mid, "box": box, "ts": ts, "from": sender, "to": recipient, "text": text}

    def read(self, box, since=0, wait=0, limit=100):
        """Return messages newer than the ``since`` timestamp cursor."""
        deadline = time.time() + float(wait or 0)
        while True:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, box, ts, sender, recipient, text, meta FROM messages"
                    " WHERE box=? AND ts > ? ORDER BY ts ASC LIMIT ?",
                    (box, float(since or 0), int(limit))).fetchall()
            if rows or time.time() >= deadline:
                items = [dict(r) for r in rows]
                for it in items:
                    it["from"] = it.pop("sender")
                    it["to"] = it.pop("recipient")
                cursor = items[-1]["ts"] if items else float(since or 0)
                return {"count": len(items), "cursor": cursor, "items": items}
            time.sleep(0.7)

    def count(self, box=None):
        with self._lock, self._connect() as conn:
            if box:
                row = conn.execute("SELECT COUNT(*) c FROM messages WHERE box=?", (box,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()
            return row["c"]

    def prune(self, keep_per_box=200):
        """Keep the queue small - it lives on a phone."""
        with self._lock, self._connect() as conn:
            for box in ("inbox", "outbox"):
                rows = conn.execute("SELECT id FROM messages WHERE box=? ORDER BY ts DESC"
                                    " LIMIT -1 OFFSET ?", (box, int(keep_per_box))).fetchall()
                if rows:
                    conn.executemany("DELETE FROM messages WHERE id=?", [(r["id"],) for r in rows])
        return True
