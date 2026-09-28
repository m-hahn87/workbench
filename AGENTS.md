# AGENTS.md — Workbench

Guidance for AI coding agents (Claude Code, Codex, Cursor, Hermes, Copilot) working in this repository.

## What this is

Workbench is a self-hosted, **Git-native, Markdown-first project context store** for humans and AI agents — an alternative to SaaS issue trackers (Linear/Jira) that you fully own. Markdown files with typed YAML frontmatter are the canonical store; SQLite (FTS5) and derived indexes are rebuildable caches. See `workbench-konzept-v4.md` for the full design rationale.

## Quick orientation

```
src/workbench/
├── core/           # service layer, context builder, file watcher
├── domain/         # item models, status machines, relations, validation
├── repository/     # git transaction store + SQLite read models
├── adapters/
│   ├── cli/        # `workbench` CLI (init, item, project, serve, doctor…)
│   ├── rest/       # versioned REST API (FastAPI) — /api/v1
│   ├── mcp/        # MCP Streamable HTTP server
│   └── web/        # read-only web UI (Jinja templates)
└── index/          # FTS5 indexing
```

- Python 3.12+, `uv` for dependency management
- Markdown canonical; **SQLite is derived — never edit DBs by hand**, rebuild via `workbench reindex`
- All writes go through the service layer (git transaction + idempotency) — no direct file writes in agent code

## Commands

```bash
uv sync --extra dev            # install deps
uv run pytest                  # full test suite (unit + integration)
uv run pytest tests/unit -q    # fast loop
uv run workbench doctor --root ./demo-data   # health check
uv run workbench validate --root ./demo-data # frontmatter validation report
```

Docker: `docker compose up -d` (serves REST + web UI; data under the mounted `--root`).

## Conventions

- **Item types** (`task`, `bug`, `feature`, `idea`, `decision`) each have their **own status machine** — check `src/workbench/domain/transitions.py` before wiring transitions. Most have no way back (e.g. `in-progress → ready` is invalid); when stuck, use the terminal states or create a replacement item.
- API quirk: comment payloads use the field **`text`** (not `body`); single-item GET returns `{"data": {…}}` wrapped.
- REST transitions: `POST /api/v1/items/{KEY}/transitions` with `{"status", "actor", "reason"}`.
- Tests: pytest, `tests/unit` + `tests/integration`. New features need tests — the suite is the merge gate.
- Style: standard Python (PEP 8), type hints in public APIs, `ruff`-compatible formatting.
- Runtime data (`db/`, `repo/` workspace data) is gitignored — **never commit instance data**.

## Working on this repo

1. **Read before writing**: check `workbench-konzept-v4.md` and the relevant `domain/` code — the status machines and relation rules are deliberate, not accidental.
2. **Changes to domain logic require tests** in `tests/` demonstrating the new/changed behavior.
3. **Commit messages**: Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`).
4. Run `uv run pytest` before pushing. CI (if enabled) runs the same suite.

## Using Workbench from an agent

If you're an agent that wants to *use* a running Workbench instance (not hack on this repo):

- REST: `http://<host>:8500/api/v1` (OpenAPI docs at `/docs`)
- MCP: Streamable HTTP at `http://<host>:8500/mcp` — tools like `workbench_claim_next_task`, `workbench_capture_idea`, `workbench_plan_feature`, `workbench_complete_task`
- Web UI: read-only overview at `/ui/`
- Optional Bearer auth via env (`WORKBENCH_API_KEY`)
- See `README.md` for the CLI quick start.
