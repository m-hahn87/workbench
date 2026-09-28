from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from workbench.domain.models import Scope, TaskItem, new_uuid7
from workbench.domain.transitions import TransitionNotAllowed, ensure_transition


def test_generated_identity_is_rfc_uuid7() -> None:
    identifier = new_uuid7()

    assert identifier.version == 7
    assert identifier.variant == uuid.RFC_4122


def test_project_scope_requires_project_slug() -> None:
    with pytest.raises(ValidationError, match="project is required"):
        TaskItem(
            key="MQ-TASK-001",
            title="A task",
            scope=Scope.PROJECT,
            created_by="codex",
            updated_by="codex",
        )


def test_task_transitions_follow_the_lifecycle_graph() -> None:
    item = TaskItem(
        key="MQ-TASK-001",
        title="A task",
        scope=Scope.PROJECT,
        project="merge-quest",
        created_by="codex",
        updated_by="codex",
    )

    assert ensure_transition(item, "ready").value == "ready"
    with pytest.raises(TransitionNotAllowed):
        ensure_transition(item, "done")
