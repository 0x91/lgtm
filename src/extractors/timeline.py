"""PR timeline events extractor."""

from typing import Optional

from ..models import TimelineEvent
from .prs import parse_datetime


# Timeline events we care about
RELEVANT_EVENTS = {
    "ready_for_review",
    "reviewed",
    "merged",
    "closed",
    "reopened",
    "converted_to_draft",
    "review_requested",
    "review_request_removed",
    "assigned",
    "unassigned",
}


def extract_timeline_event(pr_number: int, event_data: dict) -> Optional[TimelineEvent]:
    """Extract timeline event from GitHub API response.

    Returns None for events we don't care about.
    """
    event_type = event_data.get("event")

    if event_type not in RELEVANT_EVENTS:
        return None

    # Actor can be in different fields depending on event type
    actor = event_data.get("actor") or event_data.get("user") or {}
    actor_login = actor.get("login") if isinstance(actor, dict) else None

    # Timestamp can be in different fields
    created_at = (
        event_data.get("created_at")
        or event_data.get("submitted_at")
        or event_data.get("committed_at")
    )

    if not created_at:
        return None

    return TimelineEvent(
        pr_number=pr_number,
        event_type=event_type,
        actor_login=actor_login,
        created_at=parse_datetime(created_at),
    )
