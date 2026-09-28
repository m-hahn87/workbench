from __future__ import annotations

from pathlib import Path

import pytest

from workbench.core.service import ItemNotFound, RevisionConflict, Workbench
from workbench.domain.transitions import TransitionNotAllowed


@pytest.fixture()
def workbench(tmp_path: Path) -> Workbench:
    result = Workbench.initialize(
        tmp_path / "data",
        title="Test Workspace",
        key="WB",
        commit_mode="manual",
    )
    result.create_project("Merge Quest", key="MQ")
    return result


def test_create_query_transition_search_archive_and_restore(
    workbench: Workbench,
) -> None:
    created = workbench.create_item(
        item_type="task",
        title="Core merge mechanic",
        project="merge-quest",
        actor="codex",
        tags=["prototype"],
    )

    assert created.data["key"] == "MQ-TASK-001"
    assert (workbench.root / created.file_path).exists()
    assert workbench.get_item(created.data["id"]).data["key"] == "MQ-TASK-001"
    assert workbench.list_items(project="MQ")[0]["title"] == "Core merge mechanic"
    assert workbench.search("merge")[0]["key"] == "MQ-TASK-001"

    ready = workbench.transition_item(
        "MQ-TASK-001",
        "ready",
        actor="codex",
        expected_revision=created.item_revision,
    )
    assert ready.data["status"] == "ready"
    assert ready.item_revision != created.item_revision

    with pytest.raises(RevisionConflict):
        workbench.transition_item(
            "MQ-TASK-001",
            "in-progress",
            actor="other-agent",
            expected_revision=created.item_revision,
        )
    with pytest.raises(TransitionNotAllowed):
        workbench.transition_item("MQ-TASK-001", "done", actor="codex")

    archived = workbench.archive_item("MQ-TASK-001", actor="codex")
    assert archived.data["archived_from"] == "ready"
    assert workbench.list_items(project="merge-quest") == []

    restored = workbench.restore_item(
        "MQ-TASK-001",
        actor="codex",
        expected_revision=archived.item_revision,
    )
    assert restored.data["status"] == "ready"
    assert "archived_from" not in restored.data


def test_reindex_tolerates_invalid_files_and_flags_direct_invalid_transition(
    workbench: Workbench,
) -> None:
    created = workbench.create_item(
        item_type="task",
        title="Indexed task",
        project="merge-quest",
        actor="codex",
    )
    workbench.transition_item(
        created.data["key"],
        "ready",
        actor="codex",
        expected_revision=created.item_revision,
    )
    path = workbench.root / created.file_path
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("status: ready", "status: done"), encoding="utf-8")
    (workbench.root / "workspace" / "BROKEN.md").write_text(
        "not frontmatter\n", encoding="utf-8"
    )

    result = workbench.reindex()
    errors = workbench.validation_errors()

    assert result.items == 0
    assert {error["error_code"] for error in errors} == {
        "invalid_item",
        "transition_not_allowed",
    }
    with pytest.raises(ItemNotFound):
        workbench.get_item(created.data["key"])

    second_result = workbench.reindex()
    assert second_result.items == 0
    assert any(
        error["error_code"] == "transition_not_allowed"
        for error in workbench.validation_errors()
    )


def test_workspace_idea_gets_independent_readable_sequence(
    workbench: Workbench,
) -> None:
    first = workbench.create_item(item_type="idea", title="First idea", actor="matze")
    second = workbench.create_item(item_type="idea", title="Second idea", actor="matze")

    assert first.data["key"] == "WB-IDEA-001"
    assert second.data["key"] == "WB-IDEA-002"
    assert first.data["scope"] == "workspace"


def test_index_can_be_deleted_and_fully_rebuilt(workbench: Workbench) -> None:
    created = workbench.create_item(
        item_type="decision",
        title="Rebuild from Markdown",
        project="merge-quest",
        actor="codex",
    )
    database_path = workbench.database.path
    database_path.unlink()
    database_path.with_name(database_path.name + "-wal").unlink(missing_ok=True)
    database_path.with_name(database_path.name + "-shm").unlink(missing_ok=True)

    result = workbench.reindex(check_lifecycle=False)

    assert result.errors == 0
    assert workbench.get_item(created.data["id"]).data["key"] == "MQ-DEC-001"
    assert workbench.search("Rebuild")[0]["id"] == created.data["id"]
