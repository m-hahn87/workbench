# Workbench — Konzept / RFC v0.3

> Ein offener, agentenübergreifender Project Context Store für KI-gestützte Wissensarbeit.
>
> Menschen und Agenten arbeiten gemeinsam an Tasks, Ideen, Konzepten und Entscheidungen —
> mit Markdown als kanonischem Zustand, Git als Historie und einer Capability-Schicht für Agenten.

| Feld | Wert |
|---|---|
| **Status** | Draft RFC |
| **Version** | 0.3 |
| **Stand** | 2026-07-18 |
| **Lizenz** | Noch festzulegen; Empfehlung: Apache-2.0 (siehe §30) |
| **MVP-Ziel** | Ein selbst gehosteter Single-User-Service, den Menschen und verschiedene Coding-Agenten gemeinsam nutzen können |

---

## 0. Änderungen gegenüber v0.2

Dies ist ein Review-Pass auf v0.2, kein Neuentwurf. Die Struktur und die meisten Entscheidungen
bleiben unverändert — v0.2 war bereits ungewöhnlich konsistent für ein erstes Konzeptdokument.
Ergänzt wurden vor allem Stellen, an denen mehrere gleichzeitige Agenten oder ein langlebiges
Repository das Design unter Druck setzen würden, plus zwei echte Inkonsistenzen und eine
aktuelle Protokoll-Entwicklung, die mehrere offene Fragen direkt betrifft.

**Neue Unterabschnitte:**

- §9.5 Promotion und Scope-Wechsel (Idea → Project)
- §9.6 Schema-Migration
- §11.5 Bezug zu MCP Tasks (Ausblick)
- §13.5 Protokollentwicklung — Ausblick
- §15.7 Konflikt-Recovery für Agenten
- §17.5 Content Hash
- §27.4 Metriken (Ausblick)

**Behobene Inkonsistenzen:**

- `add_comment` stand als primitiver Command in §11.1, während das Kommentar-Modell in §30
  noch als offene Frage gelistet war. Jetzt entschieden (ADR-013).
- Der Default Commit Mode war in §15.4 bereits als `debounce` empfohlen, in §30 aber weiterhin
  als offen geführt. Jetzt als ADR-011 festgehalten.

**Vormals "Noch offen", jetzt entschieden:** ID-Format, Kommentar-Modell, Project Context
Format, Remote Git, Legacy SSE, Archivierung — siehe ADR-011 bis ADR-017 in §30.

**Weiterhin offen, mit Empfehlung:** Name, Lizenz, öffentliche Distribution — siehe §30.

**Extern recherchiert:** Das MCP-Protokoll steht kurz vor einer größeren Revision
(Release Candidate für 2026-07-28), die mehrere Designentscheidungen dieses Dokuments direkt
berührt — Details in §13.5.

---

## 1. Zusammenfassung

Workbench ist kein weiterer klassischer Issue-Tracker. Es ist eine offene, dauerhafte
Arbeits- und Kontextbasis für Softwareprojekte, Produktideen und KI-Agenten.

Der kanonische Zustand liegt in menschenlesbaren Markdown-Dateien. Git liefert Historie,
Diffs, Wiederherstellung und optional Remote-Synchronisierung. Ein kleiner Workbench-Core
validiert Änderungen, verwaltet Beziehungen, erzeugt atomare Git-Transaktionen und baut
einen vollständig rekonstruierbaren SQLite-Index auf.

Menschen und Agenten greifen über unterschiedliche Adapter auf denselben Core zu:

- **MCP** für ChatGPT, Gemini CLI, Claude Code, Codex und weitere Agenten
- **REST** für eigene Integrationen und Automationen
- **CLI** für lokale Arbeit, Administration und CI
- **Web UI** für Kanban, Suche und Projektübersicht
- **Direkte Markdown-Bearbeitung** für maximale Offenheit und Portabilität

Der zentrale Produktgedanke lautet:

> Kontext gehört in das System und wird bei Bedarf abgerufen — nicht dauerhaft in Prompts kopiert.

---

## 2. Problem

Aktuell ist Projektwissen über viele Werkzeuge und Gesprächskontexte verteilt:

- **Tasks und Bugs** in Linear, Jira oder GitHub Issues
- **Konzepte und Spezifikationen** in Notion, Obsidian oder Google Docs
- **Entscheidungen** in Chat-Verläufen, Meetings oder Köpfen
- **Code-Kontext** in READMEs, Pull Requests oder gar nicht
- **Neue Produktideen** in losen Notizen ohne Projektzuordnung
- **Agentenfortschritt** in temporären Sessions einzelner KI-Systeme

Coding-Agenten haben dadurch keinen gemeinsamen, strukturierten Projektzustand. Jeder Agent
muss erneut mit Kontext versorgt werden. Übergaben zwischen ChatGPT, Gemini, Claude, Codex,
OpenCode oder lokalen Agenten sind manuell und fehleranfällig.

### Folgen

- Kontext geht zwischen Sessions und Modellen verloren.
- Mehrere Agenten arbeiten mit unterschiedlichen Wissensständen.
- Entscheidungen werden mehrfach diskutiert oder widersprüchlich umgesetzt.
- Aufgaben, Ideen und Dokumentation werden redundant gepflegt.
- Ein Wechsel des Tools oder Modells erzeugt Lock-in-Kosten.
- Der Mensch wird zum manuellen Synchronisationsdienst zwischen Agenten.

---

## 3. Vision

Workbench stellt einen gemeinsamen, versionierten Arbeitsgraphen bereit. Jedes relevante
Artefakt ist ein typisierter Node mit stabiler Identität, nachvollziehbarem Lebenszyklus und
gerichteten Beziehungen zu anderen Nodes.

Beispiele:

- Eine Idee wird bewertet und später in ein Feature überführt.
- Ein Feature wird in Tasks zerlegt.
- Eine Entscheidung begründet einen Task.
- Ein Bug blockiert einen Release.
- Ein Agent übernimmt einen Task und hinterlegt Fortschritt sowie Ergebnis.
- Ein neuer Agent ruft einen kompakten Projektkontext ab und kann ohne langen Handover weiterarbeiten.

### 3.1 Produktthese

Der wertvollste Teil von Workbench ist nicht das Kanban-Board, sondern die Kombination aus:

1. **offenem, portablem Projektzustand**,
2. **stabilen, typisierten Beziehungen**,
3. **agentengerechten Lese- und Schreiboperationen**,
4. **nachvollziehbaren Änderungen über Git**,
5. **modell- und clientunabhängigem Zugriff**.

### 3.2 Was Workbench nicht sein soll

Workbench ist im MVP ausdrücklich nicht:

- eine vollständige Jira- oder Linear-Kopie,
- eine allgemeine Notion-Alternative,
- ein autonomer Projektmanager mit eingebautem LLM,
- eine Microservice-Plattform,
- eine Multi-Tenant-SaaS,
- ein Ersatz für das Code-Repository,
- ein System, das nur über seine eigene API bedienbar ist.

---

## 4. Kernanwendungsfälle

### 4.1 Projektübergreifende Ideen-Inbox

Neue App-Ideen können ohne bestehendes Repository oder Projekt erfasst, bewertet und später
in ein Projekt überführt werden.

### 4.2 Gemeinsames Task- und Bug-Tracking

Menschen und Agenten lesen und verändern denselben Backlog. Arbeit ist nicht an ein bestimmtes
Code-Repository oder einen bestimmten KI-Anbieter gebunden.

### 4.3 Agenten-Handover

Ein Agent kann einen strukturierten Kontext abrufen:

- Projektziel und aktueller Stand
- aktive und blockierte Tasks
- offene Entscheidungen
- relevante Konzepte
- letzte Änderungen
- empfohlene nächste Arbeit

### 4.4 Entscheidungsnachvollziehbarkeit

Entscheidungen werden als eigene Artefakte gespeichert und mit Features, Tasks oder Konzepten
verknüpft. Später ist erkennbar, warum etwas so umgesetzt wurde.

