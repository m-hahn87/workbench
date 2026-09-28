from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DuplicateRequest(RuntimeError):
    pass


def request_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def lookup(
        self,
        request_id: str | None,
        *,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if request_id is None:
            return None
        digest = request_hash(payload)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT operation, request_hash, result_json FROM requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != operation or row[1] != digest:
            raise DuplicateRequest(
                f"request_id {request_id!r} was already used for a different mutation"
            )
        result = json.loads(str(row[2]))
        return result if isinstance(result, dict) else None

    def remember(
        self,
        request_id: str | None,
        *,
        operation: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if request_id is None:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO requests (request_id, operation, request_hash, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    operation,
                    request_hash(payload),
                    json.dumps(result, ensure_ascii=False, default=str),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()
