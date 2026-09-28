from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from workbench.adapters.rest.app import create_app
from workbench.core.service import Workbench


def test_read_only_web_ui_routes_and_markdown_rendering(tmp_path: Path) -> None:
    root = tmp_path / "data"
    workbench = Workbench.initialize(root, commit_mode="immediate")
    workbench.create_project("Merge Quest", key="MQ", actor="matze")
    workbench.create_item(
        item_type="task",
        title="Merge mechanic",
        project="merge-quest",
        actor="codex",
        description="Render **boldly**.\n\n<script>alert('no')</script>",
    )
    blocked = workbench.create_item(
        item_type="task",
        title="Blocked animation",
        project="merge-quest",
        actor="codex",
    )
    ready = workbench.transition_item(blocked.data["key"], "ready", actor="codex")
    active = workbench.transition_item(
        blocked.data["key"],
        "in-progress",
        actor="codex",
        expected_revision=ready.item_revision,
    )
    workbench.transition_item(
        blocked.data["key"],
        "blocked",
        actor="codex",
        expected_revision=active.item_revision,
    )
    workbench.create_item(item_type="idea", title="A workspace idea", actor="matze")

    with TestClient(create_app(root)) as client:
        root_response = client.get("/", follow_redirects=False)
        dashboard = client.get("/ui/")
        project = client.get("/ui/projects/merge-quest")
        item = client.get("/ui/items/MQ-TASK-001")
        search = client.get("/ui/search", params={"q": "mechanic"})
        validation = client.get("/ui/validation")
        missing = client.get("/ui/items/missing")
        stylesheet = client.get("/ui/static/app.css")

    assert root_response.status_code == 307
    assert root_response.headers["location"] == "/ui/"
    assert dashboard.status_code == 200
    assert "What needs attention?" in dashboard.text
    assert "Merge Quest" in dashboard.text
    assert "A workspace idea" in dashboard.text
    assert project.status_code == 200
    assert "Work board" in project.text
    assert "Blocked animation" in project.text
    assert "work-card-blocked" in project.text
    assert ">Blocked</span>" in project.text
    assert item.status_code == 200
    assert "<strong>boldly</strong>" in item.text
    assert "<script>alert('no')</script>" not in item.text
    assert "&lt;script&gt;alert('no')&lt;/script&gt;" in item.text
    assert search.status_code == 200
    assert "Merge mechanic" in search.text
    assert validation.status_code == 200
    assert "All checks passed" in validation.text
    assert missing.status_code == 404
    assert "Item not found" in missing.text
    assert stylesheet.status_code == 200
    assert "--orange: #f97316" in stylesheet.text


def test_web_ui_uses_api_key_login_without_storing_plaintext_key(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "data"
    Workbench.initialize(root, commit_mode="manual")
    monkeypatch.setenv("WORKBENCH_API_KEY", "ui-secret")

    with TestClient(create_app(root)) as client:
        protected = client.get("/ui/", follow_redirects=False)
        login_page = client.get("/ui/login")
        rejected = client.post(
            "/ui/login",
            data={"token": "wrong", "next": "/ui/"},
            follow_redirects=False,
        )
        accepted = client.post(
            "/ui/login",
            data={"token": "ui-secret", "next": "/ui/"},
            follow_redirects=False,
        )
        session_cookie = client.cookies.get("workbench_ui_session")
        dashboard = client.get("/ui/")
        logout = client.post("/ui/logout", follow_redirects=False)

    assert protected.status_code == 303
    assert protected.headers["location"].startswith("/ui/login")
    assert login_page.status_code == 200
    assert "Enter your API key" in login_page.text
    assert rejected.status_code == 401
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/ui/"
    assert session_cookie and session_cookie != "ui-secret"
    assert dashboard.status_code == 200
    assert logout.status_code == 303
