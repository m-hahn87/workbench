from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from workbench.core.service import Workbench
from workbench.repository.git_transactions import RepositoryError
from workbench.state.idempotency import DuplicateRequest


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_immediate_mode_commits_only_mutation_paths(tmp_path: Path) -> None:
    workbench = Workbench.initialize(tmp_path / "data", commit_mode="immediate")
    assert git(workbench.root, "rev-list", "--count", "HEAD") == "1"

    project = workbench.create_project(
        "Merge Quest",
        key="MQ",
        actor="matze",
        request_id="req-project",
    )
    assert project.commit_pending is False
    assert git(workbench.root, "rev-list", "--count", "HEAD") == "2"

    unrelated = workbench.root / "unrelated.txt"
    unrelated.write_text("belongs to the user\n", encoding="utf-8")
    git(workbench.root, "add", "--", "unrelated.txt")
    item = workbench.create_item(
        item_type="task",
        title="Committed task",
        project="merge-quest",
        actor="codex",
        reason="Exercise the transaction path",
        request_id="req-item",
    )

    assert item.commit_pending is False
    assert item.repository_revision == git(workbench.root, "rev-parse", "HEAD")
    assert git(workbench.root, "rev-list", "--count", "HEAD") == "3"
    assert (
        git(workbench.root, "ls-tree", "--name-only", "HEAD", "--", "unrelated.txt")
        == ""
    )
    assert "A  unrelated.txt" in git(workbench.root, "status", "--porcelain")
    message = git(workbench.root, "show", "-s", "--format=%B", "HEAD")
    assert "feat(MQ-TASK-001): create task" in message
    assert "Actor: codex" in message
    assert "Request-Id: req-item" in message
    assert "Reason: Exercise the transaction path" in message


def test_mutation_rejects_a_target_path_with_staged_user_changes(
    tmp_path: Path,
) -> None:
    workbench = Workbench.initialize(tmp_path / "data", commit_mode="immediate")
    workbench.create_project("Merge Quest", key="MQ")
    created = workbench.create_item(
        item_type="task",
        title="Protected task",
        project="merge-quest",
        actor="codex",
    )
    path = workbench.root / created.file_path
    path.write_text(
        path.read_text(encoding="utf-8") + "\nUser note.\n", encoding="utf-8"
    )
    git(workbench.root, "add", "--", created.file_path)

    with pytest.raises(RepositoryError, match="staged changes"):
        workbench.transition_item("MQ-TASK-001", "ready", actor="codex")

    assert workbench.store.read_item(path)[0].status.value == "backlog"


def test_key_generation_does_not_overwrite_an_invalid_unindexed_file(
    tmp_path: Path,
) -> None:
    workbench = Workbench.initialize(tmp_path / "data", commit_mode="manual")
    workbench.create_project("Merge Quest", key="MQ")
    invalid = workbench.root / "projects" / "merge-quest" / "MQ-TASK-001.md"
    invalid.write_text("not valid frontmatter\n", encoding="utf-8")

    created = workbench.create_item(
        item_type="task",
        title="Safe task",
        project="merge-quest",
        actor="codex",
    )

    assert created.data["key"] == "MQ-TASK-002"
    assert invalid.read_text(encoding="utf-8") == "not valid frontmatter\n"


def test_debounce_mode_batches_changes_until_flush(tmp_path: Path) -> None:
    workbench = Workbench.initialize(
        tmp_path / "data",
        commit_mode="debounce",
        debounce_seconds=60,
    )
    baseline_revision = git(workbench.root, "rev-parse", "HEAD")
    project = workbench.create_project("Merge Quest", key="MQ", actor="matze")
    item = workbench.create_item(
        item_type="task",
        title="Batched task",
        project="merge-quest",
        actor="codex",
    )

    assert project.commit_pending is True
    assert item.commit_pending is True
    assert git(workbench.root, "rev-parse", "HEAD") == baseline_revision

    outcome = workbench.flush_commits()

    assert outcome.committed is True
    assert outcome.commit_pending is False
    assert git(workbench.root, "rev-list", "--count", "HEAD") == "2"
    assert "chore(workbench): apply 2 changes" in git(
        workbench.root,
        "show",
        "-s",
        "--format=%B",
        "HEAD",
    )
    assert (
        workbench.get_item("MQ-TASK-001").repository_revision
        == outcome.repository_revision
    )


def test_debounce_timer_commits_asynchronously_and_refreshes_revision(
    tmp_path: Path,
) -> None:
    workbench = Workbench.initialize(
        tmp_path / "data",
        commit_mode="debounce",
        debounce_seconds=0.1,
    )
    baseline_revision = git(workbench.root, "rev-parse", "HEAD")
    project = workbench.create_project("Async Project", key="AP", actor="codex")
    assert project.commit_pending is True

    deadline = time.monotonic() + 5
    while git(workbench.root, "rev-parse", "HEAD") == baseline_revision:
        if time.monotonic() >= deadline:
            pytest.fail("debounced Git commit did not complete")
        time.sleep(0.05)

    head = git(workbench.root, "rev-parse", "HEAD")
    while workbench.get_project("async-project")["repository_revision"] != head:
        if time.monotonic() >= deadline:
            pytest.fail("index was not refreshed after debounced Git commit")
        time.sleep(0.02)


def test_request_id_is_persistent_and_cannot_be_reused_for_other_input(
    tmp_path: Path,
) -> None:
    workbench = Workbench.initialize(tmp_path / "data", commit_mode="immediate")
    first = workbench.create_project(
        "Merge Quest",
        key="MQ",
        actor="codex",
        request_id="req-stable",
    )
    commit_count = git(workbench.root, "rev-list", "--count", "HEAD")

    reopened = Workbench(workbench.root)
    retried = reopened.create_project(
        "Merge Quest",
        key="MQ",
        actor="codex",
        request_id="req-stable",
    )

    assert retried.data["id"] == first.data["id"]
    assert git(workbench.root, "rev-list", "--count", "HEAD") == commit_count
    with pytest.raises(DuplicateRequest):
        reopened.create_project(
            "Different Project",
            key="DP",
            actor="codex",
            request_id="req-stable",
        )
