"""User dimension extractor."""

from ..models import User
from .prs import get_bot_name, is_bot


def extract_user(user_data: dict) -> User:
    """Extract user data from GitHub API response."""
    login = user_data.get("login", "unknown")
    user_is_bot = is_bot(user_data)

    bot_name: str | None = None
    if user_is_bot:
        bot_name = get_bot_name(login)

    return User(
        user_id=user_data.get("id", 0),
        login=login,
        is_bot=user_is_bot,
        bot_name=bot_name,
    )
