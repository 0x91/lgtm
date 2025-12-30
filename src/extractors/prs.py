"""Pull request data extractor."""

from datetime import datetime

from ..models import PullRequest
from ..module_config import ModuleConfig


# Module-level config instance for bot detection
# This gets set when the extractor is initialized
_config: ModuleConfig | None = None


def set_config(config: ModuleConfig) -> None:
    """Set the module config for bot detection."""
    global _config
    _config = config


def get_config() -> ModuleConfig:
    """Get the module config, using defaults if not set."""
    global _config
    if _config is None:
        _config = ModuleConfig.default()
    return _config


def is_bot(user: dict) -> bool:
    """Check if user is a bot/GitHub App.

    Uses the module config's is_bot method which supports:
    - GitHub API user type ("Bot")
    - Glob patterns (default: *[bot])
    - Explicit login list from config
    """
    config = get_config()
    login = user.get("login", "")
    user_type = user.get("type")
    return config.is_bot(login, user_type)


def get_bot_name(login: str) -> str | None:
    """Extract bot name from login."""
    config = get_config()
    return config.get_bot_name(login)


def parse_datetime(dt_str: str | None) -> datetime | None:
    """Parse ISO datetime string, returns None if input is empty."""
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def parse_datetime_required(dt_str: str) -> datetime:
    """Parse ISO datetime string, raises if input is empty."""
    if not dt_str:
        raise ValueError("datetime string is required")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def extract_pr(pr_data: dict) -> PullRequest:
    """Extract PR data from GitHub API response."""
    user = pr_data.get("user", {})

    return PullRequest(
        pr_number=pr_data["number"],
        pr_id=pr_data["id"],
        title=pr_data["title"],
        body=pr_data.get("body"),
        author_login=user.get("login", "unknown"),
        author_id=user.get("id", 0),
        author_is_bot=is_bot(user),
        state=pr_data["state"],
        merged=pr_data.get("merged", False) or pr_data.get("merged_at") is not None,
        created_at=parse_datetime_required(pr_data["created_at"]),
        updated_at=parse_datetime_required(pr_data["updated_at"]),
        merged_at=parse_datetime(pr_data.get("merged_at")),
        closed_at=parse_datetime(pr_data.get("closed_at")),
        additions=pr_data.get("additions", 0),
        deletions=pr_data.get("deletions", 0),
        changed_files=pr_data.get("changed_files", 0),
        commits=pr_data.get("commits", 0),
        comments_count=pr_data.get("comments", 0),
        review_comments_count=pr_data.get("review_comments", 0),
        draft=pr_data.get("draft", False),
        merge_commit_sha=pr_data.get("merge_commit_sha"),
    )
