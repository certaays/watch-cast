import json
import sqlite3
from pathlib import Path
from typing import Any


class StateStorage:
    """Lightweight SQLite key-value store for persisting checker state."""

    def __init__(self, db_path: str | Path = "data/state.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_state (
                    job_name TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def get_state(self, job_name: str) -> dict[str, Any]:
        """Return saved state dict for a job, or empty dict if none exists."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM job_state WHERE job_name = ?",
                (job_name,),
            ).fetchone()
        if row is None:
            return {}
        return json.loads(row[0])

    def set_state(self, job_name: str, state: dict[str, Any]) -> None:
        """Persist state dict for a job."""
        payload = json.dumps(state)
        with sqlite3.connect(self.db_path) as conn:
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

