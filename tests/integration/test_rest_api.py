from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from workbench.adapters.rest.app import create_app
from workbench.core.service import Workbench


def test_rest_vertical_slice_and_error_mapping(tmp_path: Path) -> None:
    root = tmp_path / "data"
    Workbench.initialize(root, commit_mode="immediate")

    with TestClient(create_app(root)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200

        project = client.post(
            "/api/v1/projects",
            json={
                "title": "Merge Quest",
                "key": "MQ",
                "actor": "matze",
                "request_id": "req-project",
            },
        )
        assert project.status_code == 201
        assert project.json()["commit_pending"] is False

        created = client.post(
            "/api/v1/items",
            json={
                "type": "task",
                "title": "REST task",
                "project": "merge-quest",
                "actor": "codex",
                "request_id": "req-create",
            },
        )
        assert created.status_code == 201
        item = created.json()
        assert item["data"]["key"] == "MQ-TASK-001"

        retry = client.post(
            "/api/v1/items",
            json={
                "type": "task",
                "title": "REST task",
                "project": "merge-quest",
                "actor": "codex",
                "request_id": "req-create",
            },
        )
        assert retry.json()["data"]["id"] == item["data"]["id"]

        transition = client.post(
            "/api/v1/items/MQ-TASK-001/transitions",
            json={
                "status": "ready",
                "actor": "codex",
                "expected_revision": item["item_revision"],
            },
        )
        assert transition.status_code == 200
        assert transition.json()["data"]["status"] == "ready"

        conflict = client.post(
            "/api/v1/items/MQ-TASK-001/transitions",
            json={
                "status": "in-progress",
                "actor": "other-agent",
                "expected_revision": item["item_revision"],
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "revision_conflict"

        search = client.get("/api/v1/search", params={"q": "REST"})
        assert search.status_code == 200
        assert search.json()["data"][0]["key"] == "MQ-TASK-001"
        context = client.get("/api/v1/projects/merge-quest/context")
        assert context.status_code == 200
        assert context.json()["data"]["project"]["key"] == "MQ"
        markdown_context = client.get(
            "/api/v1/projects/merge-quest/context",
            params={"format": "markdown"},
        )
        assert "# MQ — Merge Quest" in markdown_context.json()["data"]
        assert client.get("/api/v1/items/missing").status_code == 404


def test_api_key_protects_api_but_not_health(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    Workbench.initialize(root, commit_mode="manual")
    monkeypatch.setenv("WORKBENCH_API_KEY", "test-secret")

    with TestClient(create_app(root)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/projects").status_code == 401
        authorized = client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer test-secret"},
        )
        assert authorized.status_code == 200


def test_rest_exposes_mvp_updates_relations_and_workflows(tmp_path: Path) -> None:
    root = tmp_path / "data"
    workbench = Workbench.initialize(root, commit_mode="immediate")
    workbench.create_project("Merge Quest", key="MQ", actor="matze")

    with TestClient(create_app(root)) as client:
        feature = client.post(
            "/api/v1/items",
            json={
                "type": "feature",
                "title": "Release feature",
                "project": "MQ",
                "actor": "agent-a",
                "request_id": "rest-feature",
                "fields": {"target_release": "1.0.0"},
            },
        ).json()
        task = client.post(
            "/api/v1/items",
            json={
                "type": "task",
                "title": "Claimable task",
                "project": "MQ",
                "actor": "agent-a",
                "request_id": "rest-task",
            },
        ).json()
        ready = client.post(
            f"/api/v1/items/{task['data']['key']}/transitions",
            json={
                "status": "ready",
                "actor": "agent-a",
                "expected_revision": task["item_revision"],
            },
        ).json()
        claimed = client.post(
            "/api/v1/workflows/claim-next-task",
            json={"project": "MQ", "actor": "agent-b", "request_id": "rest-claim"},
        )
        linked = client.post(
            "/api/v1/relations",
            json={
                "source": feature["data"]["key"],
                "target": task["data"]["key"],
                "type": "parent_of",
                "actor": "agent-a",
                "request_id": "rest-link",
            },
        )
        related = client.get(f"/api/v1/items/{task['data']['key']}/related")
        updated = client.patch(
            f"/api/v1/items/{feature['data']['key']}",
            json={
                "actor": "agent-a",
                "fields": {"title": "Updated release feature"},
                "expected_revision": linked.json()["item_revision"],
            },
        )
        handoff = client.post(
            "/api/v1/workflows/prepare-project-handoff",
            json={"project": "MQ", "from": "agent-a", "to": "agent-b"},
        )
        idea = client.post(
            "/api/v1/workflows/capture-idea",
            json={
                "title": "REST idea",
                "actor": "agent-a",
                "score": 5,
                "request_id": "rest-capture-idea",
            },
        )
        decision = client.post(
            "/api/v1/workflows/record-decision",
            json={
                "title": "REST decision",
                "project": "MQ",
                "actor": "agent-a",
                "context": "A choice is required.",
                "decision": "Use the deterministic path.",
                "request_id": "rest-record-decision",
            },
        )
        plan = client.post(
            "/api/v1/workflows/plan-feature",
            json={
                "title": "REST planned feature",
                "project": "MQ",
                "actor": "agent-a",
                "tasks": ["First planned task", "Second planned task"],
                "request_id": "rest-plan-feature",
            },
        )

    assert ready["data"]["status"] == "ready"
    assert claimed.status_code == 200
    assert claimed.json()["data"]["assignee"] == "agent-b"
    assert linked.status_code == 201
    assert related.json()["data"][0]["relation_type"] == "child_of"
    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "Updated release feature"
    assert handoff.status_code == 200
    assert handoff.json()["data"]["to_actor"] == "agent-b"
    assert idea.status_code == 201
    assert idea.json()["data"]["scope"] == "workspace"
    assert decision.status_code == 201
    assert "deterministic path" in decision.json()["body"]
    assert plan.status_code == 201
    assert len(plan.json()["data"]["tasks"]) == 2


def test_mcp_cors_preflight_does_not_require_bearer_token(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "data"
    Workbench.initialize(root, commit_mode="manual")
    monkeypatch.setenv("WORKBENCH_API_KEY", "test-secret")
    monkeypatch.setenv("WORKBENCH_ALLOWED_ORIGINS", "https://client.example")

    with TestClient(create_app(root)) as client:
        response = client.options(
            "/mcp/",
            headers={
                "Origin": "https://client.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example"
