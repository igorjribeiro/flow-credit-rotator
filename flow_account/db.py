"""Database layer for Flow Credit Rotator.

Provides SQLite initialization, WAL mode, pragmas, schema migrations,
and thread-safe context managers for transactions.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from flow_account.config import (
    get_db_path,
    get_default_daily_limit,
    get_default_strategy,
    get_default_timezone_name,
)
from flow_account.models import Mode


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    credential_ref TEXT NOT NULL DEFAULT '',
    daily_limit INTEGER NOT NULL,
    monthly_limit INTEGER,
    used_today INTEGER NOT NULL DEFAULT 0,
    used_month INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    last_used_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL,
    strategy TEXT NOT NULL,
    current_account_id INTEGER,
    default_daily_limit INTEGER NOT NULL,
    last_reset_date TEXT,
    last_reset_month TEXT,
    timezone TEXT NOT NULL,
    FOREIGN KEY (current_account_id) REFERENCES accounts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    used_today_after INTEGER NOT NULL,
    used_month_after INTEGER NOT NULL,
    mode TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_accounts_priority ON accounts(priority);
CREATE INDEX IF NOT EXISTS idx_usage_history_account ON usage_history(account_id);
CREATE INDEX IF NOT EXISTS idx_usage_history_timestamp ON usage_history(timestamp);
"""


class Database:
    """Encapsulates SQLite connection management and thread-safe transactions."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = get_db_path(db_path)
        self._lock = threading.RLock()
        self._is_memory = self.db_path == ":memory:" or "mode=memory" in self.db_path
        self._shared_conn: Optional[sqlite3.Connection] = None

        if not self._is_memory:
            db_file = Path(self.db_path).resolve()
            db_file.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure a new SQLite connection with pragmas."""
        uri = self.db_path.startswith("file:") or self._is_memory
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False,
            uri=uri,
        )
        conn.row_factory = sqlite3.Row

        # SQLite PRAGMAs for performance and durability
        if not self._is_memory:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.DatabaseError:
                pass
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        """Initialize database tables and initial system state."""
        with self._lock:
            if self._is_memory and self._shared_conn is None:
                self._shared_conn = self._create_connection()

            conn = self._shared_conn if self._shared_conn else self._create_connection()
            try:
                with conn:
                    # Check if database schema is already initialized
                    cursor = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_state';"
                    )
                    table_exists = cursor.fetchone() is not None

                    conn.executescript(SCHEMA_SQL)
                    # Insert initial system_state row only on fresh database initialization
                    if not table_exists:
                        conn.execute(
                            """
                            INSERT INTO system_state (
                                id, mode, strategy, current_account_id,
                                default_daily_limit, last_reset_date, last_reset_month, timezone
                            ) VALUES (1, ?, ?, NULL, ?, NULL, NULL, ?);
                            """,
                            (
                                Mode.AUTO.value,
                                get_default_strategy(),
                                get_default_daily_limit(),
                                get_default_timezone_name(),
                            ),
                        )
            finally:
                if not self._is_memory and conn != self._shared_conn:
                    conn.close()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a connection context manager."""
        with self._lock:
            if self._is_memory:
                yield self._shared_conn  # type: ignore
            else:
                conn = self._create_connection()
                try:
                    yield conn
                finally:
                    conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide an atomic transaction context manager with automatic rollback on error."""
        with self._lock:
            with self.connect() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    def close(self) -> None:
        """Close shared connections if open."""
        with self._lock:
            if self._shared_conn:
                self._shared_conn.close()
                self._shared_conn = None
