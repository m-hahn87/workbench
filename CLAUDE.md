# CLAUDE.md

> Same content as AGENTS.md — this file makes Claude Code pick it up automatically.

Workbench is a self-hosted, **Git-native, Markdown-first project context store** for humans and AI agents. Markdown + YAML frontmatter is canonical; SQLite is a derived, rebuildable index.

## Commands

```bash
uv sync --extra dev            # install deps
uv run pytest                  # full suite (merge gate)
uv run pytest tests/unit -q    # fast loop
uv run workbench doctor --root ./demo-data
```

## Layout

- `src/workbench/domain/` — item models, **status machines** (each item type has its own; check `transitions.py` first), relations
- `src/workbench/repository/` — git transaction store + SQLite read models
- `src/workbench/adapters/` — `cli/`, `rest/` (FastAPI, `/api/v1`), `mcp/` (Streamable HTTP), `web/` (read-only UI)
- `src/workbench/core/` — service layer, context builder, watcher

## Rules

1. All writes go through the service layer — never write item files or DBs directly.
2. Domain changes (status machines, validation, relations) require tests in `tests/`.
3. Status machines are one-way mostly — read `transitions.py`, don't guess.
4. API quirks: comment field is `text`; GET single item wraps in `{"data": …}`.
5. Conventional Commits. Run `uv run pytest` before pushing.
6. `db/` and `repo/` are instance data — gitignored, never commit.

## Full agent guide

See `AGENTS.md` (kept identical; both files exist so every agent tool picks one up).
