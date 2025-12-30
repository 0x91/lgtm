"""Tests for the narrative report generator."""

import pytest
import duckdb

from src.report import (
    format_pct,
    format_hours,
    format_minutes,
    get_summary_stats,
    get_approval_context,
    get_review_depth_by_type,
)
from src.module_config import ModuleConfig


class TestFormatters:
    """Test formatting functions."""

    def test_format_pct(self):
        assert format_pct(50.0) == "50%"
        assert format_pct(50.5) == "50.5%"
        assert format_pct(0.0) == "0%"
        assert format_pct(None) == "N/A"

    def test_format_hours_minutes(self):
        assert format_hours(0.5) == "30 min"
        assert format_hours(0.25) == "15 min"

    def test_format_hours_hours(self):
        assert format_hours(2.5) == "2.5 hrs"
        assert format_hours(12.0) == "12.0 hrs"

    def test_format_hours_days(self):
        assert format_hours(48.0) == "2.0 days"
        assert format_hours(72.0) == "3.0 days"

    def test_format_hours_none(self):
        assert format_hours(None) == "N/A"

    def test_format_minutes(self):
        assert format_minutes(5) == "5 min"
        assert format_minutes(0.5) == "<1 min"
        assert format_minutes(None) == "N/A"


@pytest.fixture
def mock_db():
    """Create in-memory DuckDB with test data for report."""
    con = duckdb.connect()

    # Register UDFs
    config = ModuleConfig.default()
    con.create_function("module", config.extract_module, [str], str)
    con.create_function("is_generated", config.is_generated, [str], bool)

    # PRs table
    con.execute("""
        CREATE TABLE prs AS SELECT * FROM (VALUES
            (1, 1001, 'test', 'alice', 1, false, 'closed', true, 100, 50, 5, TIMESTAMP '2025-01-01 10:00:00', TIMESTAMP '2025-01-01 12:00:00', TIMESTAMP '2025-01-01 11:30:00'),
            (2, 1002, 'test2', 'bob', 2, false, 'closed', true, 200, 100, 10, TIMESTAMP '2025-01-02 10:00:00', TIMESTAMP '2025-01-02 14:00:00', TIMESTAMP '2025-01-02 13:00:00')
        ) AS t(pr_number, pr_id, title, author_login, author_id, author_is_bot, state, merged, additions, deletions, changed_files, created_at, updated_at, merged_at)
    """)

    # Reviews table
    con.execute("""
        CREATE TABLE reviews AS SELECT * FROM (VALUES
            (101, 1, 'charlie', 10, false, 'APPROVED', NULL, TIMESTAMP '2025-01-01 11:00:00'),
            (102, 1, 'diana', 11, false, 'APPROVED', 'looks good', TIMESTAMP '2025-01-01 11:30:00'),
            (103, 2, 'charlie', 10, false, 'APPROVED', '', TIMESTAMP '2025-01-02 12:00:00')
        ) AS t(review_id, pr_number, reviewer_login, reviewer_id, reviewer_is_bot, state, body, submitted_at)
    """)

    # Review comments table
    con.execute("""
        CREATE TABLE review_comments AS SELECT * FROM (VALUES
            ('c1', 1, 'diana', 11, false, 'nice work', 'src/main.py', 42)
        ) AS t(comment_id, pr_number, author_login, author_id, author_is_bot, body, path, line)
    """)

    # Files table with precomputed columns
    con.execute("""
        CREATE TABLE files AS
        SELECT *, module(filename) as computed_module, is_generated(filename) as is_gen
        FROM (VALUES
            (1, 'src/main.py', 'modified', 50, 25, 75),
            (2, 'src/util.py', 'modified', 100, 50, 150)
        ) AS t(pr_number, filename, status, additions, deletions, changes)
    """)

    return con


class TestDataFetching:
    """Test data fetching functions."""

    def test_get_summary_stats(self, mock_db):
        """Summary stats returns expected structure."""
        stats = get_summary_stats(mock_db)
        assert stats["total_prs"] == 2
        assert stats["merged_prs"] == 2
        assert stats["first_pr"] is not None
        assert stats["last_pr"] is not None

    def test_get_approval_context(self, mock_db):
        """Approval context returns expected structure."""
        ctx = get_approval_context(mock_db)
        assert "total_approvals" in ctx
        assert "empty_approvals" in ctx
        assert "expert_approvals" in ctx
        assert "firsttime_approvals" in ctx

    def test_get_review_depth_by_type(self, mock_db):
        """Review depth returns list of dicts."""
        depth = get_review_depth_by_type(mock_db)
        assert isinstance(depth, list)
        # Each row should have expected keys
        for row in depth:
            assert "type" in row
            assert "prs" in row
            assert "avg_comments" in row
            assert "pct_feedback" in row