### 4.5 Menschlicher Überblick

Eine einfache Web-Oberfläche zeigt projektbezogene Boards, offene Entscheidungen,
Blockaden und Aktivität, ohne dass Menschen Markdown-Dateien manuell durchsuchen müssen.

---

## 5. Design-Prinzipien

| Prinzip | Bedeutung |
|---|---|
| **Markdown-first** | Jedes fachliche Artefakt ist eine lesbare `.md`-Datei. Kein proprietärer Lock-in. |
| **Git-native** | Historie, Diffs, Wiederherstellung, Branches und Pull Requests bleiben nutzbar. |
| **Human-editable** | Direkte Änderungen an Markdown-Dateien sind ein unterstützter Weg, kein Hack. |
| **Derived Index** | SQLite ist ein rekonstruierbarer Cache, niemals die fachliche Source of Truth. |
| **Typed Nodes** | Tasks, Bugs, Features, Ideen, Entscheidungen und Konzepte besitzen eigene Schemas und Lebenszyklen. |
| **Canonical Relations** | Eine gerichtete Beziehung wird genau einmal gespeichert; inverse Beziehungen werden berechnet. |
| **Workspace + Project Scope** | Artefakte können zu einem Projekt oder zum gesamten Workspace gehören. |
| **Capability-oriented** | Primitive Commands werden durch höherwertige Workflows für Agenten ergänzt. |
| **Agent-neutral** | Kein Modell und kein Client ist architektonisch privilegiert. |
| **Deterministic Core** | Der Kern funktioniert ohne eingebautes LLM und verhält sich reproduzierbar. |
| **Thin Adapters** | REST, MCP, CLI und UI enthalten keine eigene Business-Logik. |
| **Secure by Default** | Remote-Zugriff ist authentifiziert; lokale Bindings sind standardmäßig restriktiv. |
| **One Service** | Core, Indexer, REST, MCP und minimale UI laufen als modularer Monolith. |

---

## 6. Architektur

```text
                         Git Repository
                 Markdown = kanonischer Zustand
                                │
                 ┌──────────────┴──────────────┐
                 │       Workbench Core        │
                 │                             │
                 │  Schema Registry            │
                 │  Command Service            │
                 │  Query Service              │
                 │  Workflow Service           │
                 │  Git Transaction Manager    │
                 │  Validator / Indexer         │
                 └──────────────┬──────────────┘
                                │
                       SQLite Derived Index
                                │
       ┌────────────────────────┼────────────────────────┐
       │                        │                        │
    REST API             MCP Server                   CLI
   /api/v1/*          /mcp + optional stdio      workbench ...
       │                        │                        │
       └───────────────┬────────┴────────┬───────────────┘
                       │                 │
                    Web UI          Agent Clients
                                  ChatGPT / Gemini /
                                  Claude / Codex / ...
```

### 6.1 Komponenten

#### Workbench Core

Der Core enthält die gesamte fachliche Logik:

- Schema- und Transition-Validierung
- ID- und Key-Erzeugung
- Commands und Queries
- höherwertige Workflows
- Relationsverwaltung
- Git-Transaktionen
- Nebenläufigkeitskontrolle
- Index-Aktualisierung

#### Git Repository

Das Git-Repository enthält den kanonischen fachlichen Zustand. Es bleibt auch ohne laufenden
Workbench-Service verständlich und bearbeitbar.

#### SQLite Index

SQLite enthält optimierte, abgeleitete Daten für:

- Volltextsuche
- Filter und Sortierung
- Graph-Abfragen
- Aktivitätsfeeds
- schnelle Projektkontexte
- Validierungsfehler

Der Index kann jederzeit vollständig gelöscht und aus Markdown neu aufgebaut werden.

#### Adapter

REST, MCP, CLI und Web UI übersetzen externe Aufrufe in Core-Commands und Core-Queries.
Sie dürfen keine voneinander abweichenden Geschäftsregeln implementieren.

### 6.2 Schreibfluss über API oder MCP

1. Client sendet einen Command oder Workflow-Aufruf.
2. Authentifizierung und Berechtigungsprüfung laufen im Adapter.
3. Core validiert Schema, Transition und erwartete Revision.
4. Core erzeugt oder verändert Markdown atomar.
5. Git Transaction Manager erstellt einen Commit oder fügt die Änderung einem Commit-Batch hinzu.
6. Indexer aktualisiert den SQLite-Index synchron für die betroffenen Dateien.
7. Client erhält Item, Commit-Revision und mögliche Warnungen zurück.

### 6.3 Lesefluss

1. Client sendet eine Query oder liest eine MCP Resource.
2. Query Service liest primär aus SQLite.
3. Für vollständige Inhalte oder Konsistenzprüfungen kann der Service direkt auf Markdown zugreifen.
4. Die Antwort enthält die zugrunde liegende Repository-Revision.

### 6.4 Direkte Markdown-Bearbeitung

1. Mensch oder lokaler Agent verändert eine Datei direkt.
2. File Watcher erkennt die Änderung.
3. Validator prüft Frontmatter, Body und Relations.
4. Gültige Dateien werden neu indexiert.
5. Ungültige Dateien bleiben im Repository, werden aber im Index als fehlerhaft markiert.
6. `workbench validate` zeigt verständliche Reparaturhinweise.

---

## 7. Repository- und Verzeichnisstruktur

```text
workbench-data/
├── _workspace.md
├── projects/
│   ├── merge-quest/
│   │   ├── _project.md
│   │   ├── MQ-TASK-001.md
│   │   ├── MQ-BUG-001.md
│   │   ├── MQ-FEAT-001.md
│   │   ├── MQ-DEC-001.md
│   │   └── MQ-CON-001.md
│   ├── gate-dash/
│   │   ├── _project.md
│   │   └── ...
│   └── kochkumpel/
│       ├── _project.md
│       └── ...
├── workspace/
│   ├── WB-IDEA-001.md
│   ├── WB-DEC-001.md
│   └── ...
├── templates/
│   ├── task.md
│   ├── bug.md
│   ├── feature.md
│   ├── idea.md
│   └── decision.md
└── .workbench/
    ├── config.yml
    └── schema-version
```

### Regeln

- Jedes Projekt besitzt eine eigene `_project.md`.
- Workspace-weite Artefakte liegen unter `workspace/`.
- Fachliche Dateien dürfen verschoben werden; die technische ID bleibt stabil.
- Dateinamen verwenden den lesbaren Key, nicht die technische ID.
- `.workbench/config.yml` enthält Einstellungen, aber keinen zentralen ID-Counter.
- Generierte Datenbanken, Logs und Secrets gehören nicht in das Daten-Repository.

---

## 8. Identität und Schlüssel

Ein einfacher globaler Zähler erzeugt bei parallelen Agenten, Branches und Imports unnötige
Konflikte. Deshalb besitzt jedes Artefakt zwei Identifikatoren.

### 8.1 Technische ID

```yaml
id: 01K0F6PS8RZ15PWK1Z3Q0A0V5M
```

- global eindeutig
- nicht veränderbar
- ULID oder UUIDv7
- wird für interne Referenzen und API-Aufrufe verwendet

### 8.2 Lesbarer Key

```yaml
key: MQ-TASK-001
```

- gut lesbar für Menschen
- enthält den Projektpräfix und Dokumenttyp
- darf innerhalb eines kontrollierten Rename-Workflows geändert werden
- wird im Dateinamen verwendet

Workspace-Artefakte erhalten einen Workspace-Präfix, beispielsweise `WB-IDEA-001`.

### 8.3 Projektpräfix

```yaml
# projects/merge-quest/_project.md
---
id: 01K0F5YJ13YVVFHAGADK9F3TXA
key: MQ
slug: merge-quest
title: Merge Quest
status: active
created: 2026-07-18
updated: 2026-07-18
---
```

