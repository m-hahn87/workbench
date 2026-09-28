from __future__ import annotations

from workbench.repository.markdown_store import MarkdownStore


FIRST = """---
schema_version: 1
id: 01983f2a-8c71-7b3e-9f2d-5a6b4c3d2e1f
key: MQ-TASK-001
type: task
title: "Core mechanic" # keep this comment
scope: project
project: merge-quest
status: backlog
created: 2026-07-18T14:20:00Z
updated: 2026-07-18T14:20:00Z
created_by: matze
updated_by: matze
tags: [prototype, flame-engine]
relations: []
priority: medium
---

## Beschreibung

Implement the core mechanic.
"""


SECOND = """---
updated_by: matze
priority: medium
relations: []
tags:
  - prototype
  - flame-engine
created_by: matze
updated: "2026-07-18T14:20:00Z"
created: "2026-07-18T14:20:00Z"
status: backlog
project: merge-quest
scope: project
title: Core mechanic
type: task
key: MQ-TASK-001
id: 01983f2a-8c71-7b3e-9f2d-5a6b4c3d2e1f
schema_version: 1
---
## Beschreibung

Implement the core mechanic.
"""


def test_round_trip_preserves_yaml_comment_and_body() -> None:
    store = MarkdownStore()
    document = store.parse(FIRST)

    rendered = store.render(document)

    assert "# keep this comment" in rendered
    assert "Implement the core mechanic." in rendered
    assert store.item_hash(store.parse(rendered)) == store.item_hash(document)


def test_content_hash_ignores_semantic_formatting_differences() -> None:
    store = MarkdownStore()
    first = store.parse(FIRST)
    second = store.parse(SECOND)

    assert store.item_hash(first) == store.item_hash(second)
