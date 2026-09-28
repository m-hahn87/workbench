from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import uvicorn

from workbench.config import CommitMode
from workbench.core.service import ItemResult, ProjectResult, Workbench


app = typer.Typer(
    help="Git-native, Markdown-first project context store", no_args_is_help=True
)
project_app = typer.Typer(help="Manage projects", no_args_is_help=True)
item_app = typer.Typer(help="Manage Workbench items", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(item_app, name="item")


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _open(root: Path) -> Workbench:
    return Workbench(root)


def _parse_fields(values: list[str] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for value in values or []:
        name, separator, raw = value.partition("=")
        if not separator or not name:
            raise typer.BadParameter("fields must use NAME=VALUE")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        fields[name] = parsed
    return fields


def _finish_item_mutation(workbench: Workbench, result: ItemResult) -> ItemResult:
    if result.commit_pending and workbench.config.git.commit_mode != CommitMode.MANUAL:
        outcome = workbench.flush_commits()
        refreshed = workbench.get_item(str(result.data["id"]))
        refreshed.repository_revision = outcome.repository_revision
        refreshed.request_id = result.request_id
        return refreshed
    return result


def _finish_project_mutation(
    workbench: Workbench, result: ProjectResult
) -> ProjectResult:
    if result.commit_pending and workbench.config.git.commit_mode != CommitMode.MANUAL:
        outcome = workbench.flush_commits()
        result.commit_pending = False
        result.repository_revision = outcome.repository_revision
    return result


@app.command("init")
def init_repository(
    path: Path = typer.Argument(
        ..., help="Directory for the Workbench data repository"
    ),
    title: str = typer.Option("Workbench", help="Workspace title"),
    key: str = typer.Option("WB", help="Readable workspace key prefix"),
    commit_mode: str = typer.Option("debounce", help="immediate, debounce, or manual"),
) -> None:
    workbench = Workbench.initialize(
        path, title=title, key=key, commit_mode=commit_mode
    )
    _print({"root": str(workbench.root), "status": "initialized"})


@app.command()
def serve(
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8500, min=1, max=65535),
) -> None:
    from workbench.adapters.rest.app import create_app
    from workbench.observability import configure_logging

    configure_logging()
    uvicorn.run(create_app(root), host=host, port=port, log_config=None)


@app.command()
def reindex(
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
    check_lifecycle: bool = typer.Option(
        True, help="Compare status changes with the previous index"
    ),
) -> None:
    result = _open(root).reindex(check_lifecycle=check_lifecycle)
    _print(
        result.__dict__
        if hasattr(result, "__dict__")
        else {
            "projects": result.projects,
            "items": result.items,
            "errors": result.errors,
            "repository_revision": result.repository_revision,
        }
    )


@app.command()
def validate(
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.reindex()
    errors = workbench.validation_errors()
    _print({"valid": not errors, "indexed_items": result.items, "errors": errors})
    if errors:
        raise typer.Exit(code=1)


@app.command()
def doctor(
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).doctor())


@app.command()
def search(
    query: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
    limit: int = typer.Option(20, min=1, max=100),
) -> None:
    _print(_open(root).search(query, limit=limit))


@app.command()
def context(
    project: str = typer.Argument(...),
    format: str = typer.Option("json", help="json or markdown"),
    max_items: int = typer.Option(50, min=1, max=500),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    value = _open(root).get_project_context(project, format=format, max_items=max_items)
    if isinstance(value, str):
        typer.echo(value)
    else:
        _print(value)


@app.command()
def activity(
    project: str | None = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=500),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).get_activity(project=project, limit=limit))