Der lesbare Sequenzzähler kann pro Projekt und Typ geführt werden. Konflikte im Zähler sind
unkritisch, weil die technische ID die echte Identität bildet. Der Core serialisiert die
Key-Vergabe bei API-Schreibvorgängen.

---

## 9. Datenmodell

### 9.1 Node-Hierarchie

```text
BaseNode
├── WorkItem
│   ├── Task
│   ├── Bug
│   └── Feature
├── KnowledgeItem
│   ├── Concept
│   └── Decision
├── EventItem
│   └── Meeting
└── Idea
```

Für das MVP sind `task`, `bug`, `feature`, `idea` und `decision` verpflichtend.
`concept` und `meeting` können in v1.1 ergänzt werden.

### 9.2 Gemeinsames Basisschema

```yaml
---
schema_version: 1
id: 01K0F6PS8RZ15PWK1Z3Q0A0V5M
key: MQ-TASK-001
type: task
title: "Core Merge Mechanic implementieren"

scope: project                 # project | workspace
project: merge-quest           # Pflicht bei scope: project

status: in-progress
created: 2026-07-18T14:20:00Z
updated: 2026-07-18T15:45:00Z
created_by: matze
updated_by: codex

tags: [prototype, flame-engine]
relations:
  - type: blocks
    target: 01K0F70AZS61D8T77NQF1TCG8J
  - type: decided_by
    target: 01K0F73W5CQG4N8MWG2WJAPZ4V
---

## Beschreibung

Die Merge-Logik für gleiche Items auf dem Spielfeld implementieren.

## Akzeptanzkriterien

- [ ] Drag-and-Drop auf einem 4×4-Grid
- [ ] Gleiche Items werden deterministisch zusammengeführt
- [ ] Animationen laufen auf einem Mid-Range-Phone flüssig
```

### 9.3 Typabhängige Felder und Lebenszyklen

#### Task

```yaml
status: backlog | ready | in-progress | blocked | review | done | cancelled
priority: urgent | high | medium | low
assignee: codex
estimate: 3d
due: 2026-07-25
```

#### Bug

```yaml
status: reported | triaged | in-progress | blocked | review | fixed | closed | rejected
severity: critical | high | medium | low
priority: urgent | high | medium | low
assignee: opencode
affected_version: 0.3.1
```

Empfohlene Body-Abschnitte:

- Schritte zur Reproduktion
- Erwartetes Verhalten
- Tatsächliches Verhalten
- Umgebung
- Hinweise und Logs

#### Feature

```yaml
status: proposed | planned | in-progress | review | delivered | cancelled
priority: urgent | high | medium | low
owner: matze
target_release: 0.4.0
```

#### Idea

```yaml
status: inbox | evaluating | accepted | rejected | promoted
score: 0
promoted_to: null
```

#### Decision

```yaml
status: proposed | accepted | superseded | rejected
supersedes: null
owners: [matze]
```

Empfohlene Body-Abschnitte:

- Kontext
- Entscheidung
- Alternativen
- Konsequenzen

#### Concept

```yaml
status: draft | review | accepted | obsolete
owners: [matze]
```

#### Meeting

```yaml
status: planned | completed | cancelled
starts_at: 2026-07-20T09:00:00Z
participants: [matze, codex]
```

### 9.4 Transition-Regeln

Statusänderungen werden nicht als beliebige Feldänderung behandelt. Jeder Typ besitzt einen
Transition-Graphen. Beispiele:

```text
Task:
backlog → ready → in-progress → review → done
                     │            │
                     └→ blocked ──┘

Idea:
inbox → evaluating → accepted → promoted
                   └→ rejected

Decision:
proposed → accepted → superseded
        └→ rejected
```

Ungültige Übergänge werden abgelehnt, sofern kein expliziter `force`-Aufruf mit Begründung
erfolgt.

### 9.5 Promotion und Scope-Wechsel

`promote_idea_to_project` (§11.3) ändert nicht nur den Status einer Idea, sondern auch ihren
Scope. Das ist im MVP der einzige Fall, in dem sich `scope`, `project` und `key` eines
bestehenden Items in einer einzigen Operation ändern:

1. Ziel-Projekt bestimmen oder neu anlegen.
2. neuen `key` im Zielprojekt-Namensraum vergeben (z. B. `WB-IDEA-014` → `MQ-FEAT-003`).
3. `scope: workspace` → `scope: project`, `project` setzen.
4. Datei von `workspace/` nach `projects/<slug>/` verschieben.
5. `promoted_to` auf den neuen `key` setzen; die ursprüngliche Idea-Datei bleibt mit
   `status: promoted` erhalten (kein Hard Delete).
6. alles in einer Git-Transaktion.

Die technische ID ändert sich dabei nie. Bestehende Relations auf die alte ID bleiben gültig;
`get_related` löst sie weiterhin auf, auch wenn das Item jetzt an anderer Stelle liegt.

### 9.6 Schema-Migration

`schema_version` ist pro Item gesetzt, nicht global. Das erlaubt inkrementelle Migration statt
eines Stop-the-World-Umbaus:

- Der Core kennt für jeden Typ einen Migrationspfad von jeder unterstützten `schema_version`
  zur aktuellen.
- Migrationen laufen lesend transparent: Ein Item mit alter `schema_version` wird beim Zugriff
  in-memory hochgezogen; die Datei wird erst bei der nächsten ohnehin stattfindenden Mutation
  neu geschrieben — keine Massen-Rewrites nur wegen eines Schema-Updates.
- `workbench migrate --dry-run` zeigt, wie viele Items pro Typ und Version betroffen wären.
- Breaking Changes (Feld entfernt oder Typ geändert) erhöhen `schema_version` und erfordern
  einen expliziten Migrationsschritt in `workbench doctor`.

---

## 10. Relationsmodell

Beziehungen sind gerichtet, typisiert und werden nur auf der Quellseite gespeichert.

```yaml
relations:
  - type: blocks
    target: 01K0F70AZS61D8T77NQF1TCG8J
```

Workbench berechnet inverse Ansichten im Index:

```text
A --blocks--> B
B --blocked_by--> A     # berechnet, nicht gespeichert
```

### 10.1 Kernrelationen

| Gespeicherte Relation | Berechnete inverse Relation | Bedeutung |
|---|---|---|
| `blocks` | `blocked_by` | Quelle blockiert Ziel |
| `parent_of` | `child_of` | Quelle ist fachlicher Parent des Ziels |
| `decided_by` | `decides` | Quelle wird durch eine Entscheidung begründet |
| `relates_to` | `related_from` | lose gerichtete Verbindung |
| `documented_in` | `documents` | Quelle ist im Ziel ausführlicher dokumentiert |
| `spawned_from` | `spawned` | Quelle entstand aus dem Ziel |
| `implements` | `implemented_by` | Quelle implementiert Ziel |
| `supersedes` | `superseded_by` | Quelle ersetzt Ziel |

### 10.2 Regeln

- Die technische ID ist das Relation-Target.
- Der Index prüft, ob Targets existieren.
- Dangling Relations werden als Validierungsfehler angezeigt, aber nicht still gelöscht.
- Zyklen sind grundsätzlich erlaubt, außer eine Relation definiert explizit eine azyklische Struktur.
- `parent_of` muss im MVP azyklisch sein.
- Relationsänderungen erfolgen atomar mit der restlichen Node-Änderung.
- Relations dürfen Projektgrenzen überschreiten (z. B. ein Task in `kochkumpel` `blocked_by`
  einer Decision in `merge-quest`). Der Index kennzeichnet projektübergreifende Relations
  gesondert, damit `get_project_context` sie optional ein- oder ausblenden kann.

---

## 11. Core-Operationen

Workbench unterscheidet drei Ebenen.

### 11.1 Primitive Commands

Primitive Commands verändern genau definierte Teile des Zustands:

```text
create_project
update_project
create_item
update_item
transition_item
archive_item
restore_item
link_items
unlink_items
assign_item
add_comment
```

