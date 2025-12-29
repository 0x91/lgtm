"""PR review data extractor."""

from ..models import Review
from .prs import is_bot, parse_datetime_required


def extract_review(pr_number: int, review_data: dict) -> Review:
    """Extract review data from GitHub API response."""
    user = review_data.get("user", {})

    return Review(
        review_id=review_data["id"],
        pr_number=pr_number,
        reviewer_login=user.get("login", "unknown"),
        reviewer_id=user.get("id", 0),
        reviewer_is_bot=is_bot(user),
        state=review_data["state"],
        body=review_data.get("body"),
        submitted_at=parse_datetime_required(review_data["submitted_at"]),
        commit_id=review_data.get("commit_id", ""),
    )
