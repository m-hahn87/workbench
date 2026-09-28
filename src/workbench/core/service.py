from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from workbench.config import CommitMode, GitConfig, load_config
from workbench.core.context_builder import (
    build_project_context,
    render_project_context_markdown,
)
from workbench.domain.models import (
    BugItem,
    CANONICAL_RELATION_TYPES,
    DecisionItem,
    FeatureItem,
    INVERSE_RELATIONS,
    IdeaItem,
    ItemModel,
    ItemType,
    Project,
    Relation,
    Scope,
    TaskItem,
    new_uuid7,
    parse_item,
    slugify,
    utc_now,
)
from workbench.domain.transitions import ensure_transition
from workbench.index.database import IndexDatabase
from workbench.index.indexer import Indexer, ReindexResult, repository_revision
from workbench.repository.git_transactions import (
    CommitOutcome,
    GitChange,
    GitTransactionManager,
)
from workbench.repository.locking import RepositoryLock
from workbench.repository.markdown_store import (
    MarkdownDocument,
    MarkdownStore,
    frontmatter_data,
)
from workbench.state.idempotency import IdempotencyStore


class WorkbenchError(RuntimeError):
    pass


class NotInitialized(WorkbenchError):
    pass


class ItemNotFound(WorkbenchError):
    pass


class ProjectNotFound(WorkbenchError):
    pass


class RevisionConflict(WorkbenchError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"item revision conflict: expected {expected}, found {actual}")
        self.expected = expected
        self.actual = actual


@dataclass(slots=True)
class ProjectResult:
    data: dict[str, Any]
    repository_revision: str
    commit_pending: bool = False
    warnings: list[str] = field(default_factory=list)
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "repository_revision": self.repository_revision,
            "commit_pending": self.commit_pending,
            "warnings": self.warnings,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectResult:
        return cls(
            data=dict(value["data"]),
            repository_revision=str(value["repository_revision"]),
            commit_pending=bool(value.get("commit_pending", False)),
            warnings=list(value.get("warnings", [])),
            request_id=value.get("request_id"),
        )


@dataclass(slots=True)
class ItemResult:
    data: dict[str, Any]
    body: str
    item_revision: str
    file_path: str
    repository_revision: str
    commit_pending: bool = False
    warnings: list[str] = field(default_factory=list)
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "body": self.body,
            "item_revision": self.item_revision,
            "file_path": self.file_path,
            "repository_revision": self.repository_revision,
            "commit_pending": self.commit_pending,
            "warnings": self.warnings,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ItemResult:
        return cls(
            data=dict(value["data"]),
            body=str(value["body"]),
            item_revision=str(value["item_revision"]),
            file_path=str(value["file_path"]),
            repository_revision=str(value["repository_revision"]),
            commit_pending=bool(value.get("commit_pending", False)),
            warnings=list(value.get("warnings", [])),
            request_id=value.get("request_id"),
        )


