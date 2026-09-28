from __future__ import annotations

from collections.abc import Mapping, Set

from workbench.domain.models import (
    BugItem,
    BugStatus,
    DecisionItem,
    DecisionStatus,
    FeatureItem,
    FeatureStatus,
    IdeaItem,
    IdeaStatus,
    TaskItem,
    TaskStatus,
)


class TransitionNotAllowed(ValueError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"transition from {current!r} to {target!r} is not allowed")
        self.current = current
        self.target = target


TASK_TRANSITIONS: Mapping[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.BACKLOG: {
        TaskStatus.READY,
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.REVIEW,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    },
    TaskStatus.READY: {
        TaskStatus.BACKLOG,
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.BLOCKED,
        TaskStatus.REVIEW,
        TaskStatus.DONE,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    },
    TaskStatus.REVIEW: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.DONE,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    },
    TaskStatus.DONE: {TaskStatus.ARCHIVED},
    TaskStatus.CANCELLED: {TaskStatus.ARCHIVED},
    TaskStatus.ARCHIVED: set(),
}

IDEA_TRANSITIONS: Mapping[IdeaStatus, Set[IdeaStatus]] = {
    IdeaStatus.INBOX: {IdeaStatus.EVALUATING, IdeaStatus.REJECTED, IdeaStatus.ARCHIVED},
    IdeaStatus.EVALUATING: {
        IdeaStatus.INBOX,
        IdeaStatus.ACCEPTED,
        IdeaStatus.REJECTED,
        IdeaStatus.ARCHIVED,
    },
    IdeaStatus.ACCEPTED: {
        IdeaStatus.PROMOTED,
        IdeaStatus.REJECTED,
        IdeaStatus.ARCHIVED,
    },
    IdeaStatus.REJECTED: {IdeaStatus.EVALUATING, IdeaStatus.ARCHIVED},
    IdeaStatus.PROMOTED: {IdeaStatus.ARCHIVED},
    IdeaStatus.ARCHIVED: set(),
}

BUG_TRANSITIONS: Mapping[BugStatus, Set[BugStatus]] = {
    BugStatus.REPORTED: {
        BugStatus.TRIAGED,
        BugStatus.IN_PROGRESS,
        BugStatus.BLOCKED,
        BugStatus.REVIEW,
        BugStatus.REJECTED,
        BugStatus.ARCHIVED,
    },
    BugStatus.TRIAGED: {
        BugStatus.IN_PROGRESS,
        BugStatus.REJECTED,
        BugStatus.ARCHIVED,
    },
    BugStatus.IN_PROGRESS: {
        BugStatus.BLOCKED,
        BugStatus.REVIEW,
        BugStatus.FIXED,
        BugStatus.REJECTED,
        BugStatus.ARCHIVED,
    },
    BugStatus.BLOCKED: {
        BugStatus.IN_PROGRESS,
        BugStatus.REVIEW,
        BugStatus.REJECTED,
        BugStatus.ARCHIVED,
    },
    BugStatus.REVIEW: {
        BugStatus.IN_PROGRESS,
        BugStatus.BLOCKED,
        BugStatus.FIXED,
        BugStatus.REJECTED,
        BugStatus.ARCHIVED,
    },
    BugStatus.FIXED: {BugStatus.CLOSED, BugStatus.IN_PROGRESS, BugStatus.ARCHIVED},
    BugStatus.CLOSED: {BugStatus.ARCHIVED},
    BugStatus.REJECTED: {BugStatus.TRIAGED, BugStatus.ARCHIVED},
    BugStatus.ARCHIVED: set(),
}

FEATURE_TRANSITIONS: Mapping[FeatureStatus, Set[FeatureStatus]] = {
    FeatureStatus.PROPOSED: {
        FeatureStatus.PLANNED,
        FeatureStatus.IN_PROGRESS,
        FeatureStatus.REVIEW,
        FeatureStatus.CANCELLED,
        FeatureStatus.ARCHIVED,
    },
    FeatureStatus.PLANNED: {
        FeatureStatus.PROPOSED,
        FeatureStatus.IN_PROGRESS,
        FeatureStatus.CANCELLED,
        FeatureStatus.ARCHIVED,
    },
    FeatureStatus.IN_PROGRESS: {
        FeatureStatus.REVIEW,
        FeatureStatus.DELIVERED,
        FeatureStatus.CANCELLED,
        FeatureStatus.ARCHIVED,
    },
    FeatureStatus.REVIEW: {
        FeatureStatus.IN_PROGRESS,
        FeatureStatus.DELIVERED,
        FeatureStatus.CANCELLED,
        FeatureStatus.ARCHIVED,
    },
    FeatureStatus.DELIVERED: {FeatureStatus.ARCHIVED},
    FeatureStatus.CANCELLED: {FeatureStatus.ARCHIVED},
    FeatureStatus.ARCHIVED: set(),
}

DECISION_TRANSITIONS: Mapping[DecisionStatus, Set[DecisionStatus]] = {
    DecisionStatus.PROPOSED: {
        DecisionStatus.ACCEPTED,
        DecisionStatus.REJECTED,
        DecisionStatus.ARCHIVED,
    },
    DecisionStatus.ACCEPTED: {DecisionStatus.SUPERSEDED, DecisionStatus.ARCHIVED},
    DecisionStatus.SUPERSEDED: {DecisionStatus.ARCHIVED},
    DecisionStatus.REJECTED: {DecisionStatus.PROPOSED, DecisionStatus.ARCHIVED},
    DecisionStatus.ARCHIVED: set(),
}

ItemWithLifecycle = TaskItem | BugItem | FeatureItem | IdeaItem | DecisionItem


def transitions_for(item: ItemWithLifecycle) -> Mapping[object, Set[object]]:
    if isinstance(item, TaskItem):
        return TASK_TRANSITIONS
    if isinstance(item, BugItem):
        return BUG_TRANSITIONS
    if isinstance(item, FeatureItem):
        return FEATURE_TRANSITIONS
    if isinstance(item, IdeaItem):
        return IDEA_TRANSITIONS
    return DECISION_TRANSITIONS


def ensure_transition(
    item: ItemWithLifecycle,
    target: str,
) -> TaskStatus | BugStatus | FeatureStatus | IdeaStatus | DecisionStatus:
    status_type = type(item.status)
    target_status = status_type(target)
    allowed = transitions_for(item)[item.status]

    if target_status not in allowed:
        raise TransitionNotAllowed(item.status.value, target_status.value)
    return target_status
