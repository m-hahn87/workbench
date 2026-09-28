from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from workbench.core.service import ItemNotFound, ProjectNotFound, Workbench


WEB_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_ROOT / "templates")
markdown = MarkdownIt(
    "commonmark", {"html": False, "linkify": False, "typographer": False}
)


def _status_label(value: str) -> str:
    return value.replace("-", " ").title()


def _format_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y · %H:%M")
    except ValueError:
        return value


templates.env.filters["status_label"] = _status_label
templates.env.filters["format_datetime"] = _format_datetime
templates.env.filters["short_revision"] = lambda value: str(value).removeprefix(
    "sha256:"
)[:10]


def install_web_ui(app: FastAPI) -> None:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/ui/") -> HTMLResponse:
        if _ui_authorized(request):
            return RedirectResponse(_safe_next(next), status_code=303)
        return _render(
            request, "login.html", title="Sign in", next=_safe_next(next), error=None
        )

    @router.post("/login", response_class=HTMLResponse)
    def login(
        request: Request,
        token: str = Form(...),
        next: str = Form("/ui/"),
    ) -> HTMLResponse:
        api_key = os.getenv("WORKBENCH_API_KEY", "")
        destination = _safe_next(next)
        if not api_key or hmac.compare_digest(token, api_key):
            response = RedirectResponse(destination, status_code=303)
            if api_key:
                response.set_cookie(
                    "workbench_ui_session",
                    _session_fingerprint(api_key),
                    httponly=True,
                    secure=os.getenv("WORKBENCH_COOKIE_SECURE", "").lower()
                    in {"1", "true", "yes"},
                    samesite="strict",
                    path="/ui",
                    max_age=60 * 60 * 12,
                )
            return response
        return _render(
            request,
            "login.html",
            title="Sign in",
            next=destination,
            error="The API key is not valid.",
            status_code=401,
        )

    @router.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/ui/login", status_code=303)
        response.delete_cookie("workbench_ui_session", path="/ui")
        return response

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        workbench = _workbench(request)
        projects = workbench.list_projects()
        items = workbench.list_items(include_archived=False)
        counts_by_project = Counter(
            str(item["project"]) for item in items if item["project"]
        )
        blockers_by_project = Counter(
            str(item["project"])
            for item in items
            if item["project"] and item["status"] == "blocked"
        )
        project_cards = [
            {
                **project,
                "item_count": counts_by_project.get(str(project["slug"]), 0),
                "blocker_count": blockers_by_project.get(str(project["slug"]), 0),
            }
            for project in projects
        ]
        workspace_items = [item for item in items if item["scope"] == "workspace"]
        recent = sorted(items, key=lambda item: str(item["updated_at"]), reverse=True)[
            :6
        ]
        return _render(
            request,
            "dashboard.html",
            title="Workspace",
            projects=project_cards,
            workspace_items=workspace_items[:6],
            recent=recent,
            metrics={
                "projects": len(projects),
                "active_items": sum(
                    item["status"]
                    in {
                        "in-progress",
                        "blocked",
                        "review",
                    }
                    for item in items
                ),
                "blockers": sum(item["status"] == "blocked" for item in items),
                "ideas": sum(item["type"] == "idea" for item in workspace_items),
            },
        )

    @router.get("/projects/{project_id}", response_class=HTMLResponse)
    def project(request: Request, project_id: str) -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        workbench = _workbench(request)
        try:
            project_data = workbench.get_project(project_id)
            items = workbench.list_items(project=project_id, include_archived=False)
            context = workbench.get_project_context(project_id, format="json")
        except ProjectNotFound:
            return _not_found(request, "Project not found", project_id)
        columns = _board_columns(items)
        return _render(
            request,
            "project.html",
            title=str(project_data["title"]),
            project=project_data,
            items=items,
            columns=columns,
            context=context,
        )

    @router.get("/items/{item_id}", response_class=HTMLResponse)
    def item(request: Request, item_id: str) -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        workbench = _workbench(request)
        try:
            result = workbench.get_item(item_id)
        except ItemNotFound:
            return _not_found(request, "Item not found", item_id)
        project = None
        if result.data.get("project"):
            try:
                project = workbench.get_project(str(result.data["project"]))
            except ProjectNotFound:
                project = None
        body_html = Markup(markdown.render(result.body))
        return _render(
            request,
            "item.html",
            title=f"{result.data['key']} · {result.data['title']}",
            item=result,
            data=result.data,
            project=project,
            body_html=body_html,
        )

    @router.get("/company", response_class=HTMLResponse)
    def company_dashboard(request: Request) -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        workbench = _workbench(request)
        items = workbench.list_items(include_archived=False)

        # --- Agenten-Loops (Kanban) aus SSOT-JSON lesen ---
        kanban: dict[str, Any] = {}
        kb_file = Path(os.getenv("WORKBENCH_ROOT", "/data/repo")) / "projects/hahn-cloud/KANBAN-STATUS.json"
        if kb_file.exists():
            try:
                kanban = json.loads(kb_file.read_text(encoding="utf-8"))
            except Exception:
                kanban = {}

        def _open(t: str, statuses: set[str]) -> list[dict[str, Any]]:
            return sorted(
                [i for i in items if i.get("type") == t and i.get("status") in statuses],
                key=lambda i: str(i.get("key", "")),
            )

        bugs_open = _open("bug", {"reported", "triaged"})
        bugs_review = _open("bug", {"review"})
        tasks_ready = _open("task", {"ready"})
        tasks_progress = _open("task", {"in-progress"})
        blocked = _open("task", {"blocked"}) + _open("bug", {"blocked"})
        review_items = bugs_review + _open("task", {"review"})

        pr_lines: list[str] = []
        pr_synced = ""
        pr_file = Path(os.getenv("WORKBENCH_ROOT", "/data/repo")) / "projects/hahn-cloud/PR-STATUS.md"
        if pr_file.exists():
            in_body = False
            for raw in pr_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("_Auto-generiert"):
                    pr_synced = line.strip("_")
                    continue
                if line.startswith("# "):
                    in_body = True
                    continue
                if in_body and (line.startswith("- #") or line.startswith("## ")):
                    pr_lines.append(line)
        recent = sorted(items, key=lambda item: str(item["updated_at"]), reverse=True)[:10]

        return _render(
            request,
            "company.html",
            title="Firma",
            kpis={
                "bugs_open": len(bugs_open),
                "review": len(review_items),
                "ready": len(tasks_ready),
                "in_progress": len(tasks_progress),
                "blocked": len(blocked),
            },
            queue={
                "review": review_items,
                "ready": tasks_ready,
                "in_progress": tasks_progress,
                "blocked": blocked,
                "bugs": bugs_open,
            },
            pr_lines=pr_lines,
            pr_synced=pr_synced,
            kanban=kanban,
            recent=recent,
        )

    @router.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "") -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        query = q.strip()
        results = _workbench(request).search(query, limit=50) if query else []
        return _render(
            request,
            "search.html",
            title="Search",
            query=query,
            results=results,
        )

    @router.get("/validation", response_class=HTMLResponse)
    def validation(request: Request) -> HTMLResponse:
        guard = _guard(request)
        if guard:
            return guard
        workbench = _workbench(request)
        result = workbench.reindex()
        errors = workbench.validation_errors()
        return _render(
            request,
            "validation.html",
            title="Validation",
            errors=errors,
            indexed_items=result.items,
        )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/", status_code=307)

    app.mount(
        "/ui/static",
        StaticFiles(directory=WEB_ROOT / "static"),
        name="ui-static",
    )
    app.include_router(router)


