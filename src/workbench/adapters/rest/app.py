from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workbench.config import CommitMode
from workbench.adapters.mcp.server import create_mcp_server, mcp_http_app
from workbench.adapters.web.router import install_web_ui
from workbench.core.service import (
    ItemNotFound,
    ProjectNotFound,
    RevisionConflict,
    Workbench,
    WorkbenchError,
)
from workbench.core.watcher import watch_repository
from workbench.domain.transitions import TransitionNotAllowed
from workbench.index.indexer import repository_revision
from workbench.repository.git_transactions import RepositoryError
from workbench.state.idempotency import DuplicateRequest


class MutationMetadata(BaseModel):
    actor: str = Field(default="api", min_length=1)
    reason: str | None = None
    request_id: str | None = None


class CreateProjectRequest(MutationMetadata):
    title: str = Field(min_length=1)
    key: str = Field(min_length=2, max_length=8)
    slug: str | None = None


class CreateItemRequest(MutationMetadata):
    model_config = ConfigDict(populate_by_name=True)

    item_type: str = Field(alias="type")
    title: str = Field(min_length=1)
    project: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectRequest(MutationMetadata):
    title: str | None = None
    status: str | None = None


class ExistingItemMutation(MutationMetadata):
    expected_revision: str | None = None


class TransitionRequest(ExistingItemMutation):
    status: str


class UpdateItemRequest(ExistingItemMutation):
    fields: dict[str, Any] = Field(default_factory=dict)
    body: str | None = None


class TypeChangeRequest(ExistingItemMutation):
    target_type: str = Field(alias="type")
    fields: dict[str, Any] = Field(default_factory=dict)


class AssignmentRequest(ExistingItemMutation):
    assignee: str | None = None


class CommentRequest(ExistingItemMutation):
    text: str = Field(min_length=1)


class RelationRequest(ExistingItemMutation):
    source: str
    target: str
    relation_type: str = Field(alias="type")


class CaptureIdeaRequest(MutationMetadata):
    title: str = Field(min_length=1)
    description: str | None = None
    score: int = 0
    tags: list[str] = Field(default_factory=list)


class PlanFeatureRequest(MutationMetadata):
    title: str = Field(min_length=1)
    project: str
    description: str | None = None
    tasks: list[str] = Field(default_factory=list)
    decision_title: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class RecordDecisionRequest(MutationMetadata):
    title: str = Field(min_length=1)
    project: str
    context: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    alternatives: str | None = None
    consequences: str | None = None
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class ClaimNextRequest(MutationMetadata):
    project: str


class CompleteTaskRequest(ExistingItemMutation):
    item_id: str


class HandoffRequest(BaseModel):
    project: str
    from_actor: str = Field(alias="from")
    to_actor: str = Field(alias="to")
    assumptions: list[str] = Field(default_factory=list)
    recommended_next_step: str | None = None
    max_items: int = Field(default=50, ge=1, le=500)


def _workbench(request: Request) -> Workbench:
    return request.app.state.workbench


def _authorize(authorization: str | None = Header(default=None)) -> None:
    api_key = os.getenv("WORKBENCH_API_KEY")
    if not api_key:
        return
    expected = f"Bearer {api_key}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "invalid API key"},
        )


def _query_response(workbench: Workbench, data: object) -> dict[str, object]:
    return {
        "data": data,
        "repository_revision": repository_revision(workbench.root),
    }