`hard_delete_item` ist kein normaler Agenten-Command und bleibt administrativ beziehungsweise
bewusst freizuschalten.

### 11.2 Queries

```text
list_projects
get_project
get_item
search_items
list_items
get_backlog
get_related
get_activity
get_validation_errors
get_project_context
```

### 11.3 Höherwertige Capabilities / Workflows

Diese Operationen bilden fachliche Abläufe ab und dürfen mehrere atomare Änderungen ausführen:

```text
capture_idea
triage_idea
promote_idea_to_project
plan_feature
break_down_feature
record_decision
claim_next_task
complete_task
prepare_project_handoff
prepare_release
```

Beispiel `plan_feature`:

1. Feature erstellen oder aktualisieren.
2. optionale Entscheidung anlegen.
3. Feature in Tasks zerlegen.
4. Parent- und Blocker-Relations setzen.
5. alle Änderungen in einer Git-Transaktion speichern.

### 11.4 Keine versteckte LLM-Abhängigkeit

Der Workbench-Core ruft im MVP kein LLM auf. Capabilities wie `generate_roadmap` oder
`break_down_feature` können später auf zwei Arten umgesetzt werden:

- als deterministische Orchestrierung bereits strukturierter Eingaben,
- als optionaler Plugin-Workflow mit einem extern konfigurierten Modell.

Das Datenmodell und die Kernoperationen dürfen nicht von einem bestimmten Modell abhängen.

### 11.5 Bezug zu MCP Tasks (Ausblick)

Die MCP-Spezifikation erhält mit der 2026-07-28-Revision eine eigene Tasks-Extension: Ein Tool
kann statt einer sofortigen Antwort einen Task-Handle zurückgeben, den der Client über
`tasks/get`, `tasks/update` und `tasks/cancel` weiterverfolgt. Das passt konzeptionell zu
mehrstufigen Workflows wie `plan_feature` oder `prepare_project_handoff`, die mehrere Commands
und später eventuell Modellaufrufe bündeln.

Für das MVP ändert das nichts — Workflows bleiben synchrone Aufrufe. Wenn Capabilities später
tatsächlich lang laufen (etwa durch einen Plugin-Workflow mit Modellaufruf, §11.4), ist die
Tasks-Extension der naheliegende Anschlusspunkt, statt ein eigenes Polling-Schema zu erfinden.
Näheres in §13.5.

---

## 12. REST API

Basis-Pfad:

```text
/api/v1
```

### 12.1 Commands

```text
POST   /projects
PATCH  /projects/{project_id}

POST   /items
PATCH  /items/{item_id}
POST   /items/{item_id}/transitions
POST   /items/{item_id}/archive
POST   /items/{item_id}/restore

POST   /relations
DELETE /relations
```

### 12.2 Queries

```text
GET /projects
GET /projects/{project_id}
GET /projects/{project_id}/items
GET /projects/{project_id}/backlog
GET /projects/{project_id}/context
GET /items/{item_id}
GET /items/{item_id}/related
GET /search
GET /activity
GET /validation-errors
```

### 12.3 Workflows

```text
POST /workflows/capture-idea
POST /workflows/plan-feature
POST /workflows/record-decision
POST /workflows/claim-next-task
POST /workflows/complete-task
POST /workflows/prepare-project-handoff
```

### 12.4 Mutations-Metadaten

Jeder Schreibaufruf unterstützt:

```json
{
  "actor": "codex",
  "reason": "Implementation abgeschlossen und Tests grün",
  "request_id": "req_01K0FA...",
  "expected_revision": "7f3d1c2"
}
```

- `actor` identifiziert Mensch, Agent oder Integration.
- `reason` erklärt die Änderung im Git-Commit.
- `request_id` ermöglicht idempotente Wiederholungen.
- `expected_revision` schützt vor verlorenen Updates.

`actor` ist im MVP ein selbst deklariertes Feld, kein kryptografisch geprüfter Wert — mit einem
einzigen API-Key kann technisch jeder Client jeden Actor-Namen angeben. Für das Single-User-MVP
ist das für den Audit-Zweck ausreichend, aber keine Zugriffskontrolle. Sobald §20.3
(Mehrbenutzerfähigkeit) umgesetzt wird, muss `actor` an die authentifizierte Identität des
Tokens gebunden werden, damit Git-Historie und Audit Log nicht durch einen fehlkonfigurierten
oder böswilligen Client verfälscht werden können.

### 12.5 Antwortformat

```json
{
  "data": {},
  "repository_revision": "8a1f294",
  "warnings": [],
  "request_id": "req_01K0FA..."
}
```

### 12.6 Fehlerklassen

```text
validation_error
transition_not_allowed
revision_conflict
item_not_found
relation_target_not_found
duplicate_request
unauthorized
forbidden
repository_error
index_error
```

---

## 13. MCP-Adapter

Der MCP-Adapter läuft im selben Service und ruft ausschließlich den Workbench-Core auf.

### 13.1 Transport

Primärer Remote-Transport:

```text
Streamable HTTP: https://workbench.example.com/mcp
```

Optional:

```text
stdio                  # lokale Agenten und Entwicklungsbetrieb
Legacy HTTP+SSE        # nur als zeitlich begrenzte Kompatibilitätsschicht
```

Die konkret unterstützte MCP-Protokollversion wird in Releases dokumentiert und in
Integrationstests gepinnt. Der Adapter soll Transportdetails kapseln, damit spätere
Protokolländerungen nicht in den Workbench-Core durchsickern.

### 13.2 MCP Tools

Schreibende oder prozessuale Aktionen werden als Tools angeboten:

```text
workbench_create_item
workbench_update_item
workbench_transition_item
workbench_archive_item
workbench_link_items
workbench_capture_idea
workbench_plan_feature
workbench_record_decision
workbench_claim_next_task
workbench_complete_task
```

Tool-Metadaten kennzeichnen mindestens:

- read-only oder mutierend
- idempotent oder nicht idempotent
- potenziell destruktiv
- erforderliche Bestätigung

### 13.3 MCP Resources

Lesender Kontext wird primär über Resources angeboten:

```text
workbench://workspace
workbench://projects
workbench://projects/{project_slug}
workbench://projects/{project_slug}/backlog
workbench://projects/{project_slug}/context
workbench://projects/{project_slug}/decisions
workbench://items/{item_id}
workbench://items/{item_id}/related
workbench://activity/recent
```

Resources liefern strukturierte Metadaten und eine kompakte Markdown-Darstellung.

### 13.4 MCP Prompts — optional

Wiederverwendbare Prompt-Templates können später angeboten werden:

```text
project-handoff
triage-inbox
plan-next-iteration
review-open-decisions
prepare-release
```

Prompts sind Komfortfunktionen und nicht Teil des fachlichen Kernzustands.

### 13.5 Protokollentwicklung — Ausblick

Die MCP-Spezifikation `2025-11-25`, auf die sich ADR-008 bezieht, ist aktuell die stabile
Version. Ein Release Candidate für `2026-07-28` liegt bereits vor, mit finaler Veröffentlichung
in Kürze angekündigt — die größte Revision seit Protokollstart:

- **Stateless Core:** Initialize-Handshake und protokollseitiger Session-State entfallen. Jeder
  Request trägt Protokollversion, Client-Informationen und Capabilities selbst mit. Ein
  zustandsloser Server passt besser zum "Thin Adapter"-Prinzip (§5) als der bisherige
  session-basierte Ablauf.
- **MCP Apps (SEP-1865):** Server können interaktive HTML-Oberflächen ausliefern, die der Host
  in einem sandboxed iframe rendert — standardisiert über `_meta.ui.resourceUri` und eine
  `ui/*`-JSON-RPC-Bridge. Das ist kein Anthropic-only-Feature mehr: Claude, ChatGPT, Goose und
  VS Code unterstützen es bereits. Für die ADR "Öffentliche Distribution" (§30) heißt das: Eine
  Workbench-UI, die als MCP App gebaut wird, kann in mehreren Hosts laufen, ohne eine separate
  ChatGPT-Apps-SDK-Integration zu pflegen.
