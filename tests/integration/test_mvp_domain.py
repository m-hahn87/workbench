from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from workbench.core.service import Workbench, WorkbenchError


def _workbench(tmp_path: Path) -> Workbench:
    workbench = Workbench.initialize(tmp_path / "data", commit_mode="immediate")
    workbench.create_project("Merge Quest", key="MQ", actor="matze")
    return workbench


def test_all_mvp_item_types_have_keys_bodies_and_lifecycles(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)

    task = workbench.create_item(
        item_type="task", title="Task", project="MQ", actor="codex"
    )
    bug = workbench.create_item(
        item_type="bug",
        title="Bug",
        project="MQ",
        actor="codex",
        fields={"severity": "high", "affected_version": "0.1.0"},
    )
    feature = workbench.create_item(
        item_type="feature", title="Feature", project="MQ", actor="matze"
    )
    idea = workbench.create_item(item_type="idea", title="Idea", actor="matze")
    decision = workbench.create_item(
        item_type="decision", title="Decision", project="MQ", actor="matze"
    )

    assert task.data["key"] == "MQ-TASK-001"
    assert bug.data["key"] == "MQ-BUG-001"
    assert feature.data["key"] == "MQ-FEAT-001"
    assert idea.data["key"] == "WB-IDEA-001"
    assert decision.data["key"] == "MQ-DEC-001"
    assert "Schritte zur Reproduktion" in bug.body
    assert "Konsequenzen" in decision.body

    assert (
        workbench.transition_item(bug.data["key"], "triaged", actor="codex").data[
            "status"
        ]
        == "triaged"
    )
    assert (
        workbench.transition_item(feature.data["key"], "planned", actor="matze").data[
            "status"
        ]
        == "planned"
    )
    assert (
        workbench.transition_item(decision.data["key"], "accepted", actor="matze").data[
            "status"
        ]
        == "accepted"
    )


