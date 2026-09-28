from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from workbench.domain.models import CANONICAL_RELATION_TYPES, ItemModel, Project
from workbench.domain.transitions import transitions_for
from workbench.index.database import IndexDatabase
from workbench.repository.markdown_store import MarkdownFormatError, MarkdownStore


@dataclass(slots=True)
class ValidationIssue:
    file_path: str
    error_code: str
    message: str
    details: dict[str, object] | None = None


@dataclass(slots=True)
class ReindexResult:
    projects: int = 0
    items: int = 0
    errors: int = 0
    repository_revision: str = "unborn"


@dataclass(slots=True)
class _ProjectEntry:
    project: Project
    path: Path
    relative_path: str
    content_hash: str


@dataclass(slots=True)
class _ItemEntry:
    item: ItemModel
    body: str
    path: Path
    relative_path: str
    content_hash: str
    issues: list[ValidationIssue] = field(default_factory=list)


def repository_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unborn"


class Indexer:
    def __init__(
        self, root: Path, database: IndexDatabase, store: MarkdownStore | None = None
    ) -> None:
        self.root = root.resolve()
        self.database = database
        self.store = store or MarkdownStore()

    def reindex(self, *, check_lifecycle: bool = True) -> ReindexResult:
        self.database.initialize()
        revision = repository_revision(self.root)
        previous_statuses = self._previous_statuses() if check_lifecycle else {}
        projects, project_issues = self._scan_projects()
        items, item_issues = self._scan_items(projects, previous_statuses)
        issues = project_issues + item_issues

        valid_projects = [
            entry
            for entry in projects
            if not any(i.file_path == entry.relative_path for i in issues)
        ]
        valid_items = [entry for entry in items if not entry.issues]

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM items_fts")
            connection.execute("DELETE FROM relations")
            connection.execute("DELETE FROM items")
            connection.execute("DELETE FROM projects")
            connection.execute("DELETE FROM validation_errors")

            for entry in valid_projects:
                connection.execute(
                    """
                    INSERT INTO projects
                        (id, key, slug, title, status, file_path, content_hash, repository_revision)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(entry.project.id),
                        entry.project.key,
                        entry.project.slug,
                        entry.project.title,
                        entry.project.status.value,
                        entry.relative_path,
                        entry.content_hash,
                        revision,
                    ),
                )

            for entry in valid_items:
                item = entry.item
                priority = getattr(item, "priority", None)
                assignee = getattr(item, "assignee", None) or getattr(
                    item, "owner", None
                )
                tags_json = json.dumps(item.tags, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT INTO items (
                        id, key, schema_version, type, title, scope, project, status,
                        priority, assignee, tags_json, rank, body, file_path, content_hash,
                        created_at, updated_at, created_by, updated_by, repository_revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id),
                        item.key,
                        item.schema_version,
                        item.type.value,
                        item.title,
                        item.scope.value,
                        item.project,
                        item.status.value,
                        priority.value if priority else None,
                        assignee,
                        tags_json,
                        item.rank,
                        entry.body,
                        entry.relative_path,
                        entry.content_hash,
                        item.created.isoformat(),
                        item.updated.isoformat(),
                        item.created_by,
                        item.updated_by,
                        revision,
                    ),
                )
                connection.execute(
                    "INSERT INTO items_fts (item_id, key, title, body, tags) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(item.id),
                        item.key,
                        item.title,
                        entry.body,
                        " ".join(item.tags),
                    ),
                )
                for relation in item.relations:
                    connection.execute(
                        "INSERT INTO relations (source_id, relation_type, target_id) VALUES (?, ?, ?)",
                        (str(item.id), relation.type, str(relation.target)),
                    )

            detected_at = datetime.now(UTC).isoformat()
            for issue in issues:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO validation_errors
                        (file_path, error_code, message, details_json, detected_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        issue.file_path,
                        issue.error_code,
                        issue.message,
                        json.dumps(issue.details, ensure_ascii=False)
                        if issue.details
                        else None,
                        detected_at,
                    ),
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('repository_revision', ?)",
                (revision,),
            )
            connection.commit()

        return ReindexResult(
            projects=len(valid_projects),
            items=len(valid_items),
            errors=len(issues),
            repository_revision=revision,
        )

    def _previous_statuses(self) -> dict[str, tuple[str, str]]:
        if not self.database.path.exists():
            return {}
        try:
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT id, type, status FROM items"
                ).fetchall()
                lifecycle_errors = connection.execute(
                    """
                    SELECT details_json
                    FROM validation_errors
                    WHERE error_code = 'transition_not_allowed' AND details_json IS NOT NULL
                    """
                ).fetchall()
        except sqlite3.OperationalError:
            return {}
        statuses = {
            str(row["id"]): (str(row["type"]), str(row["status"])) for row in rows
        }
        for row in lifecycle_errors:
            try:
                details = json.loads(str(row["details_json"]))
                statuses.setdefault(
                    str(details["item_id"]),
                    (str(details["item_type"]), str(details["previous_status"])),
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return statuses

    def _scan_projects(self) -> tuple[list[_ProjectEntry], list[ValidationIssue]]:
        entries: list[_ProjectEntry] = []
        issues: list[ValidationIssue] = []
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        seen_slugs: set[str] = set()
        for path in sorted(self.root.glob("projects/*/_project.md")):
            relative = path.relative_to(self.root).as_posix()
            try:
                project, document = self.store.read_project(path)
                entry = _ProjectEntry(
                    project=project,
                    path=path,
                    relative_path=relative,
                    content_hash=self.store.semantic_hash(project, document.body),
                )
                entries.append(entry)
                if path.parent.name != project.slug:
                    issues.append(
                        ValidationIssue(
                            relative,
                            "project_path_mismatch",
                            "project slug does not match its directory",
                        )
                    )
                for value, seen, code in (
                    (str(project.id), seen_ids, "duplicate_project_id"),
                    (project.key, seen_keys, "duplicate_project_key"),
                    (project.slug, seen_slugs, "duplicate_project_slug"),
                ):
                    if value in seen:
                        issues.append(
                            ValidationIssue(
                                relative, code, f"duplicate project identity {value!r}"
                            )
                        )
                    seen.add(value)
            except (OSError, MarkdownFormatError, ValidationError, ValueError) as exc:
                issues.append(ValidationIssue(relative, "invalid_project", str(exc)))
        return entries, issues

    def _scan_items(
        self,
        projects: list[_ProjectEntry],
        previous_statuses: dict[str, tuple[str, str]],
    ) -> tuple[list[_ItemEntry], list[ValidationIssue]]:
        paths = list(self.root.glob("workspace/*.md"))
        paths.extend(
            path
            for path in self.root.glob("projects/*/*.md")
            if path.name != "_project.md"
        )
        project_slugs = {entry.project.slug for entry in projects}
        entries: list[_ItemEntry] = []
        issues: list[ValidationIssue] = []
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()

        for path in sorted(paths):
            relative = path.relative_to(self.root).as_posix()
            try:
                item, document = self.store.read_item(path)
                entry = _ItemEntry(
                    item=item,
                    body=document.body,
                    path=path,
                    relative_path=relative,
                    content_hash=self.store.semantic_hash(item, document.body),
                )
                entries.append(entry)
                self._validate_item_path(entry, project_slugs)
                if str(item.id) in seen_ids:
                    entry.issues.append(
                        ValidationIssue(
                            relative,
                            "duplicate_item_id",
                            f"duplicate item ID {item.id}",
                        )
                    )
                if item.key in seen_keys:
                    entry.issues.append(
                        ValidationIssue(
                            relative,
                            "duplicate_item_key",
                            f"duplicate item key {item.key}",
                        )
                    )
                seen_ids.add(str(item.id))
                seen_keys.add(item.key)
                self._validate_lifecycle(entry, previous_statuses)
            except (OSError, MarkdownFormatError, ValidationError, ValueError) as exc:
                issues.append(ValidationIssue(relative, "invalid_item", str(exc)))

        known_ids = {str(entry.item.id) for entry in entries}
        for entry in entries:
            for relation in entry.item.relations:
                if relation.type not in CANONICAL_RELATION_TYPES:
                    entry.issues.append(
                        ValidationIssue(
                            entry.relative_path,
                            "unknown_relation_type",
                            f"unknown relation type {relation.type!r}",
                        )
                    )
                if str(relation.target) not in known_ids:
                    entry.issues.append(
                        ValidationIssue(
                            entry.relative_path,
                            "relation_target_not_found",
                            f"relation target {relation.target} does not exist",
                        )
                    )

        self._validate_parent_cycles(entries)
        for entry in entries:
            issues.extend(entry.issues)
        return entries, issues

    @staticmethod
    def _validate_item_path(entry: _ItemEntry, project_slugs: set[str]) -> None:
        item = entry.item
        parts = Path(entry.relative_path).parts
        if entry.path.name != f"{item.key}.md":
            entry.issues.append(
                ValidationIssue(
                    entry.relative_path,
                    "filename_mismatch",
                    "filename must equal the readable item key",
                )
            )
        if item.scope.value == "workspace":
            if len(parts) != 2 or parts[0] != "workspace":
                entry.issues.append(
                    ValidationIssue(
                        entry.relative_path,
                        "scope_path_mismatch",
                        "workspace item is outside workspace/",
                    )
                )
        else:
            if len(parts) != 3 or parts[0] != "projects" or parts[1] != item.project:
                entry.issues.append(
                    ValidationIssue(
                        entry.relative_path,
                        "scope_path_mismatch",
                        "project item path does not match its project",
                    )
                )
            if item.project not in project_slugs:
                entry.issues.append(
                    ValidationIssue(
                        entry.relative_path,
                        "project_not_found",
                        f"project {item.project!r} does not exist",
                    )
                )

    @staticmethod
    def _validate_lifecycle(
        entry: _ItemEntry, previous_statuses: dict[str, tuple[str, str]]
    ) -> None:
        previous = previous_statuses.get(str(entry.item.id))
        if previous is None or previous == (
            entry.item.type.value,
            entry.item.status.value,
        ):
            return
        previous_type, previous_status = previous
        if previous_type != entry.item.type.value:
            return
        transitions = transitions_for(entry.item)
        current_enum = next(
            (status for status in transitions if status.value == previous_status), None
        )
        target_enum = next(
            (
                status
                for status in transitions
                if status.value == entry.item.status.value
            ),
            None,
        )
        if current_enum is None or target_enum not in transitions[current_enum]:
            entry.issues.append(
                ValidationIssue(
                    entry.relative_path,
                    "transition_not_allowed",
                    f"direct edit changed status from {previous_status!r} to {entry.item.status.value!r}",
                    {
                        "item_id": str(entry.item.id),
                        "item_type": entry.item.type.value,
                        "previous_status": previous_status,
                        "target_status": entry.item.status.value,
                    },
                )
            )

    @staticmethod
    def _validate_parent_cycles(entries: list[_ItemEntry]) -> None:
        by_id = {str(entry.item.id): entry for entry in entries}
        edges = {
            source_id: [
                str(relation.target)
                for relation in entry.item.relations
                if relation.type == "parent_of"
            ]
            for source_id, entry in by_id.items()
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, trail: list[str]) -> None:
            if node in visiting:
                cycle = trail[trail.index(node) :] + [node]
                for source in set(cycle):
                    entry = by_id.get(source)
                    if entry:
                        entry.issues.append(
                            ValidationIssue(
                                entry.relative_path,
                                "parent_cycle",
                                "parent_of relations must be acyclic",
                                {"cycle": cycle},
                            )
                        )
                return
            if node in visited:
                return
            visiting.add(node)
            for target in edges.get(node, []):
                visit(target, trail + [target])
            visiting.remove(node)
            visited.add(node)

        for item_id in edges:
            visit(item_id, [item_id])