def _render(
    request: Request,
    name: str,
    *,
    title: str,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    workbench = getattr(request.app.state, "workbench", None)
    validation_count = len(workbench.validation_errors()) if workbench else 0
    return templates.TemplateResponse(
        request,
        name,
        {
            "title": title,
            "current_path": request.url.path,
            "auth_enabled": bool(os.getenv("WORKBENCH_API_KEY")),
            "validation_count": validation_count,
            **context,
        },
        status_code=status_code,
    )


def _not_found(request: Request, title: str, identifier: str) -> HTMLResponse:
    return _render(
        request,
        "error.html",
        title=title,
        message=f"No indexed object matches “{identifier}”.",
        status_code=404,
    )


def _workbench(request: Request) -> Workbench:
    return request.app.state.workbench


def _guard(request: Request) -> RedirectResponse | None:
    if _ui_authorized(request):
        return None
    destination = quote(
        request.url.path + (f"?{request.url.query}" if request.url.query else "")
    )
    return RedirectResponse(f"/ui/login?next={destination}", status_code=303)


def _ui_authorized(request: Request) -> bool:
    api_key = os.getenv("WORKBENCH_API_KEY", "")
    if not api_key:
        return True
    supplied = request.cookies.get("workbench_ui_session", "")
    return hmac.compare_digest(supplied, _session_fingerprint(api_key))


def _session_fingerprint(api_key: str) -> str:
    return hmac.new(
        api_key.encode("utf-8"), b"workbench-ui-session", hashlib.sha256
    ).hexdigest()


def _safe_next(value: str) -> str:
    return value if value.startswith("/ui/") and not value.startswith("//") else "/ui/"


def _board_columns(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = [
        ("backlog", "Backlog", {"backlog", "reported", "inbox", "proposed", "ready", "triaged", "evaluating", "accepted", "planned"}),
        ("active", "In progress", {"in-progress", "blocked"}),
        ("review", "Review", {"review"}),
        (
            "complete",
            "Completed",
            {
                "done",
                "fixed",
                "closed",
                "delivered",
                "promoted",
                "superseded",
                "rejected",
                "cancelled",
            },
        ),
    ]
    assigned: set[str] = set()
    columns: list[dict[str, Any]] = []
    for key, title, statuses in definitions:
        column_items = [item for item in items if item["status"] in statuses]
        assigned.update(str(item["id"]) for item in column_items)
        columns.append({"key": key, "title": title, "items": column_items})
    other = [item for item in items if str(item["id"]) not in assigned]
    if other:
        columns.append({"key": "other", "title": "Other", "items": other})
    return columns
