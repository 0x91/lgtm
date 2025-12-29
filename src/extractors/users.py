"""User dimension extractor."""


from ..config import KNOWN_BOTS
from ..models import User
from .prs import is_bot


def extract_user(user_data: dict) -> User:
    """Extract user data from GitHub API response."""
    login = user_data.get("login", "unknown")
    user_is_bot = is_bot(user_data)

    bot_name: str | None = None
    if user_is_bot:
        bot_name = KNOWN_BOTS.get(login)
        if not bot_name and login.endswith("[bot]"):
            bot_name = login.replace("[bot]", "")

    return User(
        user_id=user_data.get("id", 0),
        login=login,
        is_bot=user_is_bot,
        bot_name=bot_name,
    )
