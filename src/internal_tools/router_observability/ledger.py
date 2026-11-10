"""Content-addressed immutable SQLite report snapshots."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def write_snapshot(db_path: Path, report: dict) -> str:
    """Import identical reports once; never update an existing snapshot."""
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    snapshot_id = hashlib.sha256(payload.encode()).hexdigest()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS snapshots ("
            "snapshot_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, "
            "report_json TEXT NOT NULL, created_at TEXT NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE "
            "ON snapshots BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE "
            "ON snapshots BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END"
        )
        connection.execute(
            "INSERT OR IGNORE INTO snapshots(snapshot_id,schema_version,report_json) "
            "VALUES (?,?,?)",
            (snapshot_id, report["schema_version"], payload),
        )
    return snapshot_id


def _delivery_table(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS exported_snapshots ("
        "snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(snapshot_id), "
        "exported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


def pending_snapshots(db_path: Path) -> list[tuple[str, dict]]:
    """Return immutable undelivered snapshots, oldest first."""
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(db_path)) as connection, connection:
        _delivery_table(connection)
        rows = connection.execute(
            "SELECT s.snapshot_id,s.report_json FROM snapshots s LEFT JOIN "
            "exported_snapshots e ON s.snapshot_id=e.snapshot_id "
            "WHERE e.snapshot_id IS NULL ORDER BY s.created_at,s.snapshot_id"
        ).fetchall()
    return [(key, json.loads(payload)) for key, payload in rows]


def mark_exported(db_path: Path, snapshot_id: str) -> None:
    """Acknowledge delivery separately from immutable report content."""
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _delivery_table(connection)
        connection.execute(
            "INSERT OR IGNORE INTO exported_snapshots(snapshot_id) VALUES (?)",
            (snapshot_id,),
        )
