from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


class StateStorage:
    """Lightweight SQLite store for checker state, run history, and price history."""

    def __init__(self, db_path: str | Path = "data/state.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_state (
                    job_name TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trigger TEXT NOT NULL DEFAULT 'scheduled',
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    ran_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_runs_name_id ON job_runs (job_name, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_runs_id ON job_runs (id DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name TEXT NOT NULL,
                    price REAL NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_history_name_id ON price_history (job_name, id DESC)"
            )
            conn.commit()

    def get_state(self, job_name: str) -> dict[str, Any]:
        """Return saved state dict for a job, or empty dict if none exists."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM job_state WHERE job_name = ?",
                (job_name,),
            ).fetchone()
        if row is None:
            return {}
        return json.loads(row["state_json"])

    def set_state(self, job_name: str, state: dict[str, Any]) -> None:
        """Persist state dict for a job."""
        payload = json.dumps(state)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_state (job_name, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(job_name) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (job_name, payload),
            )
            conn.commit()

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Return parsed state dicts for all stored jobs."""
        with self._connect() as conn:
            rows = conn.execute("SELECT job_name, state_json FROM job_state").fetchall()
        return {row["job_name"]: json.loads(row["state_json"]) for row in rows}

    def record_run(
        self,
        job_name: str,
        status: str,
        *,
        trigger: str = "scheduled",
        message: str = "",
        error: str | None = None,
        duration_ms: int = 0,
        ran_at: str | None = None,
    ) -> int:
        """Record a job execution in run history and prune rows beyond the latest 1000."""
        timestamp = ran_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO job_runs (job_name, status, trigger, message, error, duration_ms, ran_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_name, status, trigger, message, error, duration_ms, timestamp),
            )
            run_id = int(cursor.lastrowid)
            conn.execute(
                """
                DELETE FROM job_runs
                WHERE id NOT IN (
                    SELECT id FROM job_runs ORDER BY id DESC LIMIT 1000
                )
                """
            )
            conn.commit()
        return run_id

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_name": row["job_name"],
            "status": row["status"],
            "trigger": row["trigger"],
            "message": row["message"],
            "error": row["error"],
            "duration_ms": row["duration_ms"],
            "ran_at": row["ran_at"],
        }

    def get_runs(
        self,
        limit: int = 50,
        job_name: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent job runs newest-first, optionally filtered by job_name and status."""
        clauses: list[str] = []
        params: list[Any] = []
        if job_name:
            clauses.append("job_name = ?")
            params.append(job_name)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, job_name, status, trigger, message, error, duration_ms, ran_at
                FROM job_runs
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_latest_runs_by_job(self) -> dict[str, dict[str, Any]]:
        """Return a mapping of job_name to its most recent run record."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.job_name, r.status, r.trigger, r.message, r.error, r.duration_ms, r.ran_at
                FROM job_runs r
                INNER JOIN (
                    SELECT job_name, MAX(id) AS max_id
                    FROM job_runs
                    GROUP BY job_name
                ) latest ON r.id = latest.max_id
                """
            ).fetchall()
        return {row["job_name"]: self._row_to_run(row) for row in rows}

    def get_summary_stats(self) -> dict[str, int]:
        """Return summary counts of total runs and 24-hour notifications/errors."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_runs,
                    COALESCE(SUM(CASE WHEN status = 'notified' AND ran_at >= ? THEN 1 ELSE 0 END), 0) AS notified_24h,
                    COALESCE(SUM(CASE WHEN status = 'error' AND ran_at >= ? THEN 1 ELSE 0 END), 0) AS errors_24h
                FROM job_runs
                """,
                (cutoff, cutoff),
            ).fetchone()
        if row is None:
            return {"total_runs": 0, "notified_24h": 0, "errors_24h": 0}
        return {
            "total_runs": int(row["total_runs"]),
            "notified_24h": int(row["notified_24h"]),
            "errors_24h": int(row["errors_24h"]),
        }

    def record_price(
        self,
        job_name: str,
        price: float,
        recorded_at: str | None = None,
    ) -> None:
        """Record a price observation and keep at most the latest 200 entries per job."""
        timestamp = recorded_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO price_history (job_name, price, recorded_at)
                VALUES (?, ?, ?)
                """,
                (job_name, float(price), timestamp),
            )
            conn.execute(
                """
                DELETE FROM price_history
                WHERE job_name = ?
                  AND id NOT IN (
                      SELECT id FROM price_history
                      WHERE job_name = ?
                      ORDER BY id DESC
                      LIMIT 200
                  )
                """,
                (job_name, job_name),
            )
            conn.commit()

    def get_price_history(self, job_name: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return chronological (oldest to newest) price observations for a job."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT price, recorded_at
                FROM (
                    SELECT id, price, recorded_at
                    FROM price_history
                    WHERE job_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (job_name, max(1, limit)),
            ).fetchall()
        return [
            {"price": float(row["price"]), "recorded_at": row["recorded_at"]}
            for row in rows
        ]

    def get_all_price_histories(self, limit_per_job: int = 30) -> dict[str, list[dict[str, Any]]]:
        """Return chronological price observations grouped by job_name."""
        with self._connect() as conn:
            job_rows = conn.execute(
                "SELECT DISTINCT job_name FROM price_history"
            ).fetchall()
        return {
            row["job_name"]: self.get_price_history(row["job_name"], limit=limit_per_job)
            for row in job_rows
        }

    def rename_job(self, old_name: str, new_name: str) -> None:
        """Rename a job across state, run history, and price history in one transaction."""
        if old_name == new_name:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM job_state WHERE job_name = ?", (new_name,))
            conn.execute(
                "UPDATE job_state SET job_name = ? WHERE job_name = ?",
                (new_name, old_name),
            )
            conn.execute(
                "UPDATE job_runs SET job_name = ? WHERE job_name = ?",
                (new_name, old_name),
            )
            conn.execute(
                "UPDATE price_history SET job_name = ? WHERE job_name = ?",
                (new_name, old_name),
            )
            conn.commit()

    def delete_job_data(self, job_name: str) -> None:
        """Delete state and price history for a removed job while preserving run audit logs."""
        with self._connect() as conn:
            conn.execute("DELETE FROM job_state WHERE job_name = ?", (job_name,))
            conn.execute("DELETE FROM price_history WHERE job_name = ?", (job_name,))
            conn.commit()
