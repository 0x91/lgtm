"""Tests for AI chat functionality."""

import pytest


class TestChatAvailability:
    """Tests for chat module availability."""

    def test_ai_available(self):
        """Test that AI deps are available when installed."""
        from src.chat.agent import AI_AVAILABLE

        assert AI_AVAILABLE is True

    def test_agent_class_defined(self):
        """Test that LGTMAgent class is defined."""
        from src.chat.agent import LGTMAgent

        assert LGTMAgent is not None

    def test_tui_class_defined(self):
        """Test that ChatTUI class is defined."""
        from src.chat.tui import ChatTUI

        assert ChatTUI is not None


class TestAgentTools:
    """Tests for agent tool definitions."""

    def test_tools_defined(self):
        """Test that all tools are defined."""
        from src.chat.agent import TOOLS, TOOL_FUNCTIONS

        assert len(TOOLS) == 5
        assert "get_overview" in TOOL_FUNCTIONS
        assert "get_reviewer_stats" in TOOL_FUNCTIONS
        assert "get_author_stats" in TOOL_FUNCTIONS
        assert "get_red_flags" in TOOL_FUNCTIONS
        assert "query_sql" in TOOL_FUNCTIONS

    def test_system_prompt_contains_philosophy(self):
        """Test that system prompt includes compassionate review philosophy."""
        from src.chat.agent import SYSTEM_PROMPT

        assert "compassionate" in SYSTEM_PROMPT.lower()
        assert "context" in SYSTEM_PROMPT.lower()
        assert "shame" in SYSTEM_PROMPT.lower() or "accuse" in SYSTEM_PROMPT.lower()


class TestAgentInit:
    """Tests for LGTMAgent initialization."""

    def test_default_model(self):
        """Test default model is set."""
        from src.chat.agent import LGTMAgent

        agent = LGTMAgent()
        assert agent.model == "claude-sonnet-4-20250514"

    def test_custom_model(self):
        """Test custom model can be set."""
        from src.chat.agent import LGTMAgent

        agent = LGTMAgent(model="gpt-4o")
        assert agent.model == "gpt-4o"

    def test_system_prompt_in_messages(self):
        """Test that system prompt is first message."""
        from src.chat.agent import LGTMAgent

        agent = LGTMAgent()
        assert len(agent.messages) == 1
        assert agent.messages[0]["role"] == "system"

    def test_custom_context_appended(self):
        """Test that custom context is added to system prompt."""
        from src.chat.agent import LGTMAgent

        context = "Our team does async reviews."
        agent = LGTMAgent(custom_context=context)
        assert context in agent.messages[0]["content"]

    def test_reset_clears_history(self):
        """Test that reset keeps only system prompt."""
        from src.chat.agent import LGTMAgent

        agent = LGTMAgent()
        # Simulate some messages
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "response"})
        assert len(agent.messages) == 3

        agent.reset()
        assert len(agent.messages) == 1
        assert agent.messages[0]["role"] == "system"

    def test_get_history_filters_system(self):
        """Test that get_history excludes system messages."""
        from src.chat.agent import LGTMAgent

        agent = LGTMAgent()
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "response"})

        history = agent.get_history()
        assert len(history) == 2
        assert all(msg["role"] in ("user", "assistant") for msg in history)
