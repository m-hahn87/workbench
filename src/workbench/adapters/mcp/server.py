from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable
from functools import partial
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from workbench.core.service import Workbench, WorkbenchError
from workbench.domain.transitions import TransitionNotAllowed
from workbench.repository.git_transactions import RepositoryError
from workbench.state.idempotency import DuplicateRequest


MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_mcp_server(provider: Callable[[], Workbench]) -> FastMCP:
    server = FastMCP(
        name="Workbench",
        instructions=(
            "Read and update the shared Git-native project context. Use readable item keys in "
            "conversation, pass expected_revision when updating an item, and use request_id for "
            "safe retries. Prefer resources for reading project and item context."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=_transport_security(),
    )

    @server.tool(
        name="workbench_create_item",
        description="Create a task, bug, feature, idea, or decision in Workbench.",
        annotations=MUTATING,
    )
    async def create_item(
        item_type: str,
        title: str,
        actor: str,
        request_id: str,
        project: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = await anyio.to_thread.run_sync(
                partial(
                    provider().create_item,
                    item_type=item_type,
                    title=title,
                    actor=actor,
                    project=project,
                    description=description,
                    tags=tags,
                    fields=fields,
                    reason=reason,
                    request_id=request_id,
                )
            )
            return result.as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_capture_idea",
        description="Capture a project-independent idea in the workspace inbox.",
        annotations=MUTATING,
    )
    async def capture_idea(
        title: str,
        actor: str,
        request_id: str,
        description: str | None = None,
        score: int = 0,
        tags: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().capture_idea,
                        title,
                        actor=actor,
                        description=description,
                        score=score,
                        tags=tags,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_plan_feature",
        description="Atomically create a feature, its task breakdown and an optional decision.",
        annotations=MUTATING,
    )
    async def plan_feature(
        title: str,
        project: str,
        actor: str,
        request_id: str,
        description: str | None = None,
        tasks: list[str] | None = None,
        decision_title: str | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await anyio.to_thread.run_sync(
                partial(
                    provider().plan_feature,
                    title,
                    project=project,
                    actor=actor,
                    description=description,
                    task_titles=tasks,
                    decision_title=decision_title,
                    fields=fields,
                    reason=reason,
                    request_id=request_id,
                )
            )
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_record_decision",
        description="Record a structured project decision with context and consequences.",
        annotations=MUTATING,
    )
    async def record_decision(
        title: str,
        project: str,
        actor: str,
        request_id: str,
        context: str,
        decision: str,
        alternatives: str | None = None,
        consequences: str | None = None,
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().record_decision,
                        title,
                        project=project,
                        actor=actor,
                        context=context,
                        decision=decision,
                        alternatives=alternatives,
                        consequences=consequences,
                        tags=tags,
                        fields=fields,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_update_item",
        description="Update editable item fields or its Markdown body with optimistic locking.",
        annotations=MUTATING,
    )
    async def update_item(
        item_id: str,
        actor: str,
        request_id: str,
        fields: dict[str, Any] | None = None,
        body: str | None = None,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().update_item,
                        item_id,
                        actor=actor,
                        changes=fields,
                        body=body,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_transition_item",
        description=(
            "Move an item through its validated lifecycle. On revision_conflict, reload the item "
            "resource and retry at most once after checking the new state."
        ),
        annotations=MUTATING,
    )
    async def transition_item(
        item_id: str,
        status: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = await anyio.to_thread.run_sync(
                partial(
                    provider().transition_item,
                    item_id,
                    status,
                    actor=actor,
                    expected_revision=expected_revision,
                    reason=reason,
                    request_id=request_id,
                )
            )
            return result.as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_archive_item",
        description="Archive an item without deleting its canonical Markdown file.",
        annotations=MUTATING,
    )
    async def archive_item(
        item_id: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = await anyio.to_thread.run_sync(
                partial(
                    provider().archive_item,
                    item_id,
                    actor=actor,
                    expected_revision=expected_revision,
                    reason=reason,
                    request_id=request_id,
                )
            )
            return result.as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_restore_item",
        description="Restore an archived item to its previous lifecycle state.",
        annotations=MUTATING,
    )
    async def restore_item(
        item_id: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().restore_item,
                        item_id,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_change_item_type",
        description="Change an item type while preserving its technical identity and relations.",
        annotations=MUTATING,
    )
    async def change_item_type(
        item_id: str,
        target_type: str,
        actor: str,
        request_id: str,
        fields: dict[str, Any] | None = None,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().change_item_type,
                        item_id,
                        target_type,
                        actor=actor,
                        fields=fields,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_assign_item",
        description="Assign or unassign a task, bug, or feature.",
        annotations=MUTATING,
    )
    async def assign_item(
        item_id: str,
        actor: str,
        request_id: str,
        assignee: str | None = None,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().assign_item,
                        item_id,
                        assignee,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_add_comment",
        description="Append an attributed comment to an item's canonical Markdown body.",
        annotations=MUTATING,
    )
    async def add_comment(
        item_id: str,
        text: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().add_comment,
                        item_id,
                        text,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_link_items",
        description="Create a canonical typed relation between two items.",
        annotations=MUTATING,
    )
    async def link_items(
        source_id: str,
        target_id: str,
        relation_type: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().link_items,
                        source_id,
                        target_id,
                        relation_type,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_unlink_items",
        description="Remove a canonical typed relation between two items.",
        annotations=MUTATING,
    )
    async def unlink_items(
        source_id: str,
        target_id: str,
        relation_type: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().unlink_items,
                        source_id,
                        target_id,
                        relation_type,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_claim_next_task",
        description="Atomically claim the next unassigned ready task in a project.",
        annotations=MUTATING,
    )
    async def claim_next_task(
        project: str,
        actor: str,
        request_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().claim_next_task,
                        project,
                        actor=actor,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_complete_task",
        description="Advance a task to review, or from review to done.",
        annotations=MUTATING,
    )
    async def complete_task(
        item_id: str,
        actor: str,
        request_id: str,
        expected_revision: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return (
                await anyio.to_thread.run_sync(
                    partial(
                        provider().complete_task,
                        item_id,
                        actor=actor,
                        expected_revision=expected_revision,
                        reason=reason,
                        request_id=request_id,
                    )
                )
            ).as_dict()
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_get_related",
        description="Return outgoing canonical and computed inverse relations for an item.",
        annotations=READ_ONLY,
    )
    async def get_related(item_id: str) -> dict[str, Any]:
        try:
            related = await anyio.to_thread.run_sync(
                partial(provider().get_related, item_id)
            )
            return {"relations": related, "count": len(related)}
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_prepare_handoff",
        description="Build deterministic project handoff context without invoking an LLM.",
        annotations=READ_ONLY,
    )
    async def prepare_handoff(
        project: str,
        from_actor: str,
        to_actor: str,
        assumptions: list[str] | None = None,
        recommended_next_step: str | None = None,
        max_items: int = 50,
    ) -> dict[str, Any]:
        try:
            return await anyio.to_thread.run_sync(
                partial(
                    provider().prepare_project_handoff,
                    project,
                    from_actor=from_actor,
                    to_actor=to_actor,
                    assumptions=assumptions,
                    recommended_next_step=recommended_next_step,
                    max_items=max_items,
                )
            )
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.tool(
        name="workbench_search_items",
        description="Search item titles, bodies, keys, and tags using the derived index.",
        annotations=READ_ONLY,
    )
    async def search_items(query: str, limit: int = 20) -> dict[str, Any]:
        try:
            if limit < 1 or limit > 100:
                raise WorkbenchError("search limit must be between 1 and 100")
            items = await anyio.to_thread.run_sync(
                partial(provider().search, query, limit=limit)
            )
            return {"items": items, "count": len(items)}
        except _CORE_ERRORS as exc:
            raise ToolError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://workspace",
        name="workbench_workspace",
        description="Workspace metadata and canonical Markdown overview.",
        mime_type="application/json",
    )
    async def workspace() -> str:
        data = await anyio.to_thread.run_sync(provider().get_workspace)
        return json.dumps(data, ensure_ascii=False, indent=2)

    @server.resource(
        "workbench://projects",
        name="workbench_projects",
        description="All projects in the shared Workbench repository.",
        mime_type="application/json",
    )
    async def projects() -> str:
        data = await anyio.to_thread.run_sync(provider().list_projects)
        return json.dumps({"projects": data}, ensure_ascii=False, indent=2)

    @server.resource(
        "workbench://projects/{project_slug}",
        name="workbench_project",
        description="Project metadata from its canonical Markdown file.",
        mime_type="application/json",
    )
    async def project(project_slug: str) -> str:
        try:
            data = await anyio.to_thread.run_sync(
                partial(provider().get_project, project_slug)
            )
            return json.dumps(data, ensure_ascii=False, indent=2)
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://projects/{project_slug}/backlog",
        name="workbench_project_backlog",
        description="Project backlog ordered by the derived index.",
        mime_type="application/json",
    )
    async def project_backlog(project_slug: str) -> str:
        try:
            data = await anyio.to_thread.run_sync(
                partial(provider().get_backlog, project_slug)
            )
            return json.dumps({"items": data}, ensure_ascii=False, indent=2)
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://projects/{project_slug}/decisions",
        name="workbench_project_decisions",
        description="All non-archived decisions for one project.",
        mime_type="application/json",
    )
    async def project_decisions(project_slug: str) -> str:
        try:
            data = await anyio.to_thread.run_sync(
                partial(
                    provider().list_items,
                    project=project_slug,
                    item_type="decision",
                    include_archived=False,
                )
            )
            return json.dumps({"decisions": data}, ensure_ascii=False, indent=2)
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://projects/{project_slug}/context",
        name="workbench_project_context",
        description="Deterministic structured context for one project.",
        mime_type="application/json",
    )
    async def project_context(project_slug: str) -> str:
        try:
            data = await anyio.to_thread.run_sync(
                partial(provider().get_project_context, project_slug, format="json")
            )
            return json.dumps(data, ensure_ascii=False, indent=2)
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://projects/{project_slug}/context.md",
        name="workbench_project_context_markdown",
        description="Prompt-ready Markdown context for one project.",
        mime_type="text/markdown",
    )
    async def project_context_markdown(project_slug: str) -> str:
        try:
            return await anyio.to_thread.run_sync(
                partial(provider().get_project_context, project_slug, format="markdown")
            )
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc

    @server.resource(
        "workbench://items/{item_id}",
        name="workbench_item",
        description="One item with metadata, revision, and Markdown body.",
        mime_type="text/markdown",
    )
    async def item(item_id: str) -> str:
        try:
            result = await anyio.to_thread.run_sync(
                partial(provider().get_item, item_id)
            )
        except _CORE_ERRORS as exc:
            raise ResourceError(_error_payload(exc)) from exc
        data = result.data
        return (
            f"# {data['key']} — {data['title']}\n\n"
            f"- Type: `{data['type']}`\n"
            f"- Status: `{data['status']}`\n"
            f"- Item revision: `{result.item_revision}`\n"
            f"- Repository revision: `{result.repository_revision}`\n"
            f"{result.body}"
        )

    return server


