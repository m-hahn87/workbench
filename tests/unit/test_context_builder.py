from __future__ import annotations

from workbench.core.context_builder import (
    build_project_context,
    render_project_context_markdown,
)


def test_context_prioritizes_blockers_and_active_work() -> None:
    project = {
        "id": "project-id",
        "key": "MQ",
        "slug": "merge-quest",
        "title": "Merge Quest",
        "status": "active",
    }
    items = [
        _item("MQ-TASK-003", "backlog", "low"),
        _item("MQ-TASK-002", "in-progress", "high"),
        _item("MQ-TASK-001", "blocked", "urgent"),
    ]

    context = build_project_context(project, items, repository_revision="abc123")
    markdown = render_project_context_markdown(context)

    assert context["active_work"][0]["key"] == "MQ-TASK-001"
    assert context["blockers"][0]["status"] == "blocked"
    assert context["suggested_next_tasks"][0]["key"] == "MQ-TASK-001"
    assert "## Blockers" in markdown
    assert "MQ-TASK-001" in markdown
    assert "abc123" in markdown


def _item(key: str, status: str, priority: str) -> dict[str, object]:
    return {
        "id": f"id-{key}",
        "key": key,
        "type": "task",
        "title": key,
        "status": status,
        "priority": priority,
        "assignee": None,
        "updated_at": "2026-07-18T00:00:00+00:00",
        "rank": None,
    }