class Workbench:
    def __init__(self, root: Path | str, *, reindex: bool = True) -> None:
        self.root = Path(root).resolve()
        if not (self.root / "_workspace.md").exists():
            raise NotInitialized(
                f"{self.root} is not an initialized Workbench repository"
            )
        self.config = load_config(self.root)
        self.store = MarkdownStore()
        state_directory = self.root / ".workbench" / "state"
        self.database = IndexDatabase(state_directory / "workbench.db")
        self.idempotency = IdempotencyStore(state_directory / "idempotency.db")
        self.indexer = Indexer(self.root, self.database, self.store)
        self.lock_path = state_directory / "write.lock"
        self.git = GitTransactionManager(
            self.root,
            self.lock_path,
            self.config.git,
            on_commit=lambda: self.indexer.reindex(check_lifecycle=False),
        )
        self.watcher_running = False
        self.watcher_events = 0
        self.watcher_error: str | None = None
        self.database.initialize()
        if reindex:
            self.indexer.reindex()

    @classmethod
    def initialize(
        cls,
        root: Path | str,
        *,
        title: str = "Workbench",
        key: str = "WB",
        commit_mode: str = "debounce",
        debounce_seconds: float = 30,
    ) -> Workbench:
        path = Path(root).resolve()
        path.mkdir(parents=True, exist_ok=True)
        if (path / "_workspace.md").exists():
            raise WorkbenchError(f"{path} is already initialized")
        key = key.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", key):
            raise WorkbenchError(
                "workspace key must contain 2-8 uppercase letters or digits"
            )
        try:
            parsed_commit_mode = CommitMode(commit_mode)
        except ValueError as exc:
            raise WorkbenchError(
                "commit_mode must be immediate, debounce, or manual"
            ) from exc
        git_config = GitConfig(
            commit_mode=parsed_commit_mode,
            debounce_seconds=debounce_seconds,
        )

        for directory in ("projects", "workspace", "templates", ".workbench/state"):
            (path / directory).mkdir(parents=True, exist_ok=True)
        now = utc_now().isoformat().replace("+00:00", "Z")
        workspace = MarkdownDocument(
            frontmatter=CommentedMap(
                (
                    ("id", str(new_uuid7())),
                    ("key", key),
                    ("title", title),
                    ("created", now),
                    ("updated", now),
                )
            ),
            body=f"\n# {title}\n",
        )
        store = MarkdownStore()
        store.write(path / "_workspace.md", workspace)
        (path / ".workbench" / "config.yml").write_text(
            "git:\n"
            f"  commit_mode: {git_config.commit_mode.value}\n"
            f"  debounce_seconds: {git_config.debounce_seconds}\n"
            "  author_name: Workbench\n"
            "  author_email: workbench@localhost\n",
            encoding="utf-8",
            newline="\n",
        )
        (path / ".workbench" / "schema-version").write_text(
            "1\n", encoding="utf-8", newline="\n"
        )
        (path / ".gitignore").write_text(
            ".workbench/state/\n", encoding="utf-8", newline="\n"
        )
        if not (path / ".git").exists():
            subprocess.run(
                ["git", "init", str(path)], check=True, capture_output=True, text=True
            )

        workbench = cls(path)
        workbench.git.commit_now(
            GitChange(
                paths=(
                    "_workspace.md",
                    ".workbench/config.yml",
                    ".workbench/schema-version",
                    ".gitignore",
                ),
                subject="chore: initialize workbench",
                actor="workbench",
                reason="Initialize the canonical Markdown repository",
            )
        )
        return workbench

    def create_project(
        self,
        title: str,
        *,
        key: str,
        slug: str | None = None,
        actor: str = "human",
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ProjectResult:
        payload = {
            "title": title,
            "key": key,
            "slug": slug,
            "actor": actor,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id,
            operation="create_project",
            payload=payload,
        )
        if cached is not None:
            return ProjectResult.from_dict(cached)

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id,
                operation="create_project",
                payload=payload,
            )
            if cached is not None:
                return ProjectResult.from_dict(cached)
            self.indexer.reindex()
            resolved_slug = slug or slugify(title)
            project = Project(key=key, slug=resolved_slug, title=title)
            if self.database.get_project(project.slug) or self.database.get_project(
                project.key
            ):
                raise WorkbenchError(
                    f"project slug or key already exists: {project.slug} / {project.key}"
                )
            project_path = self.root / "projects" / project.slug / "_project.md"
            if project_path.exists():
                raise WorkbenchError(
                    f"refusing to overwrite existing project file: {project_path}"
                )
            self.store.write(
                project_path, self.store.from_model(project, f"\n# {project.title}\n")
            )
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(project_path.relative_to(self.root).as_posix(),),
                    subject=f"feat({project.key}): create project",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = ProjectResult(
                data=project.model_dump(mode="json"),
                repository_revision=outcome.repository_revision,
                commit_pending=outcome.commit_pending,
                request_id=request_id,
            )
            self.idempotency.remember(
                request_id,
                operation="create_project",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def list_projects(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.list_projects()]

    def get_workspace(self) -> dict[str, Any]:
        document = self.store.read(self.root / "_workspace.md")
        return {
            **frontmatter_data(document),
            "body": document.body,
            "repository_revision": repository_revision(self.root),
        }

    def update_project(
        self,
        identifier: str,
        *,
        actor: str,
        title: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ProjectResult:
        payload = {
            "identifier": identifier,
            "actor": actor,
            "title": title,
            "status": status,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="update_project", payload=payload
        )
        if cached is not None:
            return ProjectResult.from_dict(cached)
        if title is None and status is None:
            raise WorkbenchError("update_project requires title or status")

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="update_project", payload=payload
            )
            if cached is not None:
                return ProjectResult.from_dict(cached)
            self.indexer.reindex()
            row = self.database.get_project(identifier)
            if row is None:
                raise ProjectNotFound(identifier)
            path = self.root / str(row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            project, document = self.store.read_project(path)
            if title is not None:
                document.frontmatter["title"] = title
            if status is not None:
                document.frontmatter["status"] = status
            document.frontmatter["updated"] = utc_now().date().isoformat()
            updated = Project.model_validate(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex(check_lifecycle=False)
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"chore({project.key}): update project",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = ProjectResult(
                data=updated.model_dump(mode="json"),
                repository_revision=outcome.repository_revision,
                commit_pending=outcome.commit_pending,
                request_id=request_id,
            )
            self.idempotency.remember(
                request_id,
                operation="update_project",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def get_project(self, identifier: str) -> dict[str, Any]:
        row = self.database.get_project(identifier)
        if row is None:
            raise ProjectNotFound(identifier)
        return dict(row)

    def get_project_context(
        self,
        identifier: str,
        *,
        max_items: int = 50,
        format: str = "json",
        include: list[str] | None = None,
        since: str | None = None,
    ) -> dict[str, Any] | str:
        if max_items < 1 or max_items > 500:
            raise WorkbenchError("max_items must be between 1 and 500")
        project = self.get_project(identifier)
        items = self.list_items(project=str(project["slug"]), include_archived=False)
        relations: list[dict[str, Any]] = []
        for item in items:
            for relation in self.get_related(str(item["id"])):
                relations.append(
                    {
                        "item": str(item["key"]),
                        "relation_type": relation["relation_type"],
                        "direction": relation["direction"],
                        "related_item": relation["item"],
                        "cross_project": relation["cross_project"],
                    }
                )
        activity = self.get_activity(
            project=str(project["slug"]),
            limit=min(max_items, 100),
        )
        if since is not None:
            activity = [entry for entry in activity if str(entry["timestamp"]) >= since]
        context = build_project_context(
            project,
            items,
            repository_revision=repository_revision(self.root),
            max_items=max_items,
            relations=relations,
            recent_activity=activity,
        )
        if include is not None:
            allowed = {
                "active_work",
                "blockers",
                "backlog",
                "recently_completed",
                "decisions",
                "relations",
                "recent_activity",
                "risks",
                "suggested_next_tasks",
            }
            unknown = sorted(set(include) - allowed)
            if unknown:
                raise WorkbenchError(f"unknown context sections: {', '.join(unknown)}")
            context = {
                key: value
                for key, value in context.items()
                if key in {"project", "summary", "repository_revision"}
                or key in include
            }
        if format == "json":
            return context
        if format == "markdown":
            return render_project_context_markdown(context)
        raise WorkbenchError("context format must be 'json' or 'markdown'")

    def create_item(
        self,
        *,
        item_type: str,
        title: str,
        actor: str,
        project: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        _body_override: str | None = None,
        _operation: str = "create_item",
        _subject: str | None = None,
    ) -> ItemResult:
        payload = {
            "item_type": item_type,
            "title": title,
            "actor": actor,
            "project": project,
            "description": description,
            "tags": tags or [],
            "fields": fields or {},
            "reason": reason,
            "body_override": _body_override,
        }
        cached = self.idempotency.lookup(
            request_id, operation=_operation, payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        try:
            parsed_type = ItemType(item_type)
        except ValueError as exc:
            raise WorkbenchError(
                "item type must be task, bug, feature, idea, or decision"
            ) from exc

        allowed_fields = {
            ItemType.TASK: {"priority", "assignee", "estimate", "due"},
            ItemType.BUG: {"severity", "priority", "assignee", "affected_version"},
            ItemType.FEATURE: {"priority", "owner", "target_release"},
            ItemType.IDEA: {"score"},
            ItemType.DECISION: {"supersedes", "owners"},
        }[parsed_type]
        supplied_fields = fields or {}
        unsupported = sorted(set(supplied_fields) - allowed_fields)
        if unsupported:
            raise WorkbenchError(
                f"unsupported fields for {parsed_type.value}: {', '.join(unsupported)}"
            )

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation=_operation, payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            if project:
                project_row = self.database.get_project(project)
                if project_row is None:
                    raise ProjectNotFound(project)
                scope = Scope.PROJECT
                project_slug = str(project_row["slug"])
                prefix = str(project_row["key"])
                directory = self.root / "projects" / project_slug
            else:
                scope = Scope.WORKSPACE
                project_slug = None
                prefix = self._workspace_key()
                directory = self.root / "workspace"

            key = self._next_item_key(prefix, parsed_type)
            common = {
                "key": key,
                "title": title,
                "scope": scope,
                "project": project_slug,
                "created_by": actor,
                "updated_by": actor,
                "tags": tags or [],
            }
            if parsed_type == ItemType.TASK:
                item: ItemModel = TaskItem(**common, **supplied_fields)
                body = (
                    f"\n## Beschreibung\n\n{description or title}\n\n"
                    "## Akzeptanzkriterien\n\n- [ ] Noch festzulegen\n"
                )
            elif parsed_type == ItemType.BUG:
                item = BugItem(**common, **supplied_fields)
                body = (
                    f"\n## Beschreibung\n\n{description or title}\n\n"
                    "## Schritte zur Reproduktion\n\n1. Noch festzulegen\n\n"
                    "## Erwartetes Verhalten\n\nNoch festzulegen.\n\n"
                    "## Tatsächliches Verhalten\n\nNoch festzulegen.\n\n"
                    "## Umgebung\n\nNoch festzulegen.\n"
                )
            elif parsed_type == ItemType.FEATURE:
                feature_fields = {"owner": actor, **supplied_fields}
                item = FeatureItem(**common, **feature_fields)
                body = (
                    f"\n## Beschreibung\n\n{description or title}\n\n"
                    "## Akzeptanzkriterien\n\n- [ ] Noch festzulegen\n"
                )
            elif parsed_type == ItemType.IDEA:
                item = IdeaItem(**common, **supplied_fields)
                body = f"\n## Beschreibung\n\n{description or title}\n"
            else:
                decision_fields = {"owners": [actor], **supplied_fields}
                item = DecisionItem(**common, **decision_fields)
                body = (
                    f"\n## Kontext\n\n{description or title}\n\n"
                    "## Entscheidung\n\nNoch festzulegen.\n\n"
                    "## Alternativen\n\nNoch festzulegen.\n\n"
                    "## Konsequenzen\n\nNoch festzulegen.\n"
                )
            if _body_override is not None:
                body = _body_override
            item_path = directory / f"{key}.md"
            if item_path.exists():
                raise WorkbenchError(
                    f"refusing to overwrite existing item file: {item_path}"
                )
            self.store.write(item_path, self.store.from_model(item, body))
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(item_path.relative_to(self.root).as_posix(),),
                    subject=_subject or f"feat({key}): create {parsed_type.value}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(key)
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation=_operation,
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def capture_idea(
        self,
        title: str,
        *,
        actor: str,
        description: str | None = None,
        score: int = 0,
        tags: list[str] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        """Capture a workspace-scoped idea without requiring a project."""
        return self.create_item(
            item_type="idea",
            title=title,
            actor=actor,
            description=description,
            tags=tags,
            fields={"score": score},
            reason=reason,
            request_id=request_id,
            _operation="capture_idea",
            _subject="feat(ideas): capture idea",
        )

    def record_decision(
        self,
        title: str,
        *,
        project: str,
        actor: str,
        context: str,
        decision: str,
        alternatives: str | None = None,
        consequences: str | None = None,
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        """Record a complete, structured decision in one item transaction."""
        if not context.strip() or not decision.strip():
            raise WorkbenchError("context and decision must not be empty")
        body = (
            f"\n## Kontext\n\n{context.strip()}\n\n"
            f"## Entscheidung\n\n{decision.strip()}\n\n"
            f"## Alternativen\n\n{(alternatives or 'Keine dokumentiert.').strip()}\n\n"
            f"## Konsequenzen\n\n{(consequences or 'Noch zu beobachten.').strip()}\n"
        )
        return self.create_item(
            item_type="decision",
            title=title,
            project=project,
            actor=actor,
            description=context,
            tags=tags,
            fields=fields,
            reason=reason,
            request_id=request_id,
            _body_override=body,
            _operation="record_decision",
            _subject="docs(decisions): record decision",
        )

    def plan_feature(
        self,
        title: str,
        *,
        project: str,
        actor: str,
        description: str | None = None,
        task_titles: list[str] | None = None,
        decision_title: str | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a feature, its tasks and optional decision in one Git transaction."""
        tasks = [task.strip() for task in (task_titles or []) if task.strip()]
        payload = {
            "title": title,
            "project": project,
            "actor": actor,
            "description": description,
            "task_titles": tasks,
            "decision_title": decision_title,
            "fields": fields or {},
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="plan_feature", payload=payload
        )
        if cached is not None:
            return cached

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="plan_feature", payload=payload
            )
            if cached is not None:
                return cached
            self.indexer.reindex()
            project_row = self.database.get_project(project)
            if project_row is None:
                raise ProjectNotFound(project)
            project_slug = str(project_row["slug"])
            prefix = str(project_row["key"])
            directory = self.root / "projects" / project_slug

            feature_key = self._next_item_key(prefix, ItemType.FEATURE)
            task_first_key = self._next_item_key(prefix, ItemType.TASK)
            task_first_number = int(task_first_key.rsplit("-", 1)[1])
            task_keys = [
                f"{prefix}-{ItemType.TASK.key_code}-{task_first_number + index:03d}"
                for index in range(len(tasks))
            ]
            decision_key = (
                self._next_item_key(prefix, ItemType.DECISION)
                if decision_title
                else None
            )

            task_items = [
                TaskItem(
                    key=key,
                    title=task_title,
                    scope=Scope.PROJECT,
                    project=project_slug,
                    created_by=actor,
                    updated_by=actor,
                )
                for key, task_title in zip(task_keys, tasks, strict=True)
            ]
            decision_item = (
                DecisionItem(
                    key=str(decision_key),
                    title=decision_title,
                    scope=Scope.PROJECT,
                    project=project_slug,
                    created_by=actor,
                    updated_by=actor,
                    owners=[actor],
                )
                if decision_title and decision_key
                else None
            )
            relations = [
                Relation(type="parent_of", target=task.id) for task in task_items
            ]
            if decision_item is not None:
                relations.append(Relation(type="decided_by", target=decision_item.id))
            feature_item = FeatureItem(
                key=feature_key,
                title=title,
                scope=Scope.PROJECT,
                project=project_slug,
                created_by=actor,
                updated_by=actor,
                relations=relations,
                **{"owner": actor, **(fields or {})},
            )

            documents: list[tuple[ItemModel, Path, str]] = [
                (
                    feature_item,
                    directory / f"{feature_key}.md",
                    f"\n## Beschreibung\n\n{description or title}\n\n"
                    "## Akzeptanzkriterien\n\n- [ ] Noch festzulegen\n",
                )
            ]
            documents.extend(
                (
                    task,
                    directory / f"{task.key}.md",
                    f"\n## Beschreibung\n\n{task.title}\n\n"
                    "## Akzeptanzkriterien\n\n- [ ] Noch festzulegen\n",
                )
                for task in task_items
            )
            if decision_item is not None:
                documents.append(
                    (
                        decision_item,
                        directory / f"{decision_item.key}.md",
                        f"\n## Kontext\n\n{description or title}\n\n"
                        "## Entscheidung\n\nNoch festzulegen.\n\n"
                        "## Alternativen\n\nNoch festzulegen.\n\n"
                        "## Konsequenzen\n\nNoch festzulegen.\n",
                    )
                )

            relative_paths = tuple(
                path.relative_to(self.root).as_posix() for _, path, _ in documents
            )
            self.git.assert_paths_available(relative_paths)
            if any(path.exists() for _, path, _ in documents):
                raise WorkbenchError("refusing to overwrite an existing plan file")
            for item, path, body in documents:
                self.store.write(path, self.store.from_model(item, body))
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=relative_paths,
                    subject=f"feat({feature_key}): plan feature",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            results = {item.key: self.get_item(item.key) for item, _, _ in documents}
            for item_result in results.values():
                self._apply_outcome(item_result, outcome, request_id)
            result = {
                "data": {
                    "feature": results[feature_key].as_dict(),
                    "tasks": [results[key].as_dict() for key in task_keys],
                    "decision": (
                        results[str(decision_key)].as_dict() if decision_key else None
                    ),
                },
                "repository_revision": outcome.repository_revision,
                "commit_pending": outcome.commit_pending,
                "warnings": [],
                "request_id": request_id,
            }
            self.idempotency.remember(
                request_id,
                operation="plan_feature",
                payload=payload,
                result=result,
            )
            return result

    def get_item(self, identifier: str) -> ItemResult:
        row = self.database.get_item(identifier)
        if row is None:
            raise ItemNotFound(identifier)
        path = self.root / str(row["file_path"])
        item, document = self.store.read_item(path)
        return ItemResult(
            data=item.model_dump(mode="json", exclude_none=True),
            body=document.body,
            item_revision=self.store.semantic_hash(item, document.body),
            file_path=str(row["file_path"]),
            repository_revision=str(row["repository_revision"]),
        )

    def list_items(
        self,
        *,
        project: str | None = None,
        item_type: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        project_slug = None
        if project:
            row = self.database.get_project(project)
            if row is None:
                raise ProjectNotFound(project)
            project_slug = str(row["slug"])
        return [
            dict(row)
            for row in self.database.list_items(
                project=project_slug,
                item_type=item_type,
                include_archived=include_archived,
            )
        ]

    def get_backlog(self, project: str) -> list[dict[str, Any]]:
        backlog_statuses = {"backlog", "reported", "proposed", "inbox"}
        return [
            item
            for item in self.list_items(project=project, include_archived=False)
            if item["status"] in backlog_statuses
        ]

    def update_item(
        self,
        identifier: str,
        *,
        actor: str,
        changes: dict[str, Any] | None = None,
        body: str | None = None,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        _operation: str = "update_item",
        _subject: str | None = None,
        _payload_override: dict[str, Any] | None = None,
    ) -> ItemResult:
        changes = changes or {}
        payload = _payload_override or {
            "identifier": identifier,
            "actor": actor,
            "changes": changes,
            "body": body,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation=_operation, payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        if not changes and body is None:
            raise WorkbenchError("update_item requires fields or body")

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation=_operation, payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            row = self.database.get_item(identifier)
            if row is None:
                raise ItemNotFound(identifier)
            path = self.root / str(row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            editable = self._editable_fields(item)
            unsupported = sorted(set(changes) - editable)
            if unsupported:
                raise WorkbenchError(
                    "fields require a dedicated command or are not valid for this type: "
                    + ", ".join(unsupported)
                )
            for name, value in changes.items():
                document.frontmatter[name] = value
            if body is not None:
                document.body = body
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            updated = parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=_subject or f"chore({item.key}): update item",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(updated.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation=_operation,
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def transition_item(
        self,
        identifier: str,
        target: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        _operation: str = "transition_item",
        _payload_override: dict[str, Any] | None = None,
    ) -> ItemResult:
        payload = _payload_override or {
            "identifier": identifier,
            "target": target,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation=_operation, payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation=_operation, payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            row = self.database.get_item(identifier)
            if row is None:
                raise ItemNotFound(identifier)
            path = self.root / str(row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            target_status = ensure_transition(item, target)
            if target_status.value == "archived":
                document.frontmatter["archived_from"] = item.status.value
            document.frontmatter["status"] = target_status.value
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"chore({item.key}): move item to {target_status.value}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation=_operation,
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def archive_item(
        self,
        identifier: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        return self.transition_item(
            identifier,
            "archived",
            actor=actor,
            expected_revision=expected_revision,
            reason=reason,
            request_id=request_id,
            _operation="archive_item",
        )

    def restore_item(
        self,
        identifier: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {
            "identifier": identifier,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="restore_item", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="restore_item", payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            row = self.database.get_item(identifier)
            if row is None:
                raise ItemNotFound(identifier)
            path = self.root / str(row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            if item.status.value != "archived" or not item.archived_from:
                raise WorkbenchError(
                    "only an archived item with archived_from can be restored"
                )
            restored_status = item.archived_from
            document.frontmatter["status"] = restored_status
            if "archived_from" in document.frontmatter:
                del document.frontmatter["archived_from"]
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex(check_lifecycle=False)
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"chore({item.key}): restore item to {restored_status}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation="restore_item",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def assign_item(
        self,
        identifier: str,
        assignee: str | None,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        current = self.get_item(identifier)
        item_type = ItemType(current.data["type"])
        field = {
            ItemType.TASK: "assignee",
            ItemType.BUG: "assignee",
            ItemType.FEATURE: "owner",
        }.get(item_type)
        if field is None:
            raise WorkbenchError(f"{item_type.value} items cannot be assigned")
        return self.update_item(
            identifier,
            actor=actor,
            changes={field: assignee},
            expected_revision=expected_revision,
            reason=reason,
            request_id=request_id,
            _operation="assign_item",
            _subject=f"chore({current.data['key']}): update assignment",
        )

    def add_comment(
        self,
        identifier: str,
        text: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        if not text.strip():
            raise WorkbenchError("comment text must not be empty")
        payload = {
            "identifier": identifier,
            "text": text,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="add_comment", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        current = self.get_item(identifier)
        timestamp = utc_now().isoformat().replace("+00:00", "Z")
        body = current.body.rstrip()
        if "\n## Kommentare\n" not in f"\n{body}\n":
            body += "\n\n## Kommentare"
        body += f"\n\n### {actor} — {timestamp}\n\n{text.strip()}\n"
        return self.update_item(
            identifier,
            actor=actor,
            body=body,
            expected_revision=expected_revision or current.item_revision,
            reason=reason,
            request_id=request_id,
            _operation="add_comment",
            _subject=f"docs({current.data['key']}): add comment",
            _payload_override=payload,
        )

    def link_items(
        self,
        source: str,
        target: str,
        relation_type: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="link_items", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        if relation_type not in CANONICAL_RELATION_TYPES:
            raise WorkbenchError(
                "relation_type must be canonical: "
                + ", ".join(sorted(CANONICAL_RELATION_TYPES))
            )

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="link_items", payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            source_row = self.database.get_item(source)
            target_row = self.database.get_item(target)
            if source_row is None:
                raise ItemNotFound(source)
            if target_row is None:
                raise ItemNotFound(target)
            if source_row["id"] == target_row["id"]:
                raise WorkbenchError("an item cannot relate to itself")
            if relation_type == "parent_of" and self._would_create_parent_cycle(
                str(source_row["id"]), str(target_row["id"])
            ):
                raise WorkbenchError("parent_of relation would create a cycle")
            path = self.root / str(source_row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            relation = Relation(type=relation_type, target=str(target_row["id"]))
            if any(
                existing.type == relation.type and existing.target == relation.target
                for existing in item.relations
            ):
                result = self.get_item(str(item.id))
                result.request_id = request_id
                self.idempotency.remember(
                    request_id,
                    operation="link_items",
                    payload=payload,
                    result=result.as_dict(),
                )
                return result
            relations = document.frontmatter.get("relations")
            if not isinstance(relations, list):
                relations = CommentedSeq()
                document.frontmatter["relations"] = relations
            relations.append(
                CommentedMap(
                    (("type", relation_type), ("target", str(relation.target)))
                )
            )
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"chore({item.key}): link {relation_type}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation="link_items",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def unlink_items(
        self,
        source: str,
        target: str,
        relation_type: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="unlink_items", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        if relation_type not in CANONICAL_RELATION_TYPES:
            raise WorkbenchError(f"unknown canonical relation type {relation_type!r}")

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="unlink_items", payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            source_row = self.database.get_item(source)
            target_row = self.database.get_item(target)
            if source_row is None:
                raise ItemNotFound(source)
            if target_row is None:
                raise ItemNotFound(target)
            path = self.root / str(source_row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            target_id = str(target_row["id"])
            kept = [
                existing
                for existing in item.relations
                if not (
                    existing.type == relation_type and str(existing.target) == target_id
                )
            ]
            if len(kept) == len(item.relations):
                raise WorkbenchError("relation does not exist")
            document.frontmatter["relations"] = CommentedSeq(
                CommentedMap(
                    (("type", relation.type), ("target", str(relation.target)))
                )
                for relation in kept
            )
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"chore({item.key}): unlink {relation_type}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation="unlink_items",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def get_related(self, identifier: str) -> list[dict[str, Any]]:
        row = self.database.get_item(identifier)
        if row is None:
            raise ItemNotFound(identifier)
        item_id = str(row["id"])
        project = row["project"]
        related: list[dict[str, Any]] = []
        for relation in self.database.list_relations(source_id=item_id):
            target = self.database.get_item(str(relation["target_id"]))
            if target is not None:
                related.append(
                    self._relation_view(
                        target,
                        relation_type=str(relation["relation_type"]),
                        stored_type=str(relation["relation_type"]),
                        direction="outgoing",
                        source_project=project,
                    )
                )
        for relation in self.database.list_relations(target_id=item_id):
            source = self.database.get_item(str(relation["source_id"]))
            if source is not None:
                stored_type = str(relation["relation_type"])
                related.append(
                    self._relation_view(
                        source,
                        relation_type=INVERSE_RELATIONS[stored_type],
                        stored_type=stored_type,
                        direction="incoming",
                        source_project=project,
                    )
                )
        return sorted(
            related, key=lambda entry: (entry["relation_type"], entry["item"]["key"])
        )

    def change_item_type(
        self,
        identifier: str,
        target_type: str,
        *,
        actor: str,
        fields: dict[str, Any] | None = None,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {
            "identifier": identifier,
            "target_type": target_type,
            "actor": actor,
            "fields": fields or {},
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="change_item_type", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        try:
            parsed_type = ItemType(target_type)
        except ValueError as exc:
            raise WorkbenchError(
                "target type must be task, bug, feature, idea, or decision"
            ) from exc

        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="change_item_type", payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            row = self.database.get_item(identifier)
            if row is None:
                raise ItemNotFound(identifier)
            old_path = self.root / str(row["file_path"])
            item, document = self.store.read_item(old_path)
            if item.type == parsed_type:
                raise WorkbenchError("item already has the requested type")
            if item.status.value == "archived":
                raise WorkbenchError(
                    "restore an archived item before changing its type"
                )
            actual_revision = self.store.semantic_hash(item, document.body)
            self._check_revision(expected_revision, actual_revision)
            new_key = self._next_item_key(item.key.split("-", 1)[0], parsed_type)
            new_path = old_path.with_name(f"{new_key}.md")
            old_relative = old_path.relative_to(self.root).as_posix()
            new_relative = new_path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((old_relative, new_relative))
            if new_path.exists():
                raise WorkbenchError(
                    f"refusing to overwrite existing item file: {new_path}"
                )
            common = {
                "schema_version": item.schema_version,
                "id": item.id,
                "key": new_key,
                "title": item.title,
                "scope": item.scope,
                "project": item.project,
                "created": item.created,
                "updated": utc_now(),
                "created_by": item.created_by,
                "updated_by": actor,
                "tags": item.tags,
                "rank": item.rank,
                "relations": item.relations,
            }
            changed = self._new_item_for_type(
                parsed_type, common, fields or {}, actor=actor
            )
            self.store.write(new_path, self.store.from_model(changed, document.body))
            old_path.unlink()
            self.indexer.reindex(check_lifecycle=False)
            outcome = self.git.record_change(
                GitChange(
                    paths=(old_relative, new_relative),
                    subject=f"refactor({new_key}): change item type to {parsed_type.value}",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation="change_item_type",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.search(query, limit=limit)]

    def get_activity(
        self,
        *,
        project: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise WorkbenchError("activity limit must be between 1 and 500")
        pathspec: list[str] = []
        if project is not None:
            project_row = self.database.get_project(project)
            if project_row is None:
                raise ProjectNotFound(project)
            pathspec = [f"projects/{project_row['slug']}"]
        marker = "%x1e%H%x1f%aI%x1f%an%x1f%s%x1f%b%x1f"
        command = [
            "git",
            "-C",
            str(self.root),
            "log",
            f"-n{limit}",
            f"--format={marker}",
            "--name-only",
        ]
        if pathspec:
            command.extend(["--", *pathspec])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise WorkbenchError(result.stderr.strip() or "could not read Git activity")
        activity: list[dict[str, Any]] = []
        for record in result.stdout.split("\x1e"):
            record = record.strip("\r\n")
            if not record:
                continue
            fields = record.split("\x1f", 5)
            if len(fields) != 6:
                continue
            commit, timestamp, author, subject, body, paths_text = fields
            trailers: dict[str, str] = {}
            for line in body.splitlines():
                name, separator, value = line.partition(":")
                if separator and name in {"Actor", "Request-Id", "Reason"}:
                    trailers[name] = value.strip()
            activity.append(
                {
                    "commit": commit,
                    "timestamp": timestamp,
                    "author": author,
                    "actor": trailers.get("Actor"),
                    "request_id": trailers.get("Request-Id"),
                    "reason": trailers.get("Reason"),
                    "subject": subject,
                    "paths": [
                        line.strip() for line in paths_text.splitlines() if line.strip()
                    ],
                }
            )
        return activity

    def prepare_project_handoff(
        self,
        identifier: str,
        *,
        from_actor: str,
        to_actor: str,
        assumptions: list[str] | None = None,
        recommended_next_step: str | None = None,
        max_items: int = 50,
    ) -> dict[str, Any]:
        if not from_actor or not to_actor:
            raise WorkbenchError("from_actor and to_actor are required")
        context = self.get_project_context(
            identifier, max_items=max_items, format="json"
        )
        assert isinstance(context, dict)
        status = subprocess.run(
            ["git", "-C", str(self.root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise WorkbenchError(
                status.stderr.strip() or "could not inspect open changes"
            )
        relevant = context.get("blockers", []) + context.get("active_work", [])
        return {
            "project": context["project"],
            "from_actor": from_actor,
            "to_actor": to_actor,
            "current_state": context,
            "open_changes": [
                line for line in status.stdout.splitlines() if line.strip()
            ],
            "assumptions": assumptions or [],
            "recommended_next_step": recommended_next_step
            or self._default_next_step(context),
            "relevant_item_ids": [
                {"id": item["id"], "key": item["key"]} for item in relevant[:max_items]
            ],
            "repository_revision": context["repository_revision"],
        }

    def claim_next_task(
        self,
        project: str,
        *,
        actor: str,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {"project": project, "actor": actor, "reason": reason}
        cached = self.idempotency.lookup(
            request_id, operation="claim_next_task", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        with RepositoryLock(self.lock_path):
            cached = self.idempotency.lookup(
                request_id, operation="claim_next_task", payload=payload
            )
            if cached is not None:
                return ItemResult.from_dict(cached)
            self.indexer.reindex()
            project_row = self.database.get_project(project)
            if project_row is None:
                raise ProjectNotFound(project)
            candidates = [
                row
                for row in self.database.list_items(
                    project=str(project_row["slug"]), item_type="task"
                )
                if row["status"] == "ready" and not row["assignee"]
            ]
            if not candidates:
                raise WorkbenchError("no unassigned ready task is available")
            row = candidates[0]
            path = self.root / str(row["file_path"])
            relative_path = path.relative_to(self.root).as_posix()
            self.git.assert_paths_available((relative_path,))
            item, document = self.store.read_item(path)
            if not isinstance(item, TaskItem):
                raise WorkbenchError("claim candidate is not a task")
            target = ensure_transition(item, "in-progress")
            document.frontmatter["status"] = target.value
            document.frontmatter["assignee"] = actor
            document.frontmatter["updated"] = (
                utc_now().isoformat().replace("+00:00", "Z")
            )
            document.frontmatter["updated_by"] = actor
            parse_item(frontmatter_data(document))
            self.store.write(path, document)
            self.indexer.reindex()
            outcome = self.git.record_change(
                GitChange(
                    paths=(relative_path,),
                    subject=f"feat({item.key}): claim next task",
                    actor=actor,
                    reason=reason,
                    request_id=request_id,
                ),
                lock_held=True,
            )
            result = self.get_item(str(item.id))
            self._apply_outcome(result, outcome, request_id)
            self.idempotency.remember(
                request_id,
                operation="claim_next_task",
                payload=payload,
                result=result.as_dict(),
            )
            return result

    def complete_task(
        self,
        identifier: str,
        *,
        actor: str,
        expected_revision: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ItemResult:
        payload = {
            "identifier": identifier,
            "actor": actor,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        cached = self.idempotency.lookup(
            request_id, operation="complete_task", payload=payload
        )
        if cached is not None:
            return ItemResult.from_dict(cached)
        current = self.get_item(identifier)
        if current.data["type"] != "task":
            raise WorkbenchError("complete_task only accepts task items")
        if current.data["status"] == "done":
            return current
        target = "done" if current.data["status"] == "review" else "review"
        return self.transition_item(
            identifier,
            target,
            actor=actor,
            expected_revision=expected_revision,
            reason=reason,
            request_id=request_id,
            _operation="complete_task",
            _payload_override=payload,
        )

    def reindex(self, *, check_lifecycle: bool = True) -> ReindexResult:
        with RepositoryLock(self.lock_path):
            return self.indexer.reindex(check_lifecycle=check_lifecycle)

    def flush_commits(self) -> CommitOutcome:
        return self.git.flush()

    def close(self, *, flush: bool = True) -> None:
        if flush:
            self.flush_commits()
        else:
            self.git.cancel()

    def validation_errors(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.validation_errors()]

    def doctor(self) -> dict[str, Any]:
        git = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        degraded = self.git.degraded_error()
        return {
            "repository": "ok" if git.returncode == 0 else git.stderr.strip(),
            "repository_revision": repository_revision(self.root),
            "working_tree_clean": git.returncode == 0 and not git.stdout.strip(),
            "commit_pending": self.git.has_pending,
            "degraded": degraded is not None,
            "git_error": degraded,
            "push_after_commit": self.config.git.push_after_commit,
            "git_remote": self.config.git.remote,
            "sqlite_integrity": self.database.integrity_check(),
            "validation_errors": len(self.validation_errors()),
            "watcher_running": self.watcher_running,
            "watcher_events": self.watcher_events,
            "watcher_error": self.watcher_error,
            "schema_version": (self.root / ".workbench" / "schema-version")
            .read_text(encoding="utf-8")
            .strip(),
        }

    def _workspace_key(self) -> str:
        document = self.store.read(self.root / "_workspace.md")
        key = frontmatter_data(document).get("key")
        if not isinstance(key, str):
            raise WorkbenchError("workspace key is missing")
        return key

    def _would_create_parent_cycle(self, source_id: str, target_id: str) -> bool:
        edges: dict[str, set[str]] = {}
        for row in self.database.list_relations():
            if row["relation_type"] == "parent_of":
                edges.setdefault(str(row["source_id"]), set()).add(
                    str(row["target_id"])
                )
        edges.setdefault(source_id, set()).add(target_id)
        pending = [target_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == source_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(edges.get(current, ()))
        return False

    @staticmethod
    def _default_next_step(context: dict[str, Any]) -> str:
        blockers = context.get("blockers") or []
        if blockers:
            return f"Resolve blocker {blockers[0]['key']}: {blockers[0]['title']}"
        suggested = context.get("suggested_next_tasks") or []
        if suggested:
            return f"Continue with {suggested[0]['key']}: {suggested[0]['title']}"
        return "Review the project backlog and select the next actionable item."

    @staticmethod
    def _relation_view(
        row: Any,
        *,
        relation_type: str,
        stored_type: str,
        direction: str,
        source_project: Any,
    ) -> dict[str, Any]:
        return {
            "relation_type": relation_type,
            "stored_type": stored_type,
            "direction": direction,
            "cross_project": row["project"] != source_project,
            "item": {
                "id": str(row["id"]),
                "key": str(row["key"]),
                "type": str(row["type"]),
                "title": str(row["title"]),
                "status": str(row["status"]),
                "project": row["project"],
                "tags": json.loads(str(row["tags_json"])),
            },
        }

    @staticmethod
    def _new_item_for_type(
        item_type: ItemType,
        common: dict[str, Any],
        fields: dict[str, Any],
        *,
        actor: str,
    ) -> ItemModel:
        allowed = {
            ItemType.TASK: {"priority", "assignee", "estimate", "due"},
            ItemType.BUG: {"severity", "priority", "assignee", "affected_version"},
            ItemType.FEATURE: {"priority", "owner", "target_release"},
            ItemType.IDEA: {"score"},
            ItemType.DECISION: {"supersedes", "owners"},
        }[item_type]
        unsupported = sorted(set(fields) - allowed)
        if unsupported:
            raise WorkbenchError(
                f"unsupported fields for {item_type.value}: {', '.join(unsupported)}"
            )
        if item_type == ItemType.TASK:
            return TaskItem(**common, **fields)
        if item_type == ItemType.BUG:
            return BugItem(**common, **fields)
        if item_type == ItemType.FEATURE:
            return FeatureItem(**common, **{"owner": actor, **fields})
        if item_type == ItemType.IDEA:
            return IdeaItem(**common, **fields)
        return DecisionItem(**common, **{"owners": [actor], **fields})

    @staticmethod
    def _editable_fields(item: ItemModel) -> set[str]:
        common = {"title", "tags", "rank"}
        by_type = {
            ItemType.TASK: {"priority", "assignee", "estimate", "due"},
            ItemType.BUG: {"severity", "priority", "assignee", "affected_version"},
            ItemType.FEATURE: {"priority", "owner", "target_release"},
            ItemType.IDEA: {"score"},
            ItemType.DECISION: {"supersedes", "owners"},
        }
        return common | by_type[item.type]

    def _next_item_key(self, prefix: str, item_type: ItemType) -> str:
        stem = f"{prefix}-{item_type.key_code}-"
        maximum = 0
        for row in self.database.list_items(include_archived=True):
            key = str(row["key"])
            if key.startswith(stem):
                try:
                    maximum = max(maximum, int(key.removeprefix(stem)))
                except ValueError:
                    continue
        candidates = list(self.root.glob(f"workspace/{stem}*.md"))
        candidates.extend(self.root.glob(f"projects/*/{stem}*.md"))
        for path in candidates:
            suffix = path.stem.removeprefix(stem)
            if suffix.isdigit():
                maximum = max(maximum, int(suffix))
        return f"{stem}{maximum + 1:03d}"

    @staticmethod
    def _apply_outcome(
        result: ItemResult,
        outcome: CommitOutcome,
        request_id: str | None,
    ) -> None:
        result.commit_pending = outcome.commit_pending
        result.repository_revision = outcome.repository_revision
        result.request_id = request_id

    @staticmethod
    def _check_revision(expected: str | None, actual: str) -> None:
        if expected is not None and expected != actual:
            raise RevisionConflict(expected, actual)
