from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .events import PlateEvent


def norm(plate: str) -> str:
    return "".join(c for c in plate.upper() if c.isalnum())


class Store:
    """Local history, watchlist and Telegram subscriptions (SQLite)."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sightings (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, camera_id TEXT NOT NULL,
                  camera_name TEXT, plate TEXT NOT NULL, confidence REAL, reads INTEGER, snapshot_path TEXT);
                CREATE INDEX IF NOT EXISTS s_ts ON sightings(ts);
                CREATE INDEX IF NOT EXISTS s_plate ON sightings(plate, ts);
                CREATE TABLE IF NOT EXISTS watchlist (
                  plate TEXT PRIMARY KEY, note TEXT, added_by TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS subscriptions (chat_id INTEGER PRIMARY KEY, title TEXT, ts REAL);
            """)

    def _q(self, sql, *a):
        with self.lock:
            return self.db.execute(sql, a).fetchall()

    def _x(self, sql, *a):
        with self.lock:
            cur = self.db.execute(sql, a)
            self.db.commit()
            return cur

    # --- sightings ---
    def add_sighting(self, e: PlateEvent) -> None:
        self._x("INSERT INTO sightings (ts,camera_id,camera_name,plate,confidence,reads,snapshot_path) VALUES (?,?,?,?,?,?,?)",
                e.timestamp, e.camera_id, e.camera_name, norm(e.plate), e.confidence, e.reads, e.snapshot_path)

    def last(self, n: int = 5, camera_id: str | None = None) -> list[sqlite3.Row]:
        n = max(1, min(int(n), 50))
        if camera_id:
            return self._q("SELECT * FROM sightings WHERE camera_id=? ORDER BY ts DESC LIMIT ?", camera_id, n)
        return self._q("SELECT * FROM sightings ORDER BY ts DESC LIMIT ?", n)

    def find(self, plate: str, n: int = 10) -> list[sqlite3.Row]:
        p = norm(plate)
        if not p:
            return []
        return self._q("SELECT * FROM sightings WHERE plate=? ORDER BY ts DESC LIMIT ?", p, max(1, min(n, 50)))

    def counts_today(self) -> tuple[int, int]:
        start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        r = self._q("SELECT COUNT(*) c, COUNT(DISTINCT plate) d FROM sightings WHERE ts>=?", start)[0]
        return r["c"], r["d"]

    # --- watchlist ---
    def watch(self, plate: str, note: str = "", by: str = "") -> bool:
        p = norm(plate)
        if not 2 <= len(p) <= 12:
            return False
        self._x("INSERT OR REPLACE INTO watchlist (plate,note,added_by,ts) VALUES (?,?,?,?)", p, note, by, time.time())
        return True

    def unwatch(self, plate: str) -> bool:
        return self._x("DELETE FROM watchlist WHERE plate=?", norm(plate)).rowcount > 0

    def watchlist(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM watchlist ORDER BY plate")

    def watch_note(self, plate: str) -> str | None:
        r = self._q("SELECT note FROM watchlist WHERE plate=?", norm(plate))
        return (r[0]["note"] or "") if r else None

    # --- telegram subscriptions ---
    def subscribe(self, chat_id: int, title: str = "") -> None:
        self._x("INSERT OR REPLACE INTO subscriptions (chat_id,title,ts) VALUES (?,?,?)", chat_id, title, time.time())

    def unsubscribe(self, chat_id: int) -> bool:
        return self._x("DELETE FROM subscriptions WHERE chat_id=?", chat_id).rowcount > 0

    def subscribers(self) -> list[int]:
        return [r["chat_id"] for r in self._q("SELECT chat_id FROM subscriptions")]

    def is_subscribed(self, chat_id: int) -> bool:
        return bool(self._q("SELECT 1 FROM subscriptions WHERE chat_id=?", chat_id))

    def purge(self, days: int) -> int:
        if days <= 0:
            return 0
        return self._x("DELETE FROM sightings WHERE ts<?", time.time() - days * 86400).rowcount
