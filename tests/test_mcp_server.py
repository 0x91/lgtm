"""Tests for MCP server functionality."""

import pytest


class TestMCPServerAvailability:
    """Tests for MCP server availability checks."""

    def test_mcp_available(self):
        """Test that MCP is available when ai extras are installed."""
        from src.mcp_server import MCP_AVAILABLE

        assert MCP_AVAILABLE is True

    def test_tools_defined(self):
        """Test that all tool functions are defined."""
        from src.mcp_server import (
            get_author_stats,
            get_overview,
            get_red_flags,
            get_reviewer_stats,
            query_data,
        )

        assert callable(get_overview)
        assert callable(query_data)
        assert callable(get_red_flags)
        assert callable(get_reviewer_stats)
        assert callable(get_author_stats)


class TestMCPToolFunctions:
    """Tests for MCP tool functions (require database)."""

    def test_get_overview_no_db(self):
        """Test that get_overview raises error when no database exists."""
        from src.mcp_server import get_overview

        # Should raise RuntimeError since we have no database
        with pytest.raises(RuntimeError, match="No analysis database found"):
            get_overview()

    def test_query_data_no_db(self):
        """Test that query_data raises error when no database exists."""
        from src.mcp_server import query_data

        with pytest.raises(RuntimeError, match="No analysis database found"):
            query_data("SELECT 1")

    def test_get_red_flags_no_db(self):
        """Test that get_red_flags raises error when no database exists."""
        from src.mcp_server import get_red_flags

        with pytest.raises(RuntimeError, match="No analysis database found"):
            get_red_flags()

    def test_get_reviewer_stats_no_db(self):
        """Test that get_reviewer_stats raises error when no database exists."""
        from src.mcp_server import get_reviewer_stats

        with pytest.raises(RuntimeError, match="No analysis database found"):
            get_reviewer_stats("someuser")

    def test_get_author_stats_no_db(self):
        """Test that get_author_stats raises error when no database exists."""
        from src.mcp_server import get_author_stats

        with pytest.raises(RuntimeError, match="No analysis database found"):
            get_author_stats("someuser")
