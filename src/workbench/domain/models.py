from __future__ import annotations

import re
import secrets
import time
import uuid
from datetime import UTC, date, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


CANONICAL_RELATION_TYPES = frozenset(
    {
        "blocks",
        "parent_of",
        "decided_by",
        "relates_to",
        "documented_in",
        "spawned_from",
        "implements",
        "supersedes",
    }
)
INVERSE_RELATIONS = {
    "blocks": "blocked_by",
    "parent_of": "child_of",
    "decided_by": "decides",
    "relates_to": "related_from",
    "documented_in": "documents",
    "spawned_from": "spawned",
    "implements": "implemented_by",
    "supersedes": "superseded_by",
}


class StrEnum(str, Enum):
    pass


class Scope(StrEnum):
    PROJECT = "project"
    WORKSPACE = "workspace"


class ItemType(StrEnum):
    TASK = "task"
    BUG = "bug"
    FEATURE = "feature"
    IDEA = "idea"
    DECISION = "decision"

    @property
    def key_code(self) -> str:
        return {
            ItemType.TASK: "TASK",
            ItemType.BUG: "BUG",
            ItemType.FEATURE: "FEAT",
            ItemType.IDEA: "IDEA",
            ItemType.DECISION: "DEC",
        }[self]


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class IdeaStatus(StrEnum):
    INBOX = "inbox"
    EVALUATING = "evaluating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    ARCHIVED = "archived"


class BugStatus(StrEnum):
    REPORTED = "reported"
    TRIAGED = "triaged"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    FIXED = "fixed"
    CLOSED = "closed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class FeatureStatus(StrEnum):
    PROPOSED = "proposed"
    PLANNED = "planned"
    IN_PROGRESS = "in-progress"
    REVIEW = "review"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class Priority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


def new_uuid7() -> uuid.UUID:
    """Create an RFC 9562 UUIDv7 without requiring Python 3.14's uuid.uuid7()."""
    timestamp_ms = int(time.time() * 1000)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    )
    return uuid.UUID(int=value)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("title does not contain characters suitable for a slug")
    return slug


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    target: uuid.UUID

    @field_validator("type")
    @classmethod
    def type_is_canonical(cls, value: str) -> str:
        if value not in CANONICAL_RELATION_TYPES:
            raise ValueError(f"unknown canonical relation type {value!r}")
        return value


class BaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: uuid.UUID = Field(default_factory=new_uuid7)
    key: str
    type: ItemType
    title: str = Field(min_length=1)
    scope: Scope
    project: str | None = None
    status: str
    created: datetime = Field(default_factory=utc_now)
    updated: datetime = Field(default_factory=utc_now)
    created_by: str = Field(min_length=1)
    updated_by: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    rank: str | None = None
    relations: list[Relation] = Field(default_factory=list)
    archived_from: str | None = None

    @field_validator("id")
    @classmethod
    def id_must_be_uuid7(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 7:
            raise ValueError("id must be a UUIDv7")
        return value

    @field_validator("key")
    @classmethod
    def key_must_be_readable(cls, value: str) -> str:
        if not re.fullmatch(
            r"[A-Z][A-Z0-9]{1,7}-(TASK|BUG|FEAT|IDEA|DEC)-[0-9]{3,}", value
        ):
            raise ValueError(
                "key must look like MQ-TASK-001, MQ-FEAT-001, or WB-IDEA-001"
            )
        return value

    @field_validator("created", "updated")
    @classmethod
    def timestamps_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def scope_matches_project(self) -> BaseItem:
        if self.scope == Scope.PROJECT and not self.project:
            raise ValueError("project is required for project-scoped items")
        if self.scope == Scope.WORKSPACE and self.project is not None:
            raise ValueError("project must be omitted for workspace-scoped items")
        relation_keys = [
            (relation.type, relation.target) for relation in self.relations
        ]
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("relations must be unique by type and target")
        if any(relation.target == self.id for relation in self.relations):
            raise ValueError("an item cannot relate to itself")
        return self


class TaskItem(BaseItem):
    type: Literal[ItemType.TASK] = ItemType.TASK
    status: TaskStatus = TaskStatus.BACKLOG
    priority: Priority = Priority.MEDIUM
    assignee: str | None = None
    estimate: str | None = None
    due: date | None = None

    @field_validator("estimate")
    @classmethod
    def estimate_has_supported_unit(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[1-9][0-9]*(m|h|d|w)", value):
            raise ValueError("estimate must use <n>m, <n>h, <n>d, or <n>w")
        return value


class BugItem(BaseItem):
    type: Literal[ItemType.BUG] = ItemType.BUG
    status: BugStatus = BugStatus.REPORTED
    severity: Severity = Severity.MEDIUM
    priority: Priority = Priority.MEDIUM
    assignee: str | None = None
    affected_version: str | None = None


class FeatureItem(BaseItem):
    type: Literal[ItemType.FEATURE] = ItemType.FEATURE
    status: FeatureStatus = FeatureStatus.PROPOSED
    priority: Priority = Priority.MEDIUM
    owner: str | None = None
    target_release: str | None = None


class IdeaItem(BaseItem):
    type: Literal[ItemType.IDEA] = ItemType.IDEA
    status: IdeaStatus = IdeaStatus.INBOX
    score: int = 0
    promoted_to: str | None = None


class DecisionItem(BaseItem):
    type: Literal[ItemType.DECISION] = ItemType.DECISION
    status: DecisionStatus = DecisionStatus.PROPOSED
    supersedes: uuid.UUID | None = None
    owners: list[str] = Field(default_factory=list)


ItemModel = TaskItem | BugItem | FeatureItem | IdeaItem | DecisionItem
Item = Annotated[ItemModel, Field(discriminator="type")]
ITEM_ADAPTER = TypeAdapter(Item)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=new_uuid7)
    key: str
    slug: str
    title: str = Field(min_length=1)
    status: ProjectStatus = ProjectStatus.ACTIVE
    created: date = Field(default_factory=lambda: date.today())
    updated: date = Field(default_factory=lambda: date.today())

    @field_validator("id")
    @classmethod
    def id_must_be_uuid7(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 7:
            raise ValueError("id must be a UUIDv7")
        return value

    @field_validator("key")
    @classmethod
    def key_is_prefix(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", value):
            raise ValueError("project key must contain 2-8 uppercase letters or digits")
        return value

    @field_validator("slug")
    @classmethod
    def slug_is_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("slug must contain lowercase words separated by hyphens")
        return value


def parse_item(data: dict[str, object]) -> ItemModel:
    return ITEM_ADAPTER.validate_python(data)
