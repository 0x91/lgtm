"""Tests for the narrative report generator."""

import pytest
import duckdb

from src.report import (
    format_pct,
    format_hours,
    analyze_review_substance,
    analyze_review_timing,
    analyze_review_load,
)
from src.module_config import ModuleConfig


class TestFormatters:
    """Test formatting functions."""

    def test_format_pct(self):
        assert format_pct(50.0) == "50.0%"
        assert format_pct(0.0) == "0.0%"
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

    # Files table
    con.execute("""
        CREATE TABLE files AS
        SELECT *, module(filename) as computed_module, is_generated(filename) as is_gen
        FROM (VALUES
            (1, 'src/main.py', 'modified', 50, 25, 75),
            (2, 'src/util.py', 'modified', 100, 50, 150)
        ) AS t(pr_number, filename, status, additions, deletions, changes)
    """)

    return con


class TestReportSections:
    """Test individual report sections."""

    def test_analyze_review_substance(self, mock_db):
        """Report generates substance analysis."""
        section = analyze_review_substance(mock_db)
        assert section.headline is not None
        assert "approval" in section.summary.lower() or "review" in section.summary.lower()

    def test_analyze_review_timing(self, mock_db):
        """Report generates timing analysis."""
        section = analyze_review_timing(mock_db)
        assert section.headline is not None
        assert "median" in section.summary.lower() or "wait" in section.summary.lower()

    def test_analyze_review_load(self, mock_db):
        """Report generates load analysis."""
        section = analyze_review_load(mock_db)
        assert section.headline is not None
        # Should mention reviewers
        assert "review" in section.summary.lower()
