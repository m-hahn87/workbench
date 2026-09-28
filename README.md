# Workbench

Workbench is a self-hosted, Git-native and Markdown-first project context store for humans and
AI agents. This repository implements the single-user MVP described in
[`workbench-konzept-v4.md`](workbench-konzept-v4.md).

Markdown with typed YAML frontmatter is canonical. Git provides the audit trail; SQLite and FTS5
are derived indexes that can be deleted and rebuilt at any time.

## MVP capabilities

- Workspaces and multiple projects with UUIDv7 identities and readable keys.
- Typed `task`, `bug`, `feature`, `idea`, and `decision` items with separate lifecycles.
- Canonical, validated relations with inverse views and cycle protection for `parent_of`.
- Optimistic item revisions, persistent request idempotency, and serialized Git transactions.
- Full-text search, activity derived from Git, deterministic project context, agent handoff, and
  atomic task claiming.
- Agent workflows for idea capture, structured decisions, feature planning, task completion, and
  project handoff. Feature planning writes the feature, task breakdown, relations, and optional
  decision in one Git transaction.
- Direct Markdown editing with a file watcher, validation reporting, and automatic reindexing.
- CLI, versioned REST API, MCP Streamable HTTP, and a responsive read-only web UI.
- Optional Bearer authentication, structured JSON logs, readiness diagnostics, and Docker/Compose.

## Quick start

Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/) are recommended:

```shell
uv sync --extra dev
uv run workbench init ./demo-data --title "My Workspace" --key WB
uv run workbench project create "Merge Quest" --key MQ --root ./demo-data
uv run workbench item create --type task --title "Build merge mechanic" \
  --project merge-quest --actor human --root ./demo-data
uv run workbench serve --root ./demo-data
```

Then open `http://127.0.0.1:8500/ui/`. The REST API lives below `/api/v1`, and MCP is mounted at
`/mcp`.

Useful administration commands:

```shell
uv run workbench validate --root ./demo-data
uv run workbench reindex --root ./demo-data
uv run workbench doctor --root ./demo-data
uv run workbench search "merge" --root ./demo-data
uv run workbench context merge-quest --format markdown --root ./demo-data
uv run workbench activity --project merge-quest --root ./demo-data
uv run workbench claim-next merge-quest --actor agent-b --request-id claim-001 --root ./demo-data
```

Run `uv run workbench --help`, `project --help`, or `item --help` for the complete CLI. Item
mutations accept readable keys or technical IDs. Type-specific values use repeatable
`--field NAME=VALUE` options.

## Data and Git semantics

An initialized data repository contains:

```text
_workspace.md
projects/<slug>/_project.md
projects/<slug>/<KEY>-<TYPE>-<NNN>.md
workspace/<KEY>-<TYPE>-<NNN>.md
.workbench/config.yml
.workbench/state/              # derived and ignored by Git
```

The default `debounce` commit mode batches mutations in a long-running service. Short-lived CLI
commands flush before exiting. `immediate` creates one commit per operation; `manual` deliberately
leaves changes in the working tree. Every mutation supports an `actor`, optional `reason`, and
optional `request_id`. Existing-item mutations also accept `expected_revision`.

Direct edits to canonical Markdown files are detected by the watcher. Invalid files remain on disk,
are excluded from normal queries, and appear through `validate`, `/api/v1/validation-errors`, and
the validation page in the UI. `reindex` reconstructs the complete derived index.

## REST API

Start the service locally:

```shell
uv run workbench serve --root ./demo-data
```

Representative calls:

```shell
curl http://127.0.0.1:8500/health/ready
curl -X POST http://127.0.0.1:8500/api/v1/items \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $WORKBENCH_API_KEY" \
  -d '{"type":"task","title":"API task","project":"merge-quest","actor":"codex","request_id":"req-001"}'
curl -X POST http://127.0.0.1:8500/api/v1/workflows/plan-feature \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $WORKBENCH_API_KEY" \
  -d '{"title":"Release automation","project":"merge-quest","actor":"codex","tasks":["Build artifacts","Publish checksums"],"request_id":"req-002"}'
```

The OpenAPI document is available at `/openapi.json`, with interactive documentation at `/docs`.
Mutation responses include the item revision, repository revision, pending-commit state, warnings,
and request ID. Errors use stable codes and appropriate HTTP statuses.

## MCP

The same process exposes Streamable HTTP at `http://127.0.0.1:8500/mcp`. The MVP currently
registers 18 tools:

