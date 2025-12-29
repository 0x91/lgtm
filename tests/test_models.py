"""Tests for Pydantic models."""

import pytest
from datetime import datetime, timezone

from src.models import (
    PullRequest,
    Review,
    PRComment,
    ReviewComment,
    FileChange,
    CheckRun,
    TimelineEvent,
    User,
)


class TestPullRequest:
    """Test PullRequest model."""

    def test_complete_pr(self):
        pr = PullRequest(
            pr_number=123,
            pr_id=999999,
            title="Add feature",
            body="Description here",
            author_login="octocat",
            author_id=12345,
            author_is_bot=False,
            state="closed",
            merged=True,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            merged_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            closed_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            additions=100,
            deletions=50,
            changed_files=5,
            commits=3,
            comments_count=2,
            review_comments_count=10,
            draft=False,
            merge_commit_sha="abc123",
        )

        assert pr.pr_number == 123
        assert pr.merged is True
        assert pr.author_is_bot is False

    def test_optional_fields_none(self):
        pr = PullRequest(
            pr_number=123,
            pr_id=999999,
            title="Add feature",
            body=None,
            author_login="octocat",
            author_id=12345,
            author_is_bot=False,
            state="open",
            merged=False,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            merged_at=None,
            closed_at=None,
            additions=10,
            deletions=5,
            changed_files=1,
            commits=1,
            comments_count=0,
            review_comments_count=0,
            draft=True,
            merge_commit_sha=None,
        )

        assert pr.body is None
        assert pr.merged_at is None
        assert pr.draft is True


class TestReview:
    """Test Review model."""

    def test_approval(self):
        review = Review(
            review_id=111,
            pr_number=123,
            reviewer_login="reviewer",
            reviewer_id=54321,
            reviewer_is_bot=False,
            state="APPROVED",
            body="LGTM!",
            submitted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            commit_id="abc123",
        )

        assert review.state == "APPROVED"
        assert review.reviewer_is_bot is False

    def test_empty_body(self):
        review = Review(
            review_id=111,
            pr_number=123,
            reviewer_login="reviewer",
            reviewer_id=54321,
            reviewer_is_bot=False,
            state="APPROVED",
            body=None,
            submitted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            commit_id="abc123",
        )

        assert review.body is None


class TestUser:
    """Test User model."""

    def test_human_user(self):
        user = User(
            user_id=12345,
            login="octocat",
            is_bot=False,
            bot_name=None,
        )

        assert user.is_bot is False
        assert user.bot_name is None

    def test_bot_user(self):
        user = User(
            user_id=99999,
            login="dependabot[bot]",
            is_bot=True,
            bot_name="dependabot",
        )

        assert user.is_bot is True
        assert user.bot_name == "dependabot"


class TestFileChange:
    """Test FileChange model."""

    def test_file_change(self):
        fc = FileChange(
            pr_number=123,
            filename="src/main.py",
            status="modified",
            additions=10,
            deletions=5,
            changes=15,
            module="src",
        )

        assert fc.filename == "src/main.py"
        assert fc.changes == 15


class TestCheckRun:
    """Test CheckRun model."""

    def test_completed_check(self):
        check = CheckRun(
            check_id=777,
            pr_number=123,
            name="CI / Build",
            status="completed",
            conclusion="success",
            started_at=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2025, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
            duration_seconds=300,
        )

        assert check.conclusion == "success"
        assert check.duration_seconds == 300

    def test_in_progress_check(self):
        check = CheckRun(
            check_id=777,
            pr_number=123,
            name="CI / Build",
            status="in_progress",
            conclusion=None,
            started_at=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            completed_at=None,
            duration_seconds=None,
        )

        assert check.conclusion is None
        assert check.completed_at is None


class TestTimelineEvent:
    """Test TimelineEvent model."""

    def test_ready_for_review(self):
        event = TimelineEvent(
            pr_number=123,
            event_type="ready_for_review",
            actor_login="octocat",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        assert event.event_type == "ready_for_review"

    def test_event_without_actor(self):
        event = TimelineEvent(
            pr_number=123,
            event_type="merged",
            actor_login=None,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        assert event.actor_login is None