- **Tasks-Extension:** siehe §11.5.
- **Auth-Härtung:** engere Anlehnung an OAuth/OIDC — deckt sich mit dem bereits geplanten Weg
  in §20.3.
- **Formale Deprecation Policy:** Active/Deprecated/Removed-Status mit Mindestfristen, senkt
  das Risiko, heute auf MCP zu bauen.

**Konsequenz für den MVP:** nicht auf den Release Candidate bauen, solange er nicht final ist —
das aktuelle, stabile Python-SDK bleibt für den MVP-Start die sichere Wahl. Aber: den Adapter so
kapseln, wie ADR-008 es ohnehin vorsieht, und den Wechsel auf das kommende SDK (dort in Beta
bereits als `MCPServer` statt `FastMCP` geführt) und den stateless Core früh in Phase 1 oder 2
einplanen statt ihn zu verdrängen.

---

## 14. Projektkontext und Agenten-Handover

`get_project_context` ist eine zentrale Kernfunktion. Sie ist im MVP deterministisch und
ruft kein LLM auf.

### 14.1 Eingaben

```json
{
  "project": "merge-quest",
  "include": ["active_work", "blockers", "decisions", "recent_activity"],
  "max_items": 50,
  "since": "2026-07-01T00:00:00Z",
  "format": "markdown"
}
```

Default für `format`, wenn nicht angegeben, ist `json` (ADR-014) — die Markdown-Variante bleibt
verfügbar für Menschen und für Clients, die Kontext direkt als Text in einen Prompt einbetten
wollen.

### 14.2 Inhalt eines Context Packs

1. Projektziel und Metadaten
2. aktueller Projektstatus
3. aktive, blockierte und überfällige Work Items
4. offene oder kürzlich akzeptierte Entscheidungen
5. relevante Konzepte
6. letzte Änderungen
7. aktuelle Agenten-Zuweisungen
8. Risiken und ungeklärte Punkte
9. mögliche nächste Aufgaben
10. zugrunde liegende Repository-Revision

### 14.3 Priorisierung

Bei begrenztem Kontextbudget werden Inhalte in dieser Reihenfolge ausgewählt:

1. explizit angeforderte Items
2. aktive und blockierte Arbeit
3. direkte Relationsnachbarn
4. offene Entscheidungen
5. kürzliche Aktivität
6. ältere Hintergrunddokumente

### 14.4 Handover-Workflow

`prepare_project_handoff` kann zusätzlich einen neuen Handover-Node erzeugen, der festhält:

- bisheriger Agent
- neuer Agent oder Ziel-Client
- aktueller Stand
- offene Änderungen
- lokale Annahmen
- empfohlener nächster Schritt
- relevante Item-IDs und Commit-Revisionen

---

## 15. Git-Transaktionen und Nebenläufigkeit

### 15.1 Single-Writer-Prinzip im MVP

Alle Schreiboperationen über Workbench werden innerhalb des Service-Prozesses serialisiert.
Ein Repository-Lock verhindert parallele Git-Schreibvorgänge.

Das ist kein Multi-User-Locking-Modell, reicht aber für mehrere Agenten und einen Menschen,
solange sie denselben Service verwenden.

### 15.2 Optimistic Locking

Jede Mutation kann `expected_revision` mitsenden. Stimmt diese nicht mehr mit der aktuellen
Repository-Revision überein, antwortet der Core mit `revision_conflict` und verändert nichts.

### 15.3 Idempotenz

`request_id` wird im SQLite-Index mit Ergebnis und Commit-Revision gespeichert. Wird derselbe
Aufruf nach einem Timeout wiederholt, liefert Workbench das bereits erzeugte Ergebnis zurück.

### 15.4 Commit-Modi

```yaml
git:
  commit_mode: debounce       # immediate | debounce | manual
  debounce_seconds: 30
  author_name: Workbench
  author_email: workbench@localhost
```

- **immediate:** Jeder erfolgreiche Command erzeugt sofort einen Commit.
- **debounce:** Zusammengehörige Änderungen werden für kurze Zeit gesammelt.
- **manual:** Änderungen bleiben im Working Tree, bis explizit committed wird.

Empfehlung für das MVP: `debounce`.

### 15.5 Commit-Nachrichten

```text
feat(MQ-TASK-001): move task to review

Actor: codex
Request-Id: req_01K0FA...
Reason: Implementation und Tests abgeschlossen
```

### 15.6 Direkte Git-Änderungen

Direkte Änderungen außerhalb des Service sind erlaubt. Der Service darf sie nicht automatisch
überschreiben. Vor jeder Mutation prüft er deshalb:

- sauberen beziehungsweise erwarteten Repository-Zustand,
- aktuelle HEAD-Revision,
- Dateihash des betroffenen Items,
- Schema- und Relationskonsistenz.

Der File Watcher (§17.2) darf während einer laufenden Core-Transaktion nicht gleichzeitig auf
dieselbe Datei reagieren. Der Service hält den Repository-Lock aus §15.1 auch gegenüber dem
Watcher; Watcher-Events für Dateien, die gerade Teil einer aktiven Transaktion sind, werden
zurückgestellt statt sofort verarbeitet.

### 15.7 Konflikt-Recovery für Agenten

`revision_conflict` (§12.6) beendet die Mutation, löst das Problem aber nicht automatisch. Für
Agenten-Clients ist folgendes Verhalten empfohlen:

1. Bei `revision_conflict` das betroffene Item beziehungsweise den Projektkontext neu laden.
2. Prüfen, ob die eigene Änderung inhaltlich noch gültig ist (z. B. wurde der Status von
   jemand anderem bereits weiter transitioniert).
3. genau einen automatischen Retry mit aktualisierter `expected_revision` versuchen.
4. Schlägt auch der Retry fehl, den Konflikt dem Actor melden statt weiter zu retryen.

Das gehört in die Adapter-Dokumentation (MCP-Tool-Beschreibungen, REST-Client), nicht in den
Core — der Core liefert nur den Fehler, die Retry-Strategie ist Sache des Clients. Ein blind
wiederholender Agent ohne Schritt 2 kann sonst eine fremde, zwischenzeitlich gültige Änderung
überschreiben, sobald sein Retry technisch erfolgreich ist.

---

## 16. Validierung

### 16.1 Validierungsstufen

1. **Syntax:** gültiges YAML-Frontmatter und Markdown-Datei
2. **Schema:** Pflichtfelder und typabhängige Datentypen
3. **Lifecycle:** gültiger Status und erlaubter Übergang
4. **Identity:** eindeutige technische ID und eindeutiger Key
5. **Relations:** gültige Relationstypen und vorhandene Targets
6. **Graph:** Regeln wie azyklisches `parent_of`
7. **Repository:** Dateiname, Pfad und Scope stimmen zusammen

### 16.2 Fehlerbehandlung

Ungültige Dateien werden nicht aus dem Repository verschoben oder automatisch repariert.
Der Index speichert den Fehler und blendet das Item in normalen Boards standardmäßig aus.
Eine spezielle Ansicht zeigt alle Probleme.

### 16.3 CLI

```bash
workbench validate
workbench validate --fix-safe
workbench validate --file projects/merge-quest/MQ-TASK-001.md
```

`--fix-safe` darf nur eindeutig verlustfreie Reparaturen ausführen, beispielsweise fehlende
formatierbare Zeitstempel oder normalisierte Enum-Schreibweisen.

---

## 17. Indexer

### 17.1 Eigenschaften

- vollständig rekonstruierbar
- transaktional aktualisiert
- tolerant gegenüber einzelnen ungültigen Dateien
- nachvollziehbar über Content Hash und Repository-Revision
- unabhängig von der UI

### 17.2 Trigger

