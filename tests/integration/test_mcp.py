from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from workbench.adapters.rest.app import create_app
from workbench.core.service import Workbench


def test_streamable_http_tools_and_resources_with_official_client(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    workbench = Workbench.initialize(root, commit_mode="immediate")
    workbench.create_project("Merge Quest", key="MQ", actor="matze")

    with running_server(root) as base_url:
        result = asyncio.run(exercise_mcp(f"{base_url}/mcp"))

    assert result["created_key"] == "MQ-TASK-001"
    assert result["transitioned_status"] == "ready"
    assert result["search_count"] == 1
    assert result["related_type"] == "decided_by"
    assert result["conflict_is_error"] is True
    assert "MQ-TASK-001" in result["item_resource"]
    assert "Merge Quest" in result["context_resource"]


def test_streamable_http_uses_the_shared_bearer_key(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "data"
    Workbench.initialize(root, commit_mode="manual")
    monkeypatch.setenv("WORKBENCH_API_KEY", "mcp-secret")

    with running_server(root) as base_url:
        url = f"{base_url}/mcp"
        unauthorized = httpx.post(url, json={}, follow_redirects=True)
        assert unauthorized.status_code == 401
        tool_names = asyncio.run(list_authorized_tools(url, "mcp-secret"))

    assert "workbench_create_item" in tool_names


async def exercise_mcp(url: str) -> dict[str, object]:
    async with streamable_http_client(url) as (read_stream, write_stream, _session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert {
                "workbench_create_item",
                "workbench_transition_item",
                "workbench_archive_item",
                "workbench_search_items",
                "workbench_update_item",
                "workbench_restore_item",
                "workbench_change_item_type",
                "workbench_link_items",
                "workbench_unlink_items",
                "workbench_claim_next_task",
                "workbench_prepare_handoff",
                "workbench_capture_idea",
                "workbench_plan_feature",
                "workbench_record_decision",
            } <= tool_names
            search_tool = next(
                tool for tool in tools.tools if tool.name == "workbench_search_items"
            )
            assert search_tool.annotations is not None
            assert search_tool.annotations.readOnlyHint is True

            resources = await session.list_resources()
            assert any(
                str(resource.uri) == "workbench://projects"
                for resource in resources.resources
            )
            assert any(
                str(resource.uri) == "workbench://workspace"
                for resource in resources.resources
            )
            templates = await session.list_resource_templates()
            template_uris = {
                str(template.uriTemplate) for template in templates.resourceTemplates
            }
            assert "workbench://projects/{project_slug}/context" in template_uris
            assert "workbench://items/{item_id}" in template_uris
            assert "workbench://projects/{project_slug}/backlog" in template_uris
            assert "workbench://projects/{project_slug}/decisions" in template_uris

            created = await session.call_tool(
                "workbench_create_item",
                {
                    "item_type": "task",
                    "title": "MCP integration task",
                    "project": "merge-quest",
                    "actor": "codex",
                    "request_id": "req-mcp-create",
                },
            )
            assert created.isError is False
            assert created.structuredContent is not None
            created_data = created.structuredContent

            transitioned = await session.call_tool(
                "workbench_transition_item",
                {
                    "item_id": "MQ-TASK-001",
                    "status": "ready",
                    "actor": "codex",
                    "expected_revision": created_data["item_revision"],
                    "request_id": "req-mcp-transition",
                },
            )
            assert transitioned.isError is False
            assert transitioned.structuredContent is not None

            decision = await session.call_tool(
                "workbench_create_item",
                {
                    "item_type": "decision",
                    "title": "Use MCP Streamable HTTP",
                    "project": "merge-quest",
                    "actor": "matze",
                    "request_id": "req-mcp-decision",
                },
            )
            assert decision.structuredContent is not None
            linked = await session.call_tool(
                "workbench_link_items",
                {
                    "source_id": "MQ-TASK-001",
                    "target_id": "MQ-DEC-001",
                    "relation_type": "decided_by",
                    "actor": "codex",
                    "request_id": "req-mcp-link",
                    "expected_revision": transitioned.structuredContent[
                        "item_revision"
                    ],
                },
            )
            assert linked.isError is False
            related = await session.call_tool(
                "workbench_get_related", {"item_id": "MQ-TASK-001"}
            )
            assert related.structuredContent is not None

            conflict = await session.call_tool(
                "workbench_transition_item",
                {
                    "item_id": "MQ-TASK-001",
                    "status": "in-progress",
                    "actor": "other-agent",
                    "expected_revision": created_data["item_revision"],
                    "request_id": "req-mcp-conflict",
                },
            )

            search = await session.call_tool(
                "workbench_search_items", {"query": "integration"}
            )
            assert search.structuredContent is not None
            item_resource = await session.read_resource(
                AnyUrl("workbench://items/MQ-TASK-001")
            )
            context_resource = await session.read_resource(
                AnyUrl("workbench://projects/merge-quest/context")
            )
            return {
                "created_key": created_data["data"]["key"],
                "transitioned_status": transitioned.structuredContent["data"]["status"],
                "search_count": search.structuredContent["count"],
                "related_type": related.structuredContent["relations"][0][
                    "relation_type"
                ],
                "conflict_is_error": conflict.isError,
                "item_resource": item_resource.contents[0].text,
                "context_resource": json.loads(context_resource.contents[0].text)[
                    "project"
                ]["title"],
            }


async def list_authorized_tools(url: str, api_key: str) -> set[str]:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"},
        follow_redirects=True,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                return {tool.name for tool in tools.tools}


@contextmanager
def running_server(root: Path) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(root),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("Uvicorn stopped before startup completed")
        if time.monotonic() >= deadline:
            raise TimeoutError("Uvicorn did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise TimeoutError("Uvicorn did not stop")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])
