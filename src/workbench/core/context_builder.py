from __future__ import annotations

from collections import Counter
from typing import Any


ACTIVE_STATUSES = {
    "ready",
    "triaged",
    "planned",
    "in-progress",
    "blocked",
    "review",
    "evaluating",
    "accepted",
}
BACKLOG_STATUSES = {"backlog", "reported", "proposed", "inbox"}
COMPLETED_STATUSES = {
    "done",
    "fixed",
    "closed",
    "delivered",
    "cancelled",
    "promoted",
    "superseded",
    "rejected",
}


def build_project_context(
    project: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    repository_revision: str,
    max_items: int = 50,
    relations: list[dict[str, Any]] | None = None,
    recent_activity: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered = sorted(items, key=_priority_key)
    compact = [_compact_item(item) for item in ordered]
    active = [item for item in compact if item["status"] in ACTIVE_STATUSES]
    blockers = [item for item in compact if item["status"] == "blocked"]
    backlog = [item for item in compact if item["status"] in BACKLOG_STATUSES]
    completed = [item for item in compact if item["status"] in COMPLETED_STATUSES]
    decisions = [
        item
        for item in compact
        if item["type"] == "decision" and item["status"] in {"proposed", "accepted"}
    ]
    risks = [
        item
        for item in compact
        if item["status"] == "blocked"
        or (item["type"] == "bug" and item.get("priority") in {"urgent", "high"})
    ]
    statuses = Counter(str(item["status"]) for item in items)
    types = Counter(str(item["type"]) for item in items)

    return {
        "project": {
            "id": project["id"],
            "key": project["key"],
            "slug": project["slug"],
            "title": project["title"],
            "status": project["status"],
        },
        "summary": {
            "total_items": len(items),
            "by_status": dict(sorted(statuses.items())),
            "by_type": dict(sorted(types.items())),
        },
        "active_work": active[:max_items],
        "blockers": blockers[:max_items],
        "backlog": backlog[:max_items],
        "recently_completed": completed[:max_items],
        "decisions": decisions[:max_items],
        "relations": (relations or [])[:max_items],
        "recent_activity": (recent_activity or [])[:max_items],
        "risks": risks[:max_items],
        "suggested_next_tasks": (active + backlog)[: min(max_items, 10)],
        "repository_revision": repository_revision,
    }


def render_project_context_markdown(context: dict[str, Any]) -> str:
    project = context["project"]
    summary = context["summary"]
    lines = [
        f"# {project['key']} — {project['title']}",
        "",
        f"Status: {project['status']}",
        f"Repository revision: `{context['repository_revision']}`",
        "",
        "## Summary",
        "",
        f"- Total items: {summary['total_items']}",
        f"- By status: {_format_counts(summary['by_status'])}",
        f"- By type: {_format_counts(summary['by_type'])}",
    ]
    sections = (
        ("Active work", "active_work"),
        ("Blockers", "blockers"),
        ("Backlog", "backlog"),
        ("Decisions", "decisions"),
        ("Risks", "risks"),
        ("Suggested next tasks", "suggested_next_tasks"),
    )
    for title, key in sections:
        if key in context:
            _append_items(lines, title, context[key])
    if "recent_activity" in context:
        _append_activity(lines, context["recent_activity"])
    return "\n".join(lines).rstrip() + "\n"


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "key": item["key"],
        "type": item["type"],
        "title": item["title"],
        "status": item["status"],
        "priority": item.get("priority"),
        "assignee": item.get("assignee"),
        "updated_at": item["updated_at"],
    }


def _priority_key(item: dict[str, Any]) -> tuple[int, str, str]:
    status_order = {
        "blocked": 0,
        "in-progress": 1,
        "review": 2,
        "ready": 3,
        "triaged": 3,
        "planned": 3,
        "evaluating": 4,
        "accepted": 5,
        "backlog": 6,
        "reported": 6,
        "proposed": 6,
        "inbox": 7,
    }
    priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    return (
        status_order.get(str(item["status"]), 99) * 10
        + priority_order.get(str(item.get("priority")), 9),
        str(item.get("rank") or ""),
        str(item["key"]),
    )


def _append_items(lines: list[str], title: str, items: list[dict[str, Any]]) -> None:
    lines.extend(["", f"## {title}", ""])
    if not items:
        lines.append("None.")
        return
    for item in items:
        details = [item["type"], item["status"]]
        if item.get("priority"):
            details.append(item["priority"])
        if item.get("assignee"):
            details.append(f"assigned to {item['assignee']}")
        lines.append(f"- **{item['key']}** {item['title']} ({', '.join(details)})")


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def _append_activity(lines: list[str], activity: list[dict[str, Any]]) -> None:
    lines.extend(["", "## Recent activity", ""])
    if not activity:
        lines.append("None.")
        return
    for entry in activity:
        actor = entry.get("actor") or entry.get("author") or "unknown"
        lines.append(
            f"- `{str(entry['commit'])[:8]}` {entry['subject']} "
            f"({actor}, {entry['timestamp']})"
        )