def create_app(root: Path | str) -> FastAPI:
    repository_root = Path(root).resolve()
    mcp_server = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        workbench = Workbench(repository_root)
        app.state.workbench = workbench
        assert mcp_server is not None
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(watch_repository, workbench)
                yield
                task_group.cancel_scope.cancel()
        workbench.close(flush=workbench.config.git.commit_mode == CommitMode.DEBOUNCE)

    app = FastAPI(
        title="Workbench API",
        version="0.1.0",
        lifespan=lifespan,
    )
    mcp_server = create_mcp_server(lambda: app.state.workbench)
    app.state.mcp_server = mcp_server

    @app.exception_handler(ItemNotFound)
    async def item_not_found(_request: Request, exc: ItemNotFound) -> JSONResponse:
        return _error(404, "item_not_found", str(exc))

    @app.exception_handler(ProjectNotFound)
    async def project_not_found(
        _request: Request, exc: ProjectNotFound
    ) -> JSONResponse:
        return _error(404, "project_not_found", str(exc))

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(
        _request: Request, exc: RevisionConflict
    ) -> JSONResponse:
        return _error(
            409,
            "revision_conflict",
            str(exc),
            {"expected_revision": exc.expected, "actual_revision": exc.actual},
        )

    @app.exception_handler(DuplicateRequest)
    async def duplicate_request(
        _request: Request, exc: DuplicateRequest
    ) -> JSONResponse:
        return _error(409, "duplicate_request", str(exc))

    @app.exception_handler(TransitionNotAllowed)
    async def transition_not_allowed(
        _request: Request, exc: TransitionNotAllowed
    ) -> JSONResponse:
        return _error(422, "transition_not_allowed", str(exc))

    @app.exception_handler(RepositoryError)
    async def repository_error(_request: Request, exc: RepositoryError) -> JSONResponse:
        return _error(500, "repository_error", str(exc))

    @app.exception_handler(WorkbenchError)
    async def workbench_error(_request: Request, exc: WorkbenchError) -> JSONResponse:
        return _error(422, "validation_error", str(exc))

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error(
        _request: Request, exc: ValidationError
    ) -> JSONResponse:
        return _error(422, "validation_error", str(exc))

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(workbench: Workbench = Depends(_workbench)) -> JSONResponse:
        diagnostics = workbench.doctor()
        healthy = (
            diagnostics["repository"] == "ok"
            and diagnostics["sqlite_integrity"] == "ok"
            and not diagnostics["degraded"]
        )
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ready" if healthy else "degraded",
                "checks": diagnostics,
            },
        )

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(_authorize)])

    @router.post("/projects", status_code=201)
    def create_project(
        payload: CreateProjectRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.create_project(
            payload.title,
            key=payload.key,
            slug=payload.slug,
            actor=payload.actor,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.get("/projects")
    def list_projects(workbench: Workbench = Depends(_workbench)) -> dict[str, object]:
        return _query_response(workbench, workbench.list_projects())

    @router.patch("/projects/{project_id}")
    def update_project(
        project_id: str,
        payload: UpdateProjectRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.update_project(
            project_id,
            actor=payload.actor,
            title=payload.title,
            status=payload.status,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.get("/projects/{project_id}")
    def get_project(
        project_id: str,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(workbench, workbench.get_project(project_id))

    @router.get("/projects/{project_id}/items")
    def list_project_items(
        project_id: str,
        item_type: str | None = Query(default=None, alias="type"),
        include_archived: bool = False,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        data = workbench.list_items(
            project=project_id,
            item_type=item_type,
            include_archived=include_archived,
        )
        return _query_response(workbench, data)

    @router.get("/projects/{project_id}/context")
    def project_context(
        project_id: str,
        format: str = Query(default="json", pattern="^(json|markdown)$"),
        max_items: int = Query(default=50, ge=1, le=500),
        include: list[str] | None = Query(default=None),
        since: str | None = Query(default=None),
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        data = workbench.get_project_context(
            project_id,
            format=format,
            max_items=max_items,
            include=include,
            since=since,
        )
        return _query_response(workbench, data)

    @router.get("/projects/{project_id}/backlog")
    def project_backlog(
        project_id: str,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(workbench, workbench.get_backlog(project_id))

    @router.post("/items", status_code=201)
    def create_item(
        payload: CreateItemRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.create_item(
            item_type=payload.item_type,
            title=payload.title,
            actor=payload.actor,
            project=payload.project,
            description=payload.description,
            tags=payload.tags,
            fields=payload.fields,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.get("/items/{item_id}")
    def get_item(
        item_id: str,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.get_item(item_id).as_dict()

    @router.patch("/items/{item_id}")
    def update_item(
        item_id: str,
        payload: UpdateItemRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.update_item(
            item_id,
            actor=payload.actor,
            changes=payload.fields,
            body=payload.body,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/type-change")
    def change_item_type(
        item_id: str,
        payload: TypeChangeRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.change_item_type(
            item_id,
            payload.target_type,
            actor=payload.actor,
            fields=payload.fields,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/assignment")
    def assign_item(
        item_id: str,
        payload: AssignmentRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.assign_item(
            item_id,
            payload.assignee,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/comments")
    def add_comment(
        item_id: str,
        payload: CommentRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.add_comment(
            item_id,
            payload.text,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/transitions")
    def transition_item(
        item_id: str,
        payload: TransitionRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.transition_item(
            item_id,
            payload.status,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/archive")
    def archive_item(
        item_id: str,
        payload: ExistingItemMutation,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.archive_item(
            item_id,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/items/{item_id}/restore")
    def restore_item(
        item_id: str,
        payload: ExistingItemMutation,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.restore_item(
            item_id,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.get("/items/{item_id}/related")
    def related_items(
        item_id: str,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(workbench, workbench.get_related(item_id))

    @router.post("/relations", status_code=201)
    def link_items(
        payload: RelationRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.link_items(
            payload.source,
            payload.target,
            payload.relation_type,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.delete("/relations")
    def unlink_items(
        payload: RelationRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.unlink_items(
            payload.source,
            payload.target,
            payload.relation_type,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.get("/search")
    def search(
        q: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(workbench, workbench.search(q, limit=limit))

    @router.get("/validation-errors")
    def validation_errors(
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(workbench, workbench.validation_errors())

    @router.get("/activity")
    def activity(
        project: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return _query_response(
            workbench, workbench.get_activity(project=project, limit=limit)
        )

    @router.post("/workflows/claim-next-task")
    def claim_next_task(
        payload: ClaimNextRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.claim_next_task(
            payload.project,
            actor=payload.actor,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/workflows/capture-idea", status_code=201)
    def capture_idea(
        payload: CaptureIdeaRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.capture_idea(
            payload.title,
            actor=payload.actor,
            description=payload.description,
            score=payload.score,
            tags=payload.tags,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/workflows/plan-feature", status_code=201)
    def plan_feature(
        payload: PlanFeatureRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.plan_feature(
            payload.title,
            project=payload.project,
            actor=payload.actor,
            description=payload.description,
            task_titles=payload.tasks,
            decision_title=payload.decision_title,
            fields=payload.fields,
            reason=payload.reason,
            request_id=payload.request_id,
        )

    @router.post("/workflows/record-decision", status_code=201)
    def record_decision(
        payload: RecordDecisionRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.record_decision(
            payload.title,
            project=payload.project,
            actor=payload.actor,
            context=payload.context,
            decision=payload.decision,
            alternatives=payload.alternatives,
            consequences=payload.consequences,
            tags=payload.tags,
            fields=payload.fields,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/workflows/complete-task")
    def complete_task(
        payload: CompleteTaskRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        return workbench.complete_task(
            payload.item_id,
            actor=payload.actor,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            request_id=payload.request_id,
        ).as_dict()

    @router.post("/workflows/prepare-project-handoff")
    def prepare_project_handoff(
        payload: HandoffRequest,
        workbench: Workbench = Depends(_workbench),
    ) -> dict[str, object]:
        data = workbench.prepare_project_handoff(
            payload.project,
            from_actor=payload.from_actor,
            to_actor=payload.to_actor,
            assumptions=payload.assumptions,
            recommended_next_step=payload.recommended_next_step,
            max_items=payload.max_items,
        )
        return _query_response(workbench, data)

    app.include_router(router)
    install_web_ui(app)
    app.mount("/mcp", mcp_http_app(mcp_server), name="mcp")
    return app


def _error(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})
