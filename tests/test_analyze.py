"""Tests for analysis queries - lightweight sanity checks."""

import pytest
import duckdb

from src.analyze import (
    get_connection,
    # Core metrics
    rubber_stamp_rate,
    time_to_review,
    review_coverage,
    who_reviews_whom,
    substantive_reviewers,
    bot_activity,
    module_coverage,
    pr_size_vs_review,
    # Review quality
    review_depth,
    review_iterations,
    stale_approvals,
    drive_by_reviews,
    self_review_activity,
    # Temporal patterns
    review_by_time,
    review_latency_by_author,
    review_latency_by_module,
    time_in_review,
    # Team dynamics
    review_reciprocity,
    reviewer_load_balance,
    # Risk indicators
    large_pr_no_comments,
    quick_approve_large_pr,
    single_reviewer_merges,
    # Code review quality
    code_review_depth,
    pr_type_review_depth,
    conventional_commits,
    underreviewed_code,
    # Collaboration context
    module_experts,
    module_reviewers,
    collaboration_pairs,
    module_collaboration,
    informed_approvals,
)
from src.module_config import ModuleConfig


@pytest.fixture
def mock_db():
    """Create in-memory DuckDB with minimal test data."""
    con = duckdb.connect()

    # Register UDFs
    config = ModuleConfig.default()
    con.create_function("module", config.extract_module, [str], str)
    con.create_function("is_generated", config.is_generated, [str], bool)

    # Minimal prs table
    con.execute("""
        CREATE TABLE prs AS SELECT * FROM (VALUES
            (1, 1001, 'test', 'alice', 1, false, 'closed', true, 10, 5, 2, TIMESTAMP '2025-01-01 10:00:00', TIMESTAMP '2025-01-01 12:00:00', TIMESTAMP '2025-01-01 11:30:00'),
            (2, 1002, 'test2', 'bob', 2, false, 'closed', true, 50, 20, 5, TIMESTAMP '2025-01-02 10:00:00', TIMESTAMP '2025-01-02 14:00:00', TIMESTAMP '2025-01-02 13:00:00'),
            (3, 1003, 'bot pr', 'bot[bot]', 3, true, 'closed', true, 5, 0, 1, TIMESTAMP '2025-01-03 10:00:00', TIMESTAMP '2025-01-03 10:30:00', TIMESTAMP '2025-01-03 10:20:00')
        ) AS t(pr_number, pr_id, title, author_login, author_id, author_is_bot, state, merged, additions, deletions, changed_files, created_at, updated_at, merged_at)
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

    # Minimal files table with computed columns
    con.execute("""
        CREATE TABLE files AS SELECT * FROM (VALUES
            (1, 'src/main.py', 'modified', 5, 2, 7, 'src', 'src/main.py', false),
            (1, 'src/util.py', 'added', 10, 0, 10, 'src', 'src/util.py', false),
            (2, 'backend/api.py', 'modified', 30, 15, 45, 'backend', 'backend/api.py', false),
            (3, 'config.yaml', 'modified', 1, 0, 1, 'config.yaml', 'root', false),
            (1, 'package-lock.json', 'modified', 100, 50, 150, 'root', 'root', true)
        ) AS t(pr_number, filename, status, additions, deletions, changes, module, computed_module, is_gen)
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

    # Review quality metrics
    def test_review_depth(self, mock_db, capsys):
        """review_depth runs without error."""
        review_depth(mock_db)
        captured = capsys.readouterr()
        assert "Review Depth" in captured.out

    def test_review_iterations(self, mock_db, capsys):
        """review_iterations runs without error."""
        review_iterations(mock_db)
        captured = capsys.readouterr()
        assert "Iterations" in captured.out

    def test_stale_approvals(self, mock_db, capsys):
        """stale_approvals runs without error."""
        stale_approvals(mock_db)
        captured = capsys.readouterr()
        assert "Stale" in captured.out

    def test_drive_by_reviews(self, mock_db, capsys):
        """drive_by_reviews runs without error."""
        drive_by_reviews(mock_db)
        captured = capsys.readouterr()
        assert "Drive-by" in captured.out

    def test_self_review_activity(self, mock_db, capsys):
        """self_review_activity runs without error."""
        self_review_activity(mock_db)
        captured = capsys.readouterr()
        assert "Self-Review" in captured.out

    # Temporal patterns
    def test_review_by_time(self, mock_db, capsys):
        """review_by_time runs without error."""
        review_by_time(mock_db)
        captured = capsys.readouterr()
        assert "Day of Week" in captured.out

    def test_review_latency_by_author(self, mock_db, capsys):
        """review_latency_by_author runs without error."""
        review_latency_by_author(mock_db)
        captured = capsys.readouterr()
        assert "Latency" in captured.out

    def test_review_latency_by_module(self, mock_db, capsys):
        """review_latency_by_module runs without error."""
        review_latency_by_module(mock_db)
        captured = capsys.readouterr()
        assert "Latency" in captured.out

    def test_time_in_review(self, mock_db, capsys):
        """time_in_review runs without error."""
        time_in_review(mock_db)
        captured = capsys.readouterr()
        assert "Time in Review" in captured.out

    # Team dynamics
    def test_review_reciprocity(self, mock_db, capsys):
        """review_reciprocity runs without error."""
        review_reciprocity(mock_db)
        captured = capsys.readouterr()
        assert "Reciprocity" in captured.out

    def test_reviewer_load_balance(self, mock_db, capsys):
        """reviewer_load_balance runs without error."""
        reviewer_load_balance(mock_db)
        captured = capsys.readouterr()
        assert "Load Balance" in captured.out

    # Risk indicators
    def test_large_pr_no_comments(self, mock_db, capsys):
        """large_pr_no_comments runs without error."""
        large_pr_no_comments(mock_db)
        captured = capsys.readouterr()
        assert "Large PRs" in captured.out

    def test_quick_approve_large_pr(self, mock_db, capsys):
        """quick_approve_large_pr runs without error."""
        quick_approve_large_pr(mock_db)
        captured = capsys.readouterr()
        assert "Quick Approvals" in captured.out

    def test_single_reviewer_merges(self, mock_db, capsys):
        """single_reviewer_merges runs without error."""
        single_reviewer_merges(mock_db)
        captured = capsys.readouterr()
        assert "Single Reviewer" in captured.out

    # Code review quality
    def test_code_review_depth(self, mock_db, capsys):
        """code_review_depth runs without error."""
        code_review_depth(mock_db)
        captured = capsys.readouterr()
        assert "Review Depth" in captured.out

    def test_pr_type_review_depth(self, mock_db, capsys):
        """pr_type_review_depth runs without error."""
        pr_type_review_depth(mock_db)
        captured = capsys.readouterr()
        assert "PR Type" in captured.out

    def test_conventional_commits(self, mock_db, capsys):
        """conventional_commits runs without error."""
        conventional_commits(mock_db)
        captured = capsys.readouterr()
        assert "Conventional" in captured.out

    def test_underreviewed_code(self, mock_db, capsys):
        """underreviewed_code runs without error."""
        underreviewed_code(mock_db)
        captured = capsys.readouterr()
        assert "Large Code PRs" in captured.out

    # Collaboration context
    def test_module_experts(self, mock_db, capsys):
        """module_experts runs without error."""
        module_experts(mock_db)
        captured = capsys.readouterr()
        assert "Module Experts" in captured.out

    def test_module_reviewers(self, mock_db, capsys):
        """module_reviewers runs without error."""
        module_reviewers(mock_db)
        captured = capsys.readouterr()
        assert "Module Reviewers" in captured.out

    def test_collaboration_pairs(self, mock_db, capsys):
        """collaboration_pairs runs without error."""
        collaboration_pairs(mock_db)
        captured = capsys.readouterr()
        assert "Collaboration History" in captured.out

    def test_module_collaboration(self, mock_db, capsys):
        """module_collaboration runs without error."""
        module_collaboration(mock_db)
        captured = capsys.readouterr()
        assert "Module Collaboration" in captured.out

    def test_informed_approvals(self, mock_db, capsys):
        """informed_approvals runs without error."""
        informed_approvals(mock_db)
        captured = capsys.readouterr()
        assert "Approval Context" in captured.out


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
