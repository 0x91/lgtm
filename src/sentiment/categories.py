"""Simple comment signals for code review analysis.

Keep this minimal - LLMs handle nuanced classification better.
Focus on obvious signals we can detect cheaply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Simple patterns for obvious signals only
_RUBBER_STAMP = re.compile(
    r"^\s*(lgtm|looks good|looks good to me|\+1|ship it|approved?|nice|great|awesome)\s*!?\s*$",
    re.IGNORECASE,
)
_HAS_CODE_BLOCK = re.compile(r"```")
_HAS_LINK = re.compile(r"https?://\S+")
_IS_QUESTION = re.compile(r"\?\s*$")
_IS_SHORT = 50  # characters


@dataclass
class CommentSignals:
    """Simple, obvious signals from comment text.

    These are cheap to compute and high-confidence.
    Nuanced analysis should use LLMs.
    """

    is_rubber_stamp: bool  # lgtm, +1, etc.
    has_code_block: bool  # suggests concrete fix
    has_link: bool  # references external docs
    is_question: bool  # ends with ?
    is_short: bool  # < 50 chars
    char_count: int
    word_count: int


def get_signals(text: str) -> CommentSignals:
    """Extract simple signals from comment text.

    Args:
        text: The comment text.

    Returns:
        CommentSignals with obvious patterns detected.
    """
    text = text.strip() if text else ""

    return CommentSignals(
        is_rubber_stamp=bool(_RUBBER_STAMP.match(text)),
        has_code_block=bool(_HAS_CODE_BLOCK.search(text)),
        has_link=bool(_HAS_LINK.search(text)),
        is_question=bool(_IS_QUESTION.search(text)),
        is_short=len(text) < _IS_SHORT,
        char_count=len(text),
        word_count=len(text.split()) if text else 0,
    )