- **Startup:** inkrementeller oder vollständiger Indexabgleich
- **Nach Core-Mutation:** synchrone Aktualisierung betroffener Nodes
- **File Watcher:** Verarbeitung direkter Dateisystemänderungen
- **Git Pull oder Branch-Wechsel:** Reconciliation gegen die neue Revision
- **CLI:** `workbench reindex`

### 17.3 SQLite-Schema — vereinfacht

```sql
CREATE TABLE items (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL,
    project TEXT,
    status TEXT,
    priority TEXT,
    assignee TEXT,
    tags_json TEXT NOT NULL,
    body TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    updated_by TEXT,
    repository_revision TEXT NOT NULL
);

CREATE TABLE relations (
    source_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (source_id, relation_type, target_id)
);

CREATE VIRTUAL TABLE items_fts USING fts5(
    item_id UNINDEXED,
    key,
    title,
    body,
    tags
);

CREATE TABLE validation_errors (
    file_path TEXT NOT NULL,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (file_path, error_code, message)
);

CREATE TABLE idempotency_records (
    request_id TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    repository_revision TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 17.4 Konsistenz

Nach einer Mutation gilt Read-your-writes-Konsistenz: Die API bestätigt einen Erfolg erst,
wenn Markdown, Git-Commit und betroffener Indexstand erfolgreich aktualisiert wurden.

Bei einem Indexfehler bleibt Git die Wahrheit. Der Service meldet einen degradierenden Zustand
und versucht einen gezielten Reindex.

### 17.5 Content Hash

`content_hash` (SQLite-Schema in §17.3) wird über die normalisierte Datei nach dem Round-Trip
durch den YAML/Markdown-Serializer gebildet, nicht über die rohen Datei-Bytes. Grund: Zwei
inhaltlich identische Dateien mit unterschiedlicher, aber semantisch irrelevanter Formatierung
(Zeilenumbrüche, Key-Reihenfolge im Frontmatter) sollen denselben Hash ergeben. Das ist dieselbe
Normalisierung, die die Golden-File-Tests in §28.2 bereits voraussetzen — beide sollten auf
derselben Serialisierungsfunktion aufsetzen, damit sie nicht auseinanderlaufen.

---

## 18. CLI

Die CLI ist ein Kernbestandteil der Offenheit und Administration.

```bash
workbench init /path/to/workbench-data
workbench serve
workbench doctor
workbench validate
workbench reindex
workbench project create "Merge Quest" --key MQ
workbench item create --project merge-quest --type task --title "Merge mechanic"
workbench item transition MQ-TASK-001 review
workbench search "merge animation"
workbench context merge-quest
workbench import linear export.csv
```

### MVP-Kommandos

- `init`
- `serve`
- `doctor`
- `validate`
- `reindex`
- `project create/list/show`
- `item create/show/list/transition`
- `search`
- `context`

---

## 19. Web UI

Die UI ist eine Ansicht auf den Core, nicht das Produktfundament.

### 19.1 MVP

- Projektliste
- Projektübersicht
- einfacher Backlog und Kanban-View
- Volltextsuche
- Item-Detailansicht
- Validierungsfehler
- letzte Aktivität

Das MVP darf zunächst read-only sein. Schreibfunktionen werden erst ergänzt, wenn Commands,
MCP und direkte Markdown-Bearbeitung stabil funktionieren.

### 19.2 Später

- Drag-and-drop mit validierten Statusübergängen
- Inline-Bearbeitung
- Graph View
- Timeline
- Inbox-Triage
- Handover-Ansicht
- Dark Mode
- Board- und Kontext-Ansicht zusätzlich als MCP App (§13.5) — dieselbe Jinja2/HTMX-Basis
  potenziell eingebettet in Claude, ChatGPT, Goose oder VS Code, statt einer separaten
  Distribution

### 19.3 Technologie

- MVP: Jinja2 + HTMX
- kleine Alpine.js-Komponenten nur bei echtem Bedarf
- React oder Svelte erst, wenn die Interaktivität dies begründet

### 19.4 Design

- Primary Orange: `#F97316`
- Secondary Green: `#22C55E`
- Background Warm White: `#FFFBF5`
- Font: Inter
- minimalistisch, schnell, zugänglich

---

## 20. Sicherheit und Zugriff

### 20.1 Single-User-MVP

Empfohlene Stufen:

1. Zugriff nur im privaten Netzwerk oder über Tailscale
2. zusätzlicher API-Key beziehungsweise Bearer Token
3. TLS über Reverse Proxy oder Tunnel
4. getrennte Tokens für unterschiedliche Clients

Getrennte Tokens (Punkt 4) lassen sich schon im MVP nutzen, um einem weniger vertrauenswürdigen
Client — etwa einem Remote-Agenten ohne lokale Kontrolle — nur lesenden Zugriff zu geben, auch
ohne das volle Scope-Modell aus §20.3. Kein Ersatz für echte Autorisierung, aber eine spürbare
Reduktion des Risikos durch einzelne kompromittierte oder fehlerhafte Clients.

### 20.2 Remote MCP

- Der Standard-Endpunkt ist `/mcp` über HTTPS.
- Der Server validiert zulässige Origins.
- Lokaler Betrieb bindet standardmäßig an `127.0.0.1`.
- Ein Binding auf `0.0.0.0` muss explizit konfiguriert werden.
- Secrets werden niemals im Markdown- oder Code-Repository gespeichert.

ChatGPT benötigt einen erreichbaren Remote-MCP-Server. Für private Installationen kann der
Zugriff über einen sicheren Tunnel erfolgen; andere Clients können direkt über Tailscale,
VPN oder HTTPS zugreifen.

### 20.3 Spätere Mehrbenutzerfähigkeit

Für eine öffentliche oder gemeinsam genutzte Installation:

- OAuth-basierte Autorisierung für Remote MCP
- Benutzer- und Service-Accounts
- Scopes wie `read`, `write`, `admin`
- Projektberechtigungen
- Audit Log unabhängig von Git-Autoren
- Rate Limiting

### 20.4 Tool-Sicherheit

- mutierende MCP-Tools werden als solche gekennzeichnet,
- Archivieren ist Standard, Hard Delete ist eingeschränkt,
- kritische Batch-Operationen benötigen eine explizite Bestätigung,
- Input aus Projektinhalten darf keine Auth- oder Berechtigungsregeln überschreiben.

---

## 21. Deployment

### 21.1 Docker Compose

```yaml
services:
  workbench:
    build: .
    container_name: workbench
    ports:
      - "127.0.0.1:8500:8500"
    volumes:
      - ./repo:/data/repo
      - ./db:/data/db
      - ./config:/data/config:ro
    environment:
      WORKBENCH_REPO_PATH: /data/repo
      WORKBENCH_DB_PATH: /data/db/workbench.db
      WORKBENCH_CONFIG_PATH: /data/config/config.yml
      WORKBENCH_HOST: 0.0.0.0
      WORKBENCH_PORT: 8500
      WORKBENCH_COMMIT_MODE: debounce
      WORKBENCH_LOG_FORMAT: json
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8500/health/live"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

Der Host-Port ist im Beispiel nur lokal erreichbar. Tailscale, Caddy, Traefik oder ein sicherer
Tunnel können davor geschaltet werden.

### 21.2 Endpunkte

```text
GET  /health/live
GET  /health/ready
GET  /api/v1/...
POST /mcp
GET  /mcp               # abhängig von Transport- und SDK-Unterstützung
GET  /ui/...
```

---

## 22. Tech Stack

| Komponente | Technologie |
|---|---|
| Backend | Python 3.12+ |
| API | FastAPI + Uvicorn |
| Validierung | Pydantic |
| YAML | `ruamel.yaml` für möglichst format- und kommentarfreundliche Round-Trips |
| Markdown | CommonMark-kompatibler Parser |
| Index | SQLite + FTS5 |
| Git | Git CLI über kontrollierten Subprocess-Wrapper |
| MCP | Offizielles oder etabliertes Python MCP SDK |
| CLI | Typer |
| Frontend | Jinja2 + HTMX |
| File Watcher | `watchfiles` oder vergleichbar |
| Tests | pytest + Golden Files + Integrationstests |
| Container | Docker, schlankes Python-Base-Image |

### Warum Git CLI statt GitPython im MVP?

- Verhalten entspricht direkt dem lokal debuggbaren Git.
- Worktrees, Hooks und Authentifizierung bleiben transparent.
- Weniger Abstraktionsunterschiede zu manuellen Repository-Operationen.
- Fehlerausgaben können unverändert in Diagnoseinformationen einfließen.

---

## 23. Interne Code-Struktur

```text
workbench/
├── src/workbench/
│   ├── domain/
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── transitions.py
│   │   └── relations.py
│   ├── core/
│   │   ├── commands.py
│   │   ├── queries.py
│   │   ├── workflows.py
│   │   └── context_builder.py
│   ├── repository/
│   │   ├── markdown_store.py
│   │   ├── git_transactions.py
│   │   └── locking.py
│   ├── index/
│   │   ├── database.py
│   │   ├── indexer.py
│   │   └── search.py
│   ├── adapters/
│   │   ├── rest/
│   │   ├── mcp/
│   │   ├── cli/
│   │   └── web/
│   ├── auth/
│   └── config.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── golden/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## 24. MVP

