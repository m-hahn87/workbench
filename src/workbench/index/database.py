from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    repository_revision TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL,
    project TEXT,
    status TEXT NOT NULL,
    priority TEXT,
    assignee TEXT,
    tags_json TEXT NOT NULL,
    rank TEXT,
    body TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    updated_by TEXT,
    repository_revision TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    source_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (source_id, relation_type, target_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    item_id UNINDEXED,
    key,
    title,
    body,
    tags
);

CREATE TABLE IF NOT EXISTS validation_errors (
    file_path TEXT NOT NULL,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (file_path, error_code, message)
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class IndexDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.commit()

    def get_item(self, identifier: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM items WHERE id = ? OR key = ?",
                (identifier, identifier),
            ).fetchone()

    def list_items(
        self,
        *,
        project: str | None = None,
        item_type: str | None = None,
        include_archived: bool = False,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[str] = []
        if project is not None:
            clauses.append("project = ?")
            parameters.append(project)
        if item_type is not None:
            clauses.append("type = ?")
            parameters.append(item_type)
        if not include_archived:
            clauses.append("status <> 'archived'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"SELECT * FROM items{where} ORDER BY project, rank, key",  # noqa: S608
                    parameters,
                ).fetchall()
            )

    def search(self, query: str, *, limit: int = 20) -> list[sqlite3.Row]:
        if not query.strip():
            return []
        escaped = query.replace('"', '""')
        fts_query = f'"{escaped}"'
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT items.*, bm25(items_fts) AS score
                    FROM items_fts
                    JOIN items ON items.id = items_fts.item_id
                    WHERE items_fts MATCH ?
                    ORDER BY score, items.key
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            )

    def get_project(self, identifier: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM projects WHERE id = ? OR slug = ? OR key = ?",
                (identifier, identifier, identifier.upper()),
            ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute("SELECT * FROM projects ORDER BY title").fetchall()
            )

    def validation_errors(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM validation_errors ORDER BY file_path, error_code"
                ).fetchall()
            )

    def list_relations(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[str] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            parameters.append(source_id)
        if target_id is not None:
            clauses.append("target_id = ?")
            parameters.append(target_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"SELECT source_id, relation_type, target_id FROM relations{where} "
                    "ORDER BY source_id, relation_type, target_id",  # noqa: S608
                    parameters,
                ).fetchall()
            )

    def metadata(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else None

    def integrity_check(self) -> str:
        with self.connect() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return str(row[0]) if row else "unknown"
