"""SQLite database connection and transaction management."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Generator, Sequence

from sniper.storage.migrations import (
    apply_migrations,
    get_current_version,
    rollback_migration,
)


@dataclass(frozen=True)
class ExecutionResult:
    """Stable metadata result from executing a DML or DDL statement.

    Attributes:
        rowcount: Number of rows affected by the statement (-1 if not applicable).
        lastrowid: Row ID of the last inserted row, or None if not an INSERT.
    """
    rowcount: int
    lastrowid: int | None = None


_INSERT_OR_REPLACE_PATTERN = re.compile(
    r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*(?:INSERT|REPLACE)\b",
    re.IGNORECASE | re.DOTALL,
)


class Database:
    """Thread-safe SQLite database manager for read-only market monitor storage.

    Guarantees:
    - Foreign keys strictly enabled on every connection.
    - WAL journal mode and normal synchronous mode for file-backed databases.
    - Explicit transaction boundary management with automatic rollback on error.
    - Parameterized query enforcement.
    - Thread-safe operations via re-entrant lock.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._db_path = str(db_path)
        self._is_memory = self._db_path == ":memory:" or ":memory:" in self._db_path
        self._timeout = timeout_seconds
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection and configure SQLite pragmas."""
        with self._lock:
            if self._conn is not None:
                return

            self._conn = sqlite3.connect(
                self._db_path,
                timeout=self._timeout,
                check_same_thread=False,
                isolation_level=None,  # Explicit transaction control without python DDL interference
            )
            self._conn.row_factory = sqlite3.Row

            # Enforce foreign keys on every connection
            self._conn.execute("PRAGMA foreign_keys = ON;")

            # Configure performance pragmas for persistent file-backed databases
            if not self._is_memory:
                self._conn.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)};")
                try:
                    mode = self._conn.execute("PRAGMA journal_mode;").fetchone()[0]
                    if str(mode).lower() != "wal":
                        self._conn.execute("PRAGMA journal_mode = WAL;")
                except sqlite3.OperationalError:
                    pass
                self._conn.execute("PRAGMA synchronous = NORMAL;")

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the underlying SQLite connection."""
        if self._conn is None:
            raise RuntimeError("Database connection is closed.")
        return self._conn

    @property
    def is_memory(self) -> bool:
        """Return True if database is in-memory."""
        return self._is_memory

    @property
    def db_path(self) -> str:
        """Return path or uri to database."""
        return self._db_path

    def initialize(self, target_version: int | None = None) -> list[int]:
        """Idempotently initialize schema and apply pending migrations up to target_version."""
        with self._lock:
            return apply_migrations(self.connection, target_version=target_version)

    def get_version(self) -> int:
        """Return current database schema version."""
        with self._lock:
            return get_current_version(self.connection)

    def rollback(
        self,
        target_version: int,
        allow_data_loss: bool = False,
        allow_integrity_downgrade: bool = False,
    ) -> list[int]:
        """Roll back migrations down to target_version with explicit data loss and integrity downgrade protection."""
        with self._lock:
            return rollback_migration(
                self.connection,
                target_version=target_version,
                allow_data_loss=allow_data_loss,
                allow_integrity_downgrade=allow_integrity_downgrade,
            )

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for an atomic database transaction.

        Commits upon clean block exit, rolls back on exception.
        """
        with self._lock:
            in_tx = self.connection.in_transaction
            if not in_tx:
                self.connection.execute("BEGIN IMMEDIATE")
            cur = self.connection.cursor()
            try:
                yield cur
                if not in_tx:
                    self.connection.execute("COMMIT")
            except Exception:
                if not in_tx:
                    try:
                        self.connection.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                cur.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> ExecutionResult:
        """Execute a single parameterized SQL statement within a transaction.

        Returns an ExecutionResult with rowcount and lastrowid metadata.
        For INSERT/REPLACE statements that successfully insert a row, lastrowid contains
        the integer rowid. For UPDATE, DELETE, SELECT, DDL, or ignored inserts,
        lastrowid is strictly None.
        For query retrieval, use fetch_one() or fetch_all().
        """
        with self.transaction() as cur:
            cur.execute(sql, params)
            lastrowid: int | None = None
            if _INSERT_OR_REPLACE_PATTERN.match(sql):
                if cur.rowcount > 0 and cur.lastrowid is not None and cur.lastrowid != 0:
                    lastrowid = cur.lastrowid
            return ExecutionResult(rowcount=cur.rowcount, lastrowid=lastrowid)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> ExecutionResult:
        """Execute a parameterized SQL statement against multiple parameter tuples within a transaction.

        Returns an ExecutionResult with total rowcount metadata.
        lastrowid is strictly None because executemany cannot reliably report a single row ID.
        For query retrieval, use fetch_one() or fetch_all().
        """
        with self.transaction() as cur:
            cur.executemany(sql, seq_of_params)
            return ExecutionResult(rowcount=cur.rowcount, lastrowid=None)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        """Execute a parameterized query and fetch a single row."""
        with self._lock:
            cur = self.connection.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchone()
            finally:
                cur.close()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Execute a parameterized query and fetch all matching rows."""
        with self._lock:
            cur = self.connection.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
