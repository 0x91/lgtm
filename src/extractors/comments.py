"""Comment data extractors (PR comments and review comments)."""

from ..models import PRComment, ReviewComment
from .prs import is_bot, parse_datetime


def extract_pr_comment(pr_number: int, comment_data: dict) -> PRComment:
    """Extract PR-level comment from GitHub API response."""
    user = comment_data.get("user", {})
    reactions = comment_data.get("reactions", {})

    return PRComment(
        comment_id=comment_data["id"],
        pr_number=pr_number,
        author_login=user.get("login", "unknown"),
        author_id=user.get("id", 0),
        author_is_bot=is_bot(user),
        body=comment_data.get("body", ""),
        created_at=parse_datetime(comment_data["created_at"]),
        updated_at=parse_datetime(comment_data["updated_at"]),
        reactions_total=reactions.get("total_count", 0),
    )


def extract_review_comment(pr_number: int, comment_data: dict) -> ReviewComment:
    """Extract inline code review comment from GitHub API response."""
    user = comment_data.get("user", {})

    return ReviewComment(
        comment_id=str(comment_data["id"]),
        pr_number=pr_number,
        author_login=user.get("login", "unknown"),
        author_is_bot=is_bot(user),
        body=comment_data.get("body", ""),
        path=comment_data.get("path", ""),
        line=comment_data.get("line") or comment_data.get("original_line"),
        created_at=parse_datetime(comment_data["created_at"]),
        updated_at=parse_datetime(comment_data["updated_at"]),
        # These aren't directly in REST API, set defaults
        is_resolved=False,
        is_outdated=comment_data.get("position") is None,
    )