def mcp_http_app(server: FastMCP) -> ASGIApp:
    app: ASGIApp = BearerAuthMiddleware(server.streamable_http_app())
    origins = _environment_list("WORKBENCH_ALLOWED_ORIGINS")
    if origins:
        app = CORSMiddleware(
            app,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
            ],
            expose_headers=["Mcp-Session-Id"],
        )
    return app


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            api_key = os.getenv("WORKBENCH_API_KEY")
            if api_key:
                headers = {
                    key.lower(): value for key, value in scope.get("headers", [])
                }
                supplied = headers.get(b"authorization", b"").decode("latin-1")
                if not hmac.compare_digest(supplied, f"Bearer {api_key}"):
                    response = JSONResponse(
                        {
                            "error": {
                                "code": "unauthorized",
                                "message": "invalid API key",
                            }
                        },
                        status_code=401,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


_CORE_ERRORS = (
    WorkbenchError,
    TransitionNotAllowed,
    DuplicateRequest,
    RepositoryError,
    ValueError,
)


def _error_payload(exc: Exception) -> str:
    code = "workbench_error"
    name = type(exc).__name__
    if name == "RevisionConflict":
        code = "revision_conflict"
    elif isinstance(exc, TransitionNotAllowed):
        code = "transition_not_allowed"
    elif isinstance(exc, DuplicateRequest):
        code = "duplicate_request"
    elif isinstance(exc, RepositoryError):
        code = "repository_error"
    elif name.endswith("NotFound"):
        code = "item_not_found" if name.startswith("Item") else "project_not_found"
    return json.dumps({"code": code, "message": str(exc)}, ensure_ascii=False)


def _transport_security() -> TransportSecuritySettings:
    local_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    local_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=local_hosts + _environment_list("WORKBENCH_ALLOWED_HOSTS"),
        allowed_origins=local_origins + _environment_list("WORKBENCH_ALLOWED_ORIGINS"),
    )


def _environment_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [part.strip() for part in value.split(",") if part.strip()]