### 24.1 Ziel

Ein Mensch und mindestens zwei unterschiedliche Agenten können denselben Projektzustand
zuverlässig lesen und verändern, ohne direkt voneinander abhängig zu sein.

### 24.2 Erfolgskriterien

Der MVP ist erfolgreich, wenn:

- ein Workspace und mehrere Projekte angelegt werden können,
- Ideen ohne Projekt erfasst werden können,
- Tasks, Bugs, Features und Entscheidungen als Markdown entstehen,
- direkte Markdown-Änderungen erkannt und validiert werden,
- der Index jederzeit rekonstruierbar ist,
- Agent A einen Task anlegt und Agent B ihn findet und übernimmt,
- Statusübergänge und Relations konsistent validiert werden,
- ein kompakter Projektkontext über MCP abrufbar ist,
- Git-Historie jede Änderung nachvollziehbar macht,
- der komplette Dienst in einem Container läuft.

### 24.3 Must Have

- [ ] `workbench init`, `validate`, `reindex`, `doctor`
- [ ] Markdown-Store mit typisiertem Frontmatter
- [ ] technische IDs plus lesbare Keys
- [ ] Typen: Project, Task, Bug, Feature, Idea, Decision
- [ ] typabhängige Statusmodelle
- [ ] kanonische Relations
- [ ] Git Transaction Manager mit Lock
- [ ] `expected_revision` und `request_id`
- [ ] SQLite-Indexer und FTS5-Suche
- [ ] Core Commands und Queries
- [ ] deterministischer Project Context Builder
- [ ] REST API
- [ ] MCP Streamable HTTP
- [ ] MCP Tools für Mutationen
- [ ] MCP Resources für Kontext
- [ ] API-Key-Authentifizierung
- [ ] Docker-Container
- [ ] minimale read-only Web UI
- [ ] Unit- und Integrations-Tests für kritische Pfade

### 24.4 Explizit nicht im MVP

- Graph-Visualisierung
- Timeline-UI
- Multi-User-Modell
- komplexes Rollen- und Rechtekonzept
- integrierte LLM-Synthese
- Plugin-System
- automatischer GitHub-Sync
- Branch- und Pull-Request-Workflows in der UI
- mobile App
- vollständiger Linear-Import
- Drag-and-drop als zwingende Funktion

### 24.5 Realistischer Wochenend-Schnitt

Für einen ersten vertikalen Prototypen:

1. Repository initialisieren
2. Project, Task und Idea implementieren
3. Create, Get, List, Transition und Search
4. SQLite-Index
5. MCP mit zwei Tools und zwei Resources
6. Docker
7. einfache HTML-Liste

Bug, Feature, Decision, Relations, Handover und erweiterte Auth folgen unmittelbar danach.

---

## 25. Roadmap

### Phase 0 — RFC und Spike

- [ ] Datenmodell finalisieren
- [ ] MCP-SDK und Streamable-HTTP-Kompatibilität testen
- [ ] YAML-Round-Trip mit realen Dateien testen
- [ ] Git-Lock- und Debounce-Prototyp bauen
- [ ] Namen und Lizenz festlegen

### Phase 1 — Vertical Slice

- [ ] Repository und Projekt anlegen
- [ ] Task und Idea verwalten
- [ ] Transition-Validierung
- [ ] SQLite-Index und Suche
- [ ] REST API
- [ ] MCP: Create Item, Transition Item, Project Context
- [ ] Docker-Deployment im Homelab

### Phase 2 — Nutzbarer MVP

- [ ] Bug, Feature und Decision
- [ ] Relations
- [ ] Context Builder und Handover
- [ ] direkte Dateiänderungen und Watcher
- [ ] read-only Web UI
- [ ] API-Key und Audit-Metadaten
- [ ] erste reale Projekte migrieren

### Phase 3 — Produktivität

- [ ] Kanban-Schreiboberfläche
- [ ] Inbox-Triage
- [ ] Timeline und Activity Feed
- [ ] Importer für Linear und generisches CSV
- [ ] Git Remote Pull/Push
- [ ] Release-Workflow

### Phase 4 — Kollaboration

- [ ] Multi-User
- [ ] OAuth und Scopes
- [ ] Projektberechtigungen
- [ ] Kommentare und Benachrichtigungen
- [ ] Webhooks oder Event Stream

### Phase 5 — Erweiterbarkeit und Synthese

- [ ] Plugin-System
- [ ] optionale LLM-Provider
- [ ] Roadmap-Synthese
- [ ] Release Notes
- [ ] Risiko- und Konsistenzanalysen
- [ ] Graph View

---

## 26. Migration

### 26.1 Linear

Ein Importer sollte vorhandene Issues übernehmen:

- Titel und Beschreibung
- Statusmapping
- Priorität
- Labels
- Kommentare optional
- Parent-Child-Beziehungen
- externe Ursprungs-ID

```yaml
external_refs:
  - system: linear
    id: ABC-123
    url: null
```

Der MVP kann zunächst einen CSV-Import unterstützen. Eine direkte API-Integration folgt später.

### 26.2 Obsidian und lose Markdown-Notizen

Ein Importer erkennt Frontmatter, erzeugt fehlende IDs und ordnet Dateien zunächst als
`concept` oder `idea` ein. Unsichere Zuordnungen landen in einer Review-Liste.

### 26.3 Generischer Import

```bash
workbench import csv tasks.csv --mapping mapping.yml
workbench import markdown ./notes --default-type idea
```

---

## 27. Betrieb und Observability

### 27.1 Health

- Liveness: Prozess läuft
- Readiness: Repository lesbar, Lock verfügbar, Index geöffnet
- Degraded: Repository funktioniert, Index benötigt Rebuild

### 27.2 Logging

Strukturierte Logs enthalten:

- request_id
- actor
- operation
- item_id
- repository_revision
- duration
- result

Keine vollständigen Item-Inhalte oder Secrets werden standardmäßig geloggt.

### 27.3 Diagnose

```bash
workbench doctor
```

prüft:

- Git-Installation und Repository-Zustand
- Schreibrechte
- SQLite-Integrität
- Schema-Version
- Indexabweichungen
- MCP-Endpunkt
- Konfiguration
- Lock-Dateien

### 27.4 Metriken (Ausblick)

Für das MVP reichen strukturierte Logs (§27.2) und `workbench doctor`. Sobald Workbench über
den Homelab-Gebrauch hinausgeht, sind ein paar einfache Zähler und Zeitmessungen sinnvoll, ohne
dafür schon eine Metrik-Infrastruktur einzuführen:

- Command- und Query-Latenz nach Typ
- Git-Operationsdauer (Lock-Wartezeit separat von Commit-Dauer)
- Index-Lag zwischen Mutation und sichtbarem Indexstand
- Anzahl `revision_conflict` pro Zeitraum (hoher Wert deutet auf zu grobes Locking oder zu
  viele parallele Agenten hin)

