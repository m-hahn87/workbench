from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from workbench.adapters.cli.app import app


runner = CliRunner()


def test_cli_covers_mvp_administration_and_context(tmp_path: Path) -> None:
    root = tmp_path / "data"
    initialized = runner.invoke(
        app,
        ["init", str(root), "--title", "CLI Workspace", "--commit-mode", "immediate"],
    )
    assert initialized.exit_code == 0, initialized.output

    project = runner.invoke(
        app,
        ["project", "create", "Merge Quest", "--key", "MQ", "--root", str(root)],
    )
    assert project.exit_code == 0, project.output

    bug = runner.invoke(
        app,
        [
            "item",
            "create",
            "--type",
            "bug",
            "--title",
            "CLI bug",
            "--project",
            "MQ",
            "--field",
            "severity=high",
            "--root",
            str(root),
        ],
    )
    assert bug.exit_code == 0, bug.output
    assert json.loads(bug.output)["data"]["key"] == "MQ-BUG-001"

    shown = runner.invoke(app, ["project", "show", "MQ", "--root", str(root)])
    context = runner.invoke(app, ["context", "MQ", "--root", str(root)])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["slug"] == "merge-quest"
    assert context.exit_code == 0, context.output
    assert json.loads(context.output)["summary"]["by_type"] == {"bug": 1}
