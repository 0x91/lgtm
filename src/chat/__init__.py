"""AI chat interface for code review analysis.

Provides a TUI chat interface powered by LLMs (Claude/OpenAI/Gemini)
for exploring code review patterns conversationally.

Requires optional dependencies: pip install lgtm[ai]
"""

from .agent import LGTMAgent
from .tui import ChatTUI

__all__ = ["LGTMAgent", "ChatTUI"]