Ein einfacher `/metrics`-Endpoint im Prometheus-Textformat wäre für Phase 3 oder 4 ausreichend;
für den MVP explizit kein Muss.

---

## 28. Tests

### 28.1 Unit Tests

- Schema-Validierung
- Statusübergänge
- Relation-Regeln
- ID- und Key-Erzeugung
- Context-Priorisierung

### 28.2 Golden-File-Tests

Markdown-Dateien werden eingelesen, verändert und wieder geschrieben. Tests prüfen, dass:

- semantisch irrelevante Formatierung möglichst erhalten bleibt,
- kein Inhalt verloren geht,
- Frontmatter stabil serialisiert wird,
- erwartete Diffs klein bleiben.

### 28.3 Integrationstests

- Command → Markdown → Git Commit → SQLite → Query
- direkte Dateiänderung → Watcher → Validation → Index
- Revision Conflict
- idempotenter Retry
- MCP Tool Call
- MCP Resource Read
- Reindex nach gelöschter Datenbank

### 28.4 Kompatibilitätstests

Mindestens zwei unterschiedliche MCP-Clients werden im MVP regelmäßig getestet. Client-spezifische
Konfigurationen gehören in die Dokumentation, nicht in den Workbench-Core.

---

## 29. Abgrenzung

| Tool | Hauptfokus | Was Workbench anders macht |
|---|---|---|
| **Linear** | Cloud-Issue-Tracking | Markdown-first, selbst gehostet, agenten- und projektübergreifend |
| **GitHub Issues** | Issues an Code-Repositories | Workspace-Ideen und Projekte müssen nicht an Repositories gebunden sein |
| **Notion** | flexible Dokument- und Datenbankplattform | Git-native, typisierte Nodes, stabile Agenten-API |
| **Obsidian** | persönliche Markdown-Wissensbasis | zentraler Service, validierte Workflows, Mehr-Agenten-Zugriff |
| **Plane** | selbst gehosteter Issue-Tracker | Markdown als kanonischer Zustand und kleiner modularer Monolith |
| **Vikunja** | Task-Management | Wissensartefakte, Relationsgraph, MCP Resources und Agenten-Handover |

Workbench konkurriert nicht primär über eine größere UI, sondern über Offenheit,
Nachvollziehbarkeit und agentenübergreifenden Kontext.

---

## 30. Architekturentscheidungen

### Bereits festgelegt

| ID | Entscheidung |
|---|---|
| ADR-001 | Markdown ist die fachliche Source of Truth. |
| ADR-002 | SQLite ist ein vollständig abgeleiteter Index. |
| ADR-003 | Workbench startet als modularer Monolith in einem Container. |
| ADR-004 | REST, MCP, CLI und UI verwenden denselben Core. |
| ADR-005 | Relations werden nur in kanonischer Richtung gespeichert. |
| ADR-006 | Technische IDs und lesbare Keys werden getrennt. |
| ADR-007 | Der MVP-Core enthält keine verpflichtende LLM-Abhängigkeit. |
| ADR-008 | Streamable HTTP ist der primäre Remote-MCP-Transport. |
| ADR-009 | Direkte Markdown-Bearbeitung wird offiziell unterstützt. |
| ADR-010 | Single-User und Single-Writer sind für das MVP ausreichend. |
| ADR-011 | Default Commit Mode ist `debounce` (§15.4). |
| ADR-012 | ID-Format ist UUIDv7 (RFC 9562): standardisiert, sortierbar, wachsende native DB- und Bibliotheksunterstützung. ULID bleibt technisch gleichwertig, aber ohne den Standardisierungsvorteil. |
| ADR-013 | Kommentare sind ein strukturierter Body-Abschnitt (`## Kommentare`: Actor, Zeitstempel, Text), kein eigener Node — ein eigener Lebenszyklus wäre im MVP unverhältnismäßig. |
| ADR-014 | `get_project_context` liefert JSON als primäres Format, Markdown-Rendering ist abgeleitet (§14.1) — Agenten sollen nicht auf Markdown-Parsing angewiesen sein, um Kontext zuverlässig zu verarbeiten. |
| ADR-015 | Remote Git: automatischer Push nach jedem Commit oder Debounce-Batch (risikofrei für den Single-Writer, dient als einfaches Offsite-Backup), Pull bleibt manuell (Konfliktrisiko mit lokalem, uncommittetem Zustand). |
| ADR-016 | Legacy HTTP+SSE entfällt im MVP; Streamable HTTP und stdio decken die relevanten Clients ab, SSE kommt nur bei konkretem Kompatibilitätsbedarf dazu. |
| ADR-017 | Archivierung erfolgt über ein Status-Feld, Dateien bleiben an ihrem Ort — kein `_archive/`-Ordner im MVP. Passt zu ADR-002 (Index ist ableitbar) und vermeidet kaputte relative Verweise durch Dateiverschiebung. |

### Noch offen

1. **Name:** Bleibt `Workbench` oder wird ein spezifischerer Projektname gewählt? *Hinweis:*
   „Workbench" ist in der Software-Welt stark vorbelegt (u. a. Eclipse Workbench, Salesforce
   Workbench) — die Namens- und Registrierbarkeitsprüfung lohnt sich vor einer öffentlichen
   Distribution, nicht erst danach.
2. **Lizenz:** Apache-2.0 oder MIT? *Empfehlung:* Apache-2.0, wegen des expliziten
   Patent-Grants — relevant, sobald mehr als eine Person beiträgt (z. B. IT Klub). MIT bleibt
   die einfachere Option, falls das nie relevant wird.
3. **Öffentliche Distribution:** reines MCP-Projekt oder zusätzlich ChatGPT-App-Erlebnis?
   *Update:* weniger folgenreich als noch in v0.2 — MCP Apps (§13.5) ist inzwischen ein
   herstellerübergreifender Standard, den Claude, ChatGPT, Goose und VS Code unterstützen. Eine
   spätere UI (§19.2) kann als MCP App gebaut werden, ohne sich früh auf einen Client
   festzulegen.

---

## 31. Namens- und Positionierungsidee

**Arbeitsname:** Workbench

**Kategorie:** Agentic Project Context Store

**Kurzbeschreibung:**

> Workbench is a Git-native, Markdown-first project context store where humans and AI agents
> share tasks, ideas, decisions and handovers through MCP, REST and CLI.

Mögliche spätere Namen sollten suchbarer und weniger generisch sein. Der Name muss ausdrücken,
dass es nicht nur um Tasks, sondern um gemeinsamen, dauerhaften Agentenkontext geht.

---

## 32. Referenzen

- Model Context Protocol — Transports: <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
- Model Context Protocol — Authorization: <https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- Model Context Protocol — 2026-07-28 Release Candidate (stateless Core, MCP Apps, Tasks): <https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/>
- Model Context Protocol — Roadmap 2026: <https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/>
- Gemini CLI — MCP Servers: <https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md>
- OpenAI — Developer mode and MCP apps in ChatGPT: <https://help.openai.com/en/articles/12584461>
- OpenAI — MCP Apps compatibility in ChatGPT: <https://developers.openai.com/apps-sdk/mcp-apps-in-chatgpt>

---

## 33. Nächster konkreter Schritt

Vor dem eigentlichen MVP sollte ein kleiner technischer Spike vier Risiken klären:

1. Round-Trip-Bearbeitung von YAML-Frontmatter ohne unnötig große Diffs
2. zuverlässige Git-Transaktionen mit Lock, Revision Check und Debounce
3. MCP Streamable HTTP mit mindestens zwei realen Clients
4. vollständiger Reindex aus einem absichtlich beschädigten oder teilweise ungültigen Repository

Wenn diese vier Punkte funktionieren, ist der restliche MVP überwiegend geradlinige
Produkt- und Integrationsarbeit.
