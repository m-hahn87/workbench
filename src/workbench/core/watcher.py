from __future__ import annotations

import logging
from pathlib import Path

import anyio
from watchfiles import Change, awatch

from workbench.core.service import Workbench


logger = logging.getLogger("workbench.watcher")


def canonical_markdown_filter(_change: Change, path: str) -> bool:
    candidate = Path(path)
    return candidate.suffix.lower() == ".md" and not any(
        part in {".git", ".workbench"} for part in candidate.parts
    )


async def watch_repository(workbench: Workbench) -> None:
    """Reconcile direct Markdown edits while respecting the repository writer lock."""
    workbench.watcher_running = True
    workbench.watcher_error = None
    try:
        async for changes in awatch(
            workbench.root,
            watch_filter=canonical_markdown_filter,
            debounce=250,
            step=50,
        ):
            try:
                result = await anyio.to_thread.run_sync(workbench.reindex)
                workbench.watcher_events += len(changes)
                workbench.watcher_error = None
                logger.info(
                    "repository reconciled after direct Markdown change",
                    extra={
                        "changed_paths": len(changes),
                        "indexed_items": result.items,
                        "validation_errors": result.errors,
                    },
                )
            except (
                Exception
            ) as exc:  # keep the watcher alive and surface the failure via doctor
                workbench.watcher_error = str(exc)
                logger.exception("repository watcher reconciliation failed")
    finally:
        workbench.watcher_running = False
