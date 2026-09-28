from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workbench.config import CommitMode, GitConfig
from workbench.index.indexer import repository_revision
from workbench.repository.locking import RepositoryLock


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitChange:
    paths: tuple[str, ...]
    subject: str
    actor: str
    reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    commit_pending: bool
    repository_revision: str
    committed: bool = False


class GitTransactionManager:
    def __init__(
        self,
        root: Path,
        lock_path: Path,
        config: GitConfig,
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.lock_path = lock_path
        self.config = config
        self._on_commit = on_commit
        self._mutex = threading.RLock()
        self._pending: list[GitChange] = []
        self._timer: threading.Timer | None = None
        self._degraded_path = self.root / ".workbench" / "state" / "git-degraded.json"

    def record_change(
        self, change: GitChange, *, lock_held: bool = False
    ) -> CommitOutcome:
        if self.config.commit_mode == CommitMode.IMMEDIATE:
            revision = self._commit_with_optional_lock([change], lock_held=lock_held)
            return CommitOutcome(False, revision, committed=True)

        with self._mutex:
            self._pending.append(change)
            if self.config.commit_mode == CommitMode.DEBOUNCE:
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = threading.Timer(
                    self.config.debounce_seconds, self._timer_flush
                )
                self._timer.daemon = True
                self._timer.start()
        return CommitOutcome(True, repository_revision(self.root))

    def commit_now(
        self, change: GitChange, *, lock_held: bool = False
    ) -> CommitOutcome:
        revision = self._commit_with_optional_lock([change], lock_held=lock_held)
        return CommitOutcome(False, revision, committed=True)

    def flush(self, *, lock_held: bool = False) -> CommitOutcome:
        with self._mutex:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._pending:
                return CommitOutcome(
                    False, repository_revision(self.root), committed=False
                )
            changes = self._pending
            self._pending = []

        try:
            revision = self._commit_with_optional_lock(changes, lock_held=lock_held)
        except RepositoryError:
            with self._mutex:
                self._pending = changes + self._pending
            raise
        return CommitOutcome(False, revision, committed=True)

    def cancel(self) -> None:
        with self._mutex:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = []

    @property
    def has_pending(self) -> bool:
        with self._mutex:
            return bool(self._pending)

    def degraded_error(self) -> dict[str, object] | None:
        if not self._degraded_path.exists():
            return None
        try:
            value = json.loads(self._degraded_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"message": "Git commit failed; diagnostic record is unreadable"}
        return value if isinstance(value, dict) else {"message": str(value)}

    def assert_paths_available(self, paths: tuple[str, ...]) -> None:
        self._validate_paths(list(paths))
        unmerged = self._git("diff", "--name-only", "--diff-filter=U", "--", *paths)
        if unmerged.returncode != 0:
            raise RepositoryError(
                unmerged.stderr.strip() or "could not inspect merge conflicts"
            )
        if unmerged.stdout.strip():
            raise RepositoryError(
                f"target path has unresolved conflicts: {unmerged.stdout.strip()}"
            )

        staged = self._git("diff", "--cached", "--quiet", "--", *paths)
        if staged.returncode == 1:
            raise RepositoryError("target path already has staged changes")
        if staged.returncode != 0:
            raise RepositoryError(
                staged.stderr.strip() or "could not inspect staged changes"
            )

    def _timer_flush(self) -> None:
        try:
            self.flush()
        except RepositoryError:
            # The failure is persisted for health/doctor. A timer thread has no caller to notify.
            return

    def _commit_with_optional_lock(
        self, changes: list[GitChange], *, lock_held: bool
    ) -> str:
        if lock_held:
            return self._commit(changes)
        with RepositoryLock(self.lock_path):
            return self._commit(changes)

    def _commit(self, changes: list[GitChange]) -> str:
        paths = sorted({path for change in changes for path in change.paths})
        if not paths:
            return repository_revision(self.root)
        self._validate_paths(paths)

        add = self._git("add", "--", *paths)
        if add.returncode != 0:
            self._record_failure("git add", add.stderr, changes)
            raise RepositoryError(add.stderr.strip() or "git add failed")

        changed = self._git("diff", "--cached", "--quiet", "--", *paths)
        if changed.returncode == 0:
            self._clear_failure()
            return repository_revision(self.root)
        if changed.returncode != 1:
            self._record_failure("git diff --cached", changed.stderr, changes)
            raise RepositoryError(
                changed.stderr.strip() or "could not inspect staged changes"
            )

        message = self._commit_message(changes)
        commit = self._git(
            "commit", "--only", "-m", message, "--", *paths, commit_environment=True
        )
        if commit.returncode != 0:
            self._record_failure("git commit", commit.stderr, changes)
            raise RepositoryError(commit.stderr.strip() or "git commit failed")
        self._clear_failure()
        if self.config.push_after_commit:
            push = self._git("push", self.config.remote, "HEAD")
            if push.returncode != 0:
                self._record_failure("git push", push.stderr, changes)
        if self._on_commit is not None:
            self._on_commit()
        return repository_revision(self.root)

    def _git(
        self, *arguments: str, commit_environment: bool = False
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if commit_environment:
            environment.update(
                {
                    "GIT_AUTHOR_NAME": self.config.author_name,
                    "GIT_AUTHOR_EMAIL": self.config.author_email,
                    "GIT_COMMITTER_NAME": self.config.author_name,
                    "GIT_COMMITTER_EMAIL": self.config.author_email,
                }
            )
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def _validate_paths(self, paths: list[str]) -> None:
        for relative in paths:
            absolute = (self.root / relative).resolve()
            try:
                absolute.relative_to(self.root)
            except ValueError as exc:
                raise RepositoryError(
                    f"refusing to commit path outside repository: {relative}"
                ) from exc

    @staticmethod
    def _commit_message(changes: list[GitChange]) -> str:
        if len(changes) == 1:
            title = changes[0].subject
        else:
            title = f"chore(workbench): apply {len(changes)} changes"
        actors = sorted({change.actor for change in changes})
        request_ids = sorted(
            {change.request_id for change in changes if change.request_id}
        )
        reasons = [change.reason for change in changes if change.reason]
        trailers = [f"Actor: {', '.join(actors)}"]
        if request_ids:
            trailers.append(f"Request-Id: {', '.join(request_ids)}")
        if reasons:
            trailers.append(f"Reason: {'; '.join(dict.fromkeys(reasons))}")
        return f"{title}\n\n" + "\n".join(trailers)

    def _record_failure(
        self, operation: str, error: str, changes: list[GitChange]
    ) -> None:
        self._degraded_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "operation": operation,
            "message": error.strip() or f"{operation} failed",
            "detected_at": datetime.now(UTC).isoformat(),
            "paths": sorted({path for change in changes for path in change.paths}),
        }
        temporary = self._degraded_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self._degraded_path)

    def _clear_failure(self) -> None:
        self._degraded_path.unlink(missing_ok=True)