@app.command("claim-next")
def claim_next(
    project: str = typer.Argument(...),
    actor: str = typer.Option(...),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.claim_next_task(
        project, actor=actor, reason=reason, request_id=request_id
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@app.command()
def handoff(
    project: str = typer.Argument(...),
    from_actor: str = typer.Option(..., "--from"),
    to_actor: str = typer.Option(..., "--to"),
    assumption: list[str] | None = typer.Option(None, "--assumption"),
    recommended_next_step: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(
        _open(root).prepare_project_handoff(
            project,
            from_actor=from_actor,
            to_actor=to_actor,
            assumptions=assumption,
            recommended_next_step=recommended_next_step,
        )
    )


@project_app.command("create")
def project_create(
    title: str = typer.Argument(...),
    key: str = typer.Option(..., help="2-8 character project prefix, for example MQ"),
    slug: str | None = typer.Option(None),
    actor: str = typer.Option("human"),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.create_project(
        title,
        key=key,
        slug=slug,
        actor=actor,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_project_mutation(workbench, result).as_dict())


@project_app.command("list")
def project_list(
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).list_projects())


@project_app.command("show")
def project_show(
    identifier: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).get_project(identifier))


@project_app.command("update")
def project_update(
    identifier: str = typer.Argument(...),
    title: str | None = typer.Option(None),
    status: str | None = typer.Option(None),
    actor: str = typer.Option("human"),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.update_project(
        identifier,
        actor=actor,
        title=title,
        status=status,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_project_mutation(workbench, result).as_dict())


@project_app.command("backlog")
def project_backlog(
    identifier: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).get_backlog(identifier))


@item_app.command("create")
def item_create(
    title: str = typer.Option(...),
    item_type: str = typer.Option(
        ..., "--type", help="task, bug, feature, idea, or decision"
    ),
    actor: str = typer.Option("human"),
    project: str | None = typer.Option(
        None, help="Project slug, key, or ID; omit for workspace scope"
    ),
    description: str | None = typer.Option(None),
    tag: list[str] | None = typer.Option(None, "--tag"),
    field: list[str] | None = typer.Option(
        None, "--field", help="Type-specific NAME=VALUE"
    ),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.create_item(
        item_type=item_type,
        title=title,
        actor=actor,
        project=project,
        description=description,
        tags=tag,
        fields=_parse_fields(field),
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("show")
def item_show(
    identifier: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).get_item(identifier).as_dict())


@item_app.command("list")
def item_list(
    project: str | None = typer.Option(None),
    item_type: str | None = typer.Option(None, "--type"),
    include_archived: bool = typer.Option(False),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(
        _open(root).list_items(
            project=project,
            item_type=item_type,
            include_archived=include_archived,
        )
    )


@item_app.command("transition")
def item_transition(
    identifier: str = typer.Argument(...),
    status: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.transition_item(
        identifier,
        status,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("archive")
def item_archive(
    identifier: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.archive_item(
        identifier,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("restore")
def item_restore(
    identifier: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.restore_item(
        identifier,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("update")
def item_update(
    identifier: str = typer.Argument(...),
    field: list[str] | None = typer.Option(None, "--field", help="Editable NAME=VALUE"),
    body_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.update_item(
        identifier,
        actor=actor,
        changes=_parse_fields(field),
        body=body_file.read_text(encoding="utf-8") if body_file else None,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("change-type")
def item_change_type(
    identifier: str = typer.Argument(...),
    target_type: str = typer.Argument(...),
    field: list[str] | None = typer.Option(
        None, "--field", help="Target-specific NAME=VALUE"
    ),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.change_item_type(
        identifier,
        target_type,
        actor=actor,
        fields=_parse_fields(field),
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("assign")
def item_assign(
    identifier: str = typer.Argument(...),
    assignee: str | None = typer.Argument(None),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.assign_item(
        identifier,
        assignee,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("comment")
def item_comment(
    identifier: str = typer.Argument(...),
    text: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.add_comment(
        identifier,
        text,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("link")
def item_link(
    source: str = typer.Argument(...),
    target: str = typer.Argument(...),
    relation_type: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.link_items(
        source,
        target,
        relation_type,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("unlink")
def item_unlink(
    source: str = typer.Argument(...),
    target: str = typer.Argument(...),
    relation_type: str = typer.Argument(...),
    actor: str = typer.Option("human"),
    expected_revision: str | None = typer.Option(None),
    reason: str | None = typer.Option(None),
    request_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    workbench = _open(root)
    result = workbench.unlink_items(
        source,
        target,
        relation_type,
        actor=actor,
        expected_revision=expected_revision,
        reason=reason,
        request_id=request_id,
    )
    _print(_finish_item_mutation(workbench, result).as_dict())


@item_app.command("related")
def item_related(
    identifier: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), help="Workbench data repository"),
) -> None:
    _print(_open(root).get_related(identifier))
