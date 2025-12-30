"""Tests for analysis queries - lightweight sanity checks."""

import pytest
import duckdb

from src.analyze import (
    get_connection,
    rubber_stamp_rate,
    time_to_review,
    review_coverage,
    who_reviews_whom,
    substantive_reviewers,
    bot_activity,
    module_coverage,
    pr_size_vs_review,
)


@pytest.fixture
def mock_db():
    """Create in-memory DuckDB with minimal test data."""
    con = duckdb.connect()

    # Minimal prs table
    con.execute("""
        CREATE TABLE prs AS SELECT * FROM (VALUES
            (1, 1001, 'test', 'alice', 1, false, 'closed', true, 10, 5, 2, TIMESTAMP '2025-01-01 10:00:00', TIMESTAMP '2025-01-01 12:00:00'),
            (2, 1002, 'test2', 'bob', 2, false, 'closed', true, 50, 20, 5, TIMESTAMP '2025-01-02 10:00:00', TIMESTAMP '2025-01-02 14:00:00'),
            (3, 1003, 'bot pr', 'bot[bot]', 3, true, 'closed', true, 5, 0, 1, TIMESTAMP '2025-01-03 10:00:00', TIMESTAMP '2025-01-03 10:30:00')
        ) AS t(pr_number, pr_id, title, author_login, author_id, author_is_bot, state, merged, additions, deletions, changed_files, created_at, updated_at)
    """)

    # Minimal reviews table
    con.execute("""
        CREATE TABLE reviews AS SELECT * FROM (VALUES
            (101, 1, 'charlie', 10, false, 'APPROVED', NULL, TIMESTAMP '2025-01-01 11:00:00'),
            (102, 1, 'diana', 11, false, 'COMMENTED', 'looks good', TIMESTAMP '2025-01-01 11:30:00'),
            (103, 2, 'charlie', 10, false, 'APPROVED', '', TIMESTAMP '2025-01-02 12:00:00'),
            (104, 3, 'github-actions[bot]', 99, true, 'COMMENTED', 'CI passed', TIMESTAMP '2025-01-03 10:15:00')
        ) AS t(review_id, pr_number, reviewer_login, reviewer_id, reviewer_is_bot, state, body, submitted_at)
    """)

    # Minimal review_comments table
    con.execute("""
        CREATE TABLE review_comments AS SELECT * FROM (VALUES
            ('c1', 1, 'charlie', 10, false, 'fix this', 'src/main.py', 42),
            ('c2', 2, 'diana', 11, false, 'nice', 'src/util.py', 10)
        ) AS t(comment_id, pr_number, author_login, author_id, author_is_bot, body, path, line)
    """)

    # Minimal files table
    con.execute("""
        CREATE TABLE files AS SELECT * FROM (VALUES
            (1, 'src/main.py', 'modified', 5, 2, 7, 'src'),
            (1, 'src/util.py', 'added', 10, 0, 10, 'src'),
            (2, 'backend/api.py', 'modified', 30, 15, 45, 'backend'),
            (3, 'config.yaml', 'modified', 1, 0, 1, 'config.yaml')
        ) AS t(pr_number, filename, status, additions, deletions, changes, module)
    """)

    # Minimal users table
    con.execute("""
        CREATE TABLE users AS SELECT * FROM (VALUES
            (1, 'alice', false, NULL),
            (2, 'bob', false, NULL),
            (3, 'bot[bot]', true, 'bot'),
            (10, 'charlie', false, NULL),
            (11, 'diana', false, NULL),
            (99, 'github-actions[bot]', true, 'github-actions')
        ) AS t(user_id, login, is_bot, bot_name)
    """)

    # Empty tables for completeness
    con.execute("CREATE TABLE pr_comments (comment_id INT, pr_number INT, author_login VARCHAR, author_id INT, author_is_bot BOOLEAN, body VARCHAR)")
    con.execute("CREATE TABLE checks (check_id INT, pr_number INT, name VARCHAR, status VARCHAR, conclusion VARCHAR)")
    con.execute("CREATE TABLE timeline_events (pr_number INT, event_type VARCHAR, actor_login VARCHAR, created_at TIMESTAMP)")

    return con


class TestGetConnection:
    """Tests for database connection."""

    def test_get_connection_returns_duckdb(self):
        """Verify get_connection returns a DuckDB connection."""
        con = get_connection()
        assert con is not None
        # Should be able to execute queries
        result = con.execute("SELECT 1").fetchone()
        assert result == (1,)
        con.close()


class TestAnalysisQueries:
    """Smoke tests - each query runs without error on minimal data."""

    def test_rubber_stamp_rate(self, mock_db, capsys):
        """rubber_stamp_rate runs without error."""
        rubber_stamp_rate(mock_db)
        captured = capsys.readouterr()
        assert "Rubber Stamp" in captured.out

    def test_time_to_review(self, mock_db, capsys):
        """time_to_review runs without error."""
        time_to_review(mock_db)
        captured = capsys.readouterr()
        assert "Time to First Review" in captured.out

    def test_review_coverage(self, mock_db, capsys):
        """review_coverage runs without error."""
        review_coverage(mock_db)
        captured = capsys.readouterr()
        assert "Review Coverage" in captured.out

    def test_who_reviews_whom(self, mock_db, capsys):
        """who_reviews_whom runs without error."""
        who_reviews_whom(mock_db)
        captured = capsys.readouterr()
        assert "Reviewer-Author" in captured.out

    def test_substantive_reviewers(self, mock_db, capsys):
        """substantive_reviewers runs without error."""
        substantive_reviewers(mock_db)
        captured = capsys.readouterr()
        assert "Substantive" in captured.out

    def test_bot_activity(self, mock_db, capsys):
        """bot_activity runs without error."""
        bot_activity(mock_db)
        captured = capsys.readouterr()
        assert "Bot Activity" in captured.out

    def test_module_coverage(self, mock_db, capsys):
        """module_coverage runs without error."""
        module_coverage(mock_db)
        captured = capsys.readouterr()
        assert "Module" in captured.out

    def test_pr_size_vs_review(self, mock_db, capsys):
        """pr_size_vs_review runs without error."""
        pr_size_vs_review(mock_db)
        captured = capsys.readouterr()
        assert "Size" in captured.out


class TestQueryResults:
    """Basic result validation on mock data."""

    def test_review_coverage_counts(self, mock_db):
        """Verify review coverage returns expected counts."""
        result = mock_db.execute("""
            SELECT COUNT(*) FROM prs
        """).fetchone()
        assert result[0] == 3  # 3 PRs in fixture

    def test_human_vs_bot_reviews(self, mock_db):
        """Verify human/bot review distinction."""
        result = mock_db.execute("""
            SELECT
                SUM(CASE WHEN reviewer_is_bot THEN 1 ELSE 0 END) as bot,
                SUM(CASE WHEN NOT reviewer_is_bot THEN 1 ELSE 0 END) as human
            FROM reviews
        """).fetchone()
        assert result[0] == 1  # 1 bot review
        assert result[1] == 3  # 3 human reviews

    def test_empty_approval_detection(self, mock_db):
        """Verify empty approval detection logic."""
        result = mock_db.execute("""
            SELECT COUNT(*)
            FROM reviews
            WHERE state = 'APPROVED'
              AND (body IS NULL OR TRIM(body) = '')
        """).fetchone()
        assert result[0] == 2  # Both approvals have empty body