def test_update_assignment_comment_and_type_change_are_atomic(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    created = workbench.create_item(
        item_type="task", title="Original", project="MQ", actor="codex"
    )
    item_id = created.data["id"]

    updated = workbench.update_item(
        created.data["key"],
        actor="matze",
        changes={"title": "Updated", "priority": "high"},
        expected_revision=created.item_revision,
    )
    assigned = workbench.assign_item(
        item_id,
        "codex",
        actor="matze",
        expected_revision=updated.item_revision,
    )
    commented = workbench.add_comment(
        item_id,
        "Ready for implementation.",
        actor="matze",
        expected_revision=assigned.item_revision,
        request_id="comment-1",
    )
    duplicate = workbench.add_comment(
        item_id,
        "Ready for implementation.",
        actor="matze",
        expected_revision=assigned.item_revision,
        request_id="comment-1",
    )
    changed = workbench.change_item_type(
        item_id,
        "bug",
        actor="codex",
        fields={"severity": "critical"},
        expected_revision=commented.item_revision,
    )

    assert commented.item_revision == duplicate.item_revision
    assert commented.body.count("Ready for implementation.") == 1
    assert changed.data["id"] == item_id
    assert changed.data["key"] == "MQ-BUG-001"
    assert changed.data["severity"] == "critical"
    assert not (workbench.root / "projects/merge-quest/MQ-TASK-001.md").exists()


def test_relations_expose_inverse_views_and_reject_parent_cycles(
    tmp_path: Path,
) -> None:
    workbench = _workbench(tmp_path)
    parent = workbench.create_item(
        item_type="feature", title="Parent", project="MQ", actor="matze"
    )
    child = workbench.create_item(
        item_type="task", title="Child", project="MQ", actor="codex"
    )

    linked = workbench.link_items(
        parent.data["key"], child.data["key"], "parent_of", actor="matze"
    )
    outgoing = workbench.get_related(parent.data["key"])
    incoming = workbench.get_related(child.data["key"])

    assert linked.data["relations"][0]["target"] == child.data["id"]
    assert outgoing[0]["relation_type"] == "parent_of"
    assert outgoing[0]["item"]["key"] == child.data["key"]
    assert incoming[0]["relation_type"] == "child_of"
    assert incoming[0]["item"]["key"] == parent.data["key"]

    with pytest.raises(WorkbenchError, match="cycle"):
        workbench.link_items(
            child.data["key"], parent.data["key"], "parent_of", actor="codex"
        )

    unlinked = workbench.unlink_items(
        parent.data["key"], child.data["key"], "parent_of", actor="matze"
    )
    assert unlinked.data["relations"] == []


def test_activity_context_handoff_and_agent_claim_flow(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    task = workbench.create_item(
        item_type="task",
        title="Ready for another agent",
        project="MQ",
        actor="agent-a",
        request_id="create-ready-task",
    )
    workbench.transition_item(task.data["key"], "ready", actor="agent-a")
    decision = workbench.create_item(
        item_type="decision",
        title="Use deterministic merges",
        project="MQ",
        actor="matze",
    )
    workbench.transition_item(decision.data["key"], "accepted", actor="matze")

    claimed = workbench.claim_next_task(
        "merge-quest", actor="agent-b", request_id="claim-next"
    )
    context = workbench.get_project_context("MQ")
    activity = workbench.get_activity(project="MQ")
    handoff = workbench.prepare_project_handoff(
        "MQ",
        from_actor="agent-a",
        to_actor="agent-b",
        assumptions=["The merge order is stable."],
    )

    assert claimed.data["status"] == "in-progress"
    assert claimed.data["assignee"] == "agent-b"
    assert isinstance(context, dict)
    assert context["decisions"][0]["key"] == decision.data["key"]
    assert any(entry["actor"] == "agent-a" for entry in activity)
    assert handoff["to_actor"] == "agent-b"
    assert handoff["assumptions"] == ["The merge order is stable."]
    assert handoff["repository_revision"] == context["repository_revision"]


def test_agent_workflows_capture_plan_and_record_atomically(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    idea = workbench.capture_idea(
        "Offline mode",
        actor="agent-a",
        description="Keep capture available without the service.",
        score=8,
        request_id="capture-offline",
    )
    decision = workbench.record_decision(
        "Use append-only migrations",
        project="MQ",
        actor="agent-a",
        context="Existing Markdown must stay readable.",
        decision="Apply versioned, forward-only schema migrations.",
        consequences="Rollback restores files from Git.",
        request_id="record-migrations",
    )
    commits_before = int(
        subprocess.run(
            ["git", "-C", str(workbench.root), "rev-list", "--count", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    plan = workbench.plan_feature(
        "Release automation",
        project="MQ",
        actor="agent-b",
        description="Produce repeatable releases.",
        task_titles=["Build artifacts", "Publish checksums"],
        decision_title="Choose artifact registry",
        fields={"target_release": "1.0.0"},
        request_id="plan-release",
    )
    duplicate = workbench.plan_feature(
        "Release automation",
        project="MQ",
        actor="agent-b",
        description="Produce repeatable releases.",
        task_titles=["Build artifacts", "Publish checksums"],
        decision_title="Choose artifact registry",
        fields={"target_release": "1.0.0"},
        request_id="plan-release",
    )
    commits_after = int(
        subprocess.run(
            ["git", "-C", str(workbench.root), "rev-list", "--count", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )

    feature = plan["data"]["feature"]
    planned_tasks = plan["data"]["tasks"]
    assert idea.data["scope"] == "workspace"
    assert idea.data["score"] == 8
    assert "forward-only schema migrations" in decision.body
    assert feature["data"]["key"] == "MQ-FEAT-001"
    assert [task["data"]["key"] for task in planned_tasks] == [
        "MQ-TASK-001",
        "MQ-TASK-002",
    ]
    assert len(feature["data"]["relations"]) == 3
    assert duplicate == plan
    assert commits_after == commits_before + 1
