"""
Developer B — SQLite Event Database Module (Module B4)
======================================================
Provides three functions consumed by backend/app.py:

  init_db()          — Creates the events table if it does not yet exist.
                       Idempotent; safe to call multiple times.

  insert_event(d)    — Inserts one detection event row.
                       Opens a fresh connection each call to avoid Flask
                       dev-server thread-safety issues (SQLite connections are
                       not safe to share across threads with the default
                       check_same_thread=True setting).  Failures are caught
                       and printed; they never propagate to the caller.

  get_recent_events(limit) — Queries the most recent `limit` rows.
                       Same per-call connection pattern.  Returns [] on error.

NOTE: events.db is excluded from version control via .gitignore (*.db rule).
Only this Python module is committed.

Schema (no bbox — not useful to persist; the video stream already shows it):
  events (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp     TEXT    NOT NULL,
      class         TEXT    NOT NULL,
      confidence    REAL    NOT NULL,
      severity      TEXT    NOT NULL,
      snapshot_path TEXT    DEFAULT NULL
  )
"""

import os
import sqlite3

# ---------------------------------------------------------------------------
# DB_PATH — resolved relative to THIS FILE's location, not the CWD.
#
# This file lives at: backend/database/db.py
# So: os.path.dirname(os.path.abspath(__file__)) == .../backend/database/
# events.db will be created alongside this module at runtime.
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.db")

# DDL used by init_db() — stored as a module-level constant for clarity.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    class         TEXT    NOT NULL,
    confidence    REAL    NOT NULL,
    severity      TEXT    NOT NULL,
    snapshot_path TEXT,
    report_path   TEXT
);
"""


def init_db() -> None:
    """
    Create the events table if it does not already exist, and ensure
    snapshot_path and report_path columns are present for existing databases.

    Opens its own connection, runs CREATE TABLE IF NOT EXISTS (fully
    idempotent), verifies/adds the snapshot_path and report_path columns, commits, and
    immediately closes.  Safe to call at import time and safe to call again
    on every app restart.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_CREATE_TABLE_SQL)

        # Migration helper: Check if columns exist in an already created events table
        cursor = conn.execute("PRAGMA table_info(events)")
        columns = [row[1] for row in cursor.fetchall()]
        if "snapshot_path" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN snapshot_path TEXT")
        if "report_path" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN report_path TEXT")

        conn.commit()
    finally:
        conn.close()


def insert_event(
    event: dict,
    snapshot_path: str | None = None,
    report_path: str | None = None,
) -> None:
    """
    Persist one detection event to the database.

    Only "timestamp", "class", "confidence", "severity", and optional
    "snapshot_path" / "report_path" are stored.
    "bbox" is intentionally omitted from the schema — it is already rendered
    live on the video stream overlay and does not need to be queryable.

    A brand-new sqlite3 connection is opened for each call.  This is
    intentional: Flask's dev server is multi-threaded, and sharing a single
    global connection would raise `ProgrammingError: SQLite objects created in
    a thread can only be used in that same thread` for any thread that didn't
    create it.  Per-call connections are the simplest correct approach for a
    hackathon prototype.

    Any sqlite3 exception is caught, printed to stdout, and swallowed — a
    logging failure must never crash the video stream or any API route.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            # Check if paths were passed as args or in event dict
            snap = snapshot_path if snapshot_path is not None else event.get("snapshot_path")
            rep = report_path if report_path is not None else event.get("report_path")
            conn.execute(
                "INSERT INTO events (timestamp, class, confidence, severity, snapshot_path, report_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event["timestamp"],
                    event["class"],
                    float(event["confidence"]),
                    event["severity"],
                    snap,
                    rep,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Belt-and-suspenders: catch connection failures too (e.g. disk full,
        # permissions error).  Print clearly so it shows in the Flask log.
        print(f"[db.insert_event] Failed to persist event: {exc!r}  event={event!r}")


def get_recent_events(limit: int = 20) -> list:
    """
    Return the `limit` most recently inserted events as a list of dicts.

    Each dict has keys: "id", "timestamp", "class", "confidence", "severity",
    "snapshot_path", "report_path".
    Returns an empty list on any failure (query errors must not 500 the caller).

    Row ordering: id DESC ensures newest-first; the caller can reverse if needed.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row  # enables dict-style column access
        try:
            cursor = conn.execute(
                "SELECT id, timestamp, class, confidence, severity, snapshot_path, report_path "
                "FROM events "
                "ORDER BY id DESC "
                "LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        # Convert sqlite3.Row objects to plain dicts for JSON serialisation.
        return [
            {
                "id":            row["id"],
                "timestamp":     row["timestamp"],
                "class":         row["class"],
                "confidence":    row["confidence"],
                "severity":      row["severity"],
                "snapshot_path": row["snapshot_path"],
                "report_path":   row["report_path"],
            }
            for row in rows
        ]
    except Exception as exc:
        print(f"[db.get_recent_events] Query failed: {exc!r}")
        return []


def get_event_by_id(event_id: int) -> dict | None:
    """
    Look up a single event row by its primary key ID.
    Returns dict if found, None otherwise or on error.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT id, timestamp, class, confidence, severity, snapshot_path, report_path "
                "FROM events "
                "WHERE id = ?",
                (event_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "id":            row["id"],
                "timestamp":     row["timestamp"],
                "class":         row["class"],
                "confidence":    row["confidence"],
                "severity":      row["severity"],
                "snapshot_path": row["snapshot_path"],
                "report_path":   row["report_path"],
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"[db.get_event_by_id] Query failed: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Module-level initialisation — runs once on first import.
# Guarantees the table exists before any insert_event() call is made,
# regardless of which thread or route triggers the first import.
# ---------------------------------------------------------------------------
init_db()