```text
workbench_create_item          workbench_capture_idea
workbench_plan_feature        workbench_record_decision
workbench_update_item          workbench_transition_item
workbench_archive_item         workbench_restore_item
workbench_change_item_type     workbench_assign_item
workbench_add_comment          workbench_link_items
workbench_unlink_items         workbench_claim_next_task
workbench_complete_task        workbench_get_related
workbench_prepare_handoff      workbench_search_items
```

Resources and templates:

```text
workbench://workspace
workbench://projects
workbench://projects/{project_slug}
workbench://projects/{project_slug}/backlog
workbench://projects/{project_slug}/decisions
workbench://projects/{project_slug}/context
workbench://projects/{project_slug}/context.md
workbench://items/{item_id}
```

The Python integration suite uses the official MCP client. A second compatibility smoke test uses
the official TypeScript client:

```shell
mkdir mcp-smoke
npm install --prefix mcp-smoke @modelcontextprotocol/client
cp scripts/mcp-typescript-smoke.mjs mcp-smoke/
node mcp-smoke/mcp-typescript-smoke.mjs http://127.0.0.1:8500/mcp
```

## Web UI

The read-only UI at `/ui/` provides the workspace dashboard, project backlog and board, item
details, full-text search, recent Git activity, and validation errors. Mutations remain explicit
through CLI, REST, MCP, or direct Markdown edits.

When `WORKBENCH_API_KEY` is set, `/ui/login` accepts the same key and creates an HttpOnly session
cookie without storing the plaintext key. Set `WORKBENCH_COOKIE_SECURE=1` behind HTTPS.

## Configuration and security

Environment variables override `.workbench/config.yml`:

| Variable | Purpose |
|---|---|
| `WORKBENCH_API_KEY` | Protect REST, MCP, and the UI with one Bearer/login key |
| `WORKBENCH_COMMIT_MODE` | `immediate`, `debounce`, or `manual` |
| `WORKBENCH_DEBOUNCE_SECONDS` | Commit debounce window |
| `WORKBENCH_GIT_AUTHOR_NAME` / `WORKBENCH_GIT_AUTHOR_EMAIL` | Git commit identity |
| `WORKBENCH_GIT_PUSH_AFTER_COMMIT` | Push after a successful commit |
| `WORKBENCH_GIT_REMOTE` | Remote used for optional pushes; default `origin` |
| `WORKBENCH_ALLOWED_HOSTS` | Additional comma-separated MCP host patterns |
| `WORKBENCH_ALLOWED_ORIGINS` | Additional comma-separated browser origins |
| `WORKBENCH_COOKIE_SECURE` | Mark the UI session cookie secure when set to `1` |

Health endpoints are intentionally public. Bind to localhost unless a trusted reverse proxy and
API key are configured. The MVP is single-user and single-writer: several agents may share one
service, but multiple independent Workbench writer processes against the same repository are not a
supported deployment topology. Automatic Git pull and merge resolution are outside the MVP.

## Using Workbench with AI coding agents

This repo ships `AGENTS.md` and `CLAUDE.md` — agent guidance (repo layout, commands, domain rules
like the per-type status machines). Point any coding agent (Claude Code, Codex, Cursor, …) at the
repository and it picks them up automatically.

For agents *using* a running instance, Workbench exposes:

- **MCP** (Streamable HTTP, `/mcp`) — tools like `workbench_claim_next_task`, `workbench_capture_idea`, `workbench_plan_feature`, `workbench_complete_task`
- **REST API** (`/api/v1`, OpenAPI at `/docs`) — full CRUD, transitions, search
- **Web UI** (read-only overview at `/ui/`)

## Docker

Initialize the mounted repository once, then start the service:

```shell
docker compose run --rm workbench init /data/repo --title "My Workspace" --key WB
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8500/health/ready
```

Compose binds port 8500 to localhost. Canonical Markdown and Git data live below `./repo`; the
derived SQLite and idempotency databases live below `./db`. The image includes a healthcheck and
the service uses structured JSON logging.

## Development and verification

```shell
uv sync --extra dev
uv run pytest
uv run pytest --cov=workbench --cov-report=term-missing
docker compose config --quiet
docker build -t workbench-mvp:test .
```

The integration suite covers Markdown/Git/index round trips, every MVP item lifecycle, relations,
revision conflicts, idempotent retries, direct-edit watching, database rebuild, CLI, REST, MCP,
authentication, context/handoff, and the read-only UI.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
