import json
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH, ensure_dirs, now_dubai
from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,          -- source:source_id
    source TEXT, source_id TEXT, url TEXT, title TEXT,
    make TEXT, model TEXT, trim TEXT, year INTEGER, km INTEGER, price INTEGER,
    specs TEXT, body TEXT, fuel TEXT, city TEXT, seller_type TEXT,
    description TEXT,
    matches INTEGER DEFAULT 0,     -- passed the criteria on its latest sighting
    notified INTEGER DEFAULT 0,    -- included in a report already
    flags TEXT,                    -- JSON: red flags / warnings at notification time
    first_seen TEXT, last_seen TEXT,
    dismissed INTEGER DEFAULT 0,   -- you tapped "Not interested": never reported again
    dismissed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_group ON listings(make, model, year);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);  -- Telegram update offset, last numbering
CREATE TABLE IF NOT EXISTS price_history (
    key TEXT, price INTEGER, seen_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    status TEXT,                   -- running | ok | failed
    scheduled INTEGER,
    summary TEXT, error TEXT
);
"""


def norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _prefix_match(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    return bool(a and b) and (a.startswith(b) or b.startswith(a))  # "Mercedes" vs "Mercedes Benz"


def same_car(a, b) -> bool:
    """Heuristic cross-site match: same make, model and year, mileage within 1% (min 300 km)."""
    if a.year != b.year or a.km is None or b.km is None:
        return False
    return (_prefix_match(a.make, b.make) and _prefix_match(a.model, b.model)
            and abs(a.km - b.km) <= max(300, 0.01 * max(a.km, b.km)))


class DB:
    def __init__(self, path=DB_PATH):
        ensure_dirs()
        self.conn = sqlite3.connect(path, timeout=30)  # the Telegram sync task may write at the same time
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # databases created before these columns existed
        for table, col, typ in (("listings", "dismissed", "INTEGER DEFAULT 0"), ("listings", "dismissed_at", "TEXT"),
                                ("listings", "notified_price", "INTEGER"), ("runs", "mode", "TEXT")):
            if col not in {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.conn.commit()

    def get_kv(self, k: str, default=None):
        row = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def set_kv(self, k: str, v: str):
        self.conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, v))
        self.conn.commit()

    def set_dismissed(self, key: str, dismissed: bool) -> bool:
        """Returns False if the listing isn't in the database."""
        cur = self.conn.execute(
            "UPDATE listings SET dismissed=?, dismissed_at=? WHERE key=?",
            (int(dismissed), now_dubai().isoformat(timespec="seconds") if dismissed else None, key))
        self.conn.commit()
        return cur.rowcount > 0

    def get(self, key: str):
        return self.conn.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()

    def upsert(self, l: Listing, matches: bool, now: str):
        """Insert or refresh a listing. Returns the previous row (or None if new)."""
        prev = self.get(l.key)
        if prev is None:
            self.conn.execute(
                """INSERT INTO listings (key, source, source_id, url, title, make, model, trim, year, km,
                   price, specs, body, fuel, city, seller_type, description, matches, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (l.key, l.source, l.source_id, l.url, l.title, l.make, l.model, l.trim, l.year, l.km,
                 l.price, l.specs, l.body, l.fuel, l.city, l.seller_type, l.description, int(matches), now, now),
            )
            self.conn.execute("INSERT INTO price_history VALUES (?,?,?)", (l.key, l.price, now))
        else:
            self.conn.execute(
                """UPDATE listings SET url=?, title=?, trim=?, km=?, price=?, city=?, matches=?, last_seen=?,
                   description=COALESCE(?, description) WHERE key=?""",
                (l.url, l.title, l.trim, l.km, l.price, l.city, int(matches), now, l.description, l.key),
            )
            if l.price and l.price != prev["price"]:
                self.conn.execute("INSERT INTO price_history VALUES (?,?,?)", (l.key, l.price, now))
        return prev

    def reset_notified(self, key: str):
        self.conn.execute("UPDATE listings SET notified=0, notified_price=NULL WHERE key=?", (key,))

    def mark_notified(self, l: Listing):
        """notified_price = the price you were last told about; price drops are measured against it, so
        the hourly watch and the daily run can both see a drop no matter which one ran last."""
        self.conn.execute(
            "UPDATE listings SET notified=1, notified_price=?, flags=? WHERE key=?",
            (l.price, json.dumps({"red_flags": l.red_flags, "warnings": l.warnings}), l.key),
        )

    def find_duplicate(self, l: Listing):
        """Key of an already-notified listing on another site that looks like the same car."""
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE notified=1 AND source<>? AND year=?", (l.source, l.year)).fetchall()
        for r in rows:
            if same_car(l, Listing(source=r["source"], source_id=r["source_id"], url=r["url"], title=r["title"],
                                   make=r["make"], model=r["model"], year=r["year"], km=r["km"])):
                return r["key"]
        return None

    def comparables(self, l: Listing, window_days: int, specs: list):
        """Prices of other listings with the same make, model and year seen recently."""
        since = (now_dubai() - timedelta(days=window_days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT key, model, price, specs FROM listings WHERE lower(make)=lower(?) AND year=? "
            "AND last_seen>=? AND key<>? AND price>0 "
            "AND (matches=1 OR lower(fuel) LIKE '%hybrid%')",  # compare hybrids with hybrids only
            (l.make, l.year, since, l.key),
        ).fetchall()
        allowed = {s.lower() for s in specs}
        return [r["price"] for r in rows
                if norm(r["model"]) == norm(l.model) and (r["specs"] or "").lower() in allowed]

    # --- run bookkeeping ---
    def start_run(self, scheduled: bool, mode: str = "daily") -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, status, scheduled, mode) VALUES (?, 'running', ?, ?)",
            (now_dubai().isoformat(timespec="seconds"), int(scheduled), mode),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, summary: dict = None, error: str = None):
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, summary=?, error=? WHERE id=?",
            (now_dubai().isoformat(timespec="seconds"), status,
             json.dumps(summary) if summary else None, error, run_id),
        )
        self.conn.commit()

    def last_successful_run(self):
        row = self.conn.execute(
            "SELECT started_at FROM runs WHERE status='ok' AND COALESCE(mode, 'daily')='daily' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return datetime.fromisoformat(row["started_at"]) if row else None

    def commit(self):
        self.conn.commit()
