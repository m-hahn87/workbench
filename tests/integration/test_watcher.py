from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from workbench.adapters.rest.app import create_app
from workbench.core.service import Workbench


def _eventually(predicate, *, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("watcher did not reconcile the direct edit in time")


def test_direct_markdown_edits_are_reindexed_and_lifecycle_checked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    workbench = Workbench.initialize(root, commit_mode="manual")
    workbench.create_project("Merge Quest", key="MQ", actor="matze")
    created = workbench.create_item(
        item_type="task", title="Watched task", project="MQ", actor="codex"
    )
    path = root / created.file_path

    with TestClient(create_app(root)) as client:
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original.replace("title: Watched task", "title: Directly edited task"),
            encoding="utf-8",
        )

        def title_is_searchable() -> bool:
            response = client.get("/api/v1/search", params={"q": "Directly edited"})
            return response.status_code == 200 and response.json()["data"]

        _eventually(title_is_searchable)

        edited = path.read_text(encoding="utf-8")
        path.write_text(
            edited.replace("status: backlog", "status: done"),
            encoding="utf-8",
        )

        def invalid_transition_is_reported() -> bool:
            response = client.get("/api/v1/validation-errors")
            errors = response.json().get("data", [])
            return any(
                error["error_code"] == "transition_not_allowed" for error in errors
            )

        _eventually(invalid_transition_is_reported)
