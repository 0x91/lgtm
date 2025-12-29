"""CI check runs extractor."""

from ..models import CheckRun
from .prs import parse_datetime


def extract_check_run(pr_number: int, check_data: dict) -> CheckRun:
    """Extract check run data from GitHub API response."""
    started_at = parse_datetime(check_data.get("started_at"))
    completed_at = parse_datetime(check_data.get("completed_at"))

    duration_seconds = None
    if started_at and completed_at:
        duration_seconds = int((completed_at - started_at).total_seconds())

    return CheckRun(
        check_id=check_data["id"],
        pr_number=pr_number,
        name=check_data.get("name", "unknown"),
        status=check_data.get("status", "unknown"),
        conclusion=check_data.get("conclusion"),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
    )
