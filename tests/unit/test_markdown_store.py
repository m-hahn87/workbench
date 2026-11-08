from __future__ import annotations

from workbench.repository.markdown_store import MarkdownStore, extract_description


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


def test_extract_description_returns_exact_description_across_templates_and_comments() -> None:
    desc = "Exakte Beschreibung.\n\n## Unterabschnitt\n- Detail A"
    task_body = (
        f"\n## Beschreibung\n\n{desc}\n\n"
        "## Akzeptanzkriterien\n\n- [ ] Noch festzulegen\n"
    )
    bug_body = (
        f"\n## Beschreibung\n\n{desc}\n\n"
        "## Schritte zur Reproduktion\n\n1. Noch festzulegen\n\n"
        "## Erwartetes Verhalten\n\nNoch festzulegen.\n\n"
        "## Tatsächliches Verhalten\n\nNoch festzulegen.\n\n"
        "## Umgebung\n\nNoch festzulegen.\n"
    )
    commented_body = (
        task_body.rstrip()
        + "\n\n## Kommentare\n\n### agent — 2026-11-08T10:00:00Z\n\nNotiz\n"
    )
    raw_patched_body = "\nDirekt via PATCH gesetzter Body\n"

    assert extract_description(task_body) == desc
    assert extract_description(bug_body) == desc
    assert extract_description(commented_body) == desc
    assert extract_description(raw_patched_body) == "Direkt via PATCH gesetzter Body"

