"""Tests for data extractors."""

from datetime import UTC, datetime

import pytest

from src.extractors.checks import extract_check_run
from src.extractors.comments import extract_pr_comment, extract_review_comment
from src.extractors.files import extract_file_change, extract_module
from src.extractors.prs import extract_pr, is_bot, parse_datetime
from src.extractors.reviews import extract_review
from src.extractors.timeline import RELEVANT_EVENTS, extract_timeline_event
from src.extractors.users import extract_user


# Factory helpers
def make_user(**overrides) -> dict:
    base = {"id": 12345, "login": "octocat", "type": "User"}
    base.update(overrides)
    return base


def make_pr_data(**overrides) -> dict:
    base = {
        "number": 123,
        "id": 999999,
        "title": "Fix bug",
        "body": "Desc",
        "user": make_user(),
        "state": "closed",
        "merged": True,
        "created_at": "2025-01-10T09:00:00Z",
        "updated_at": "2025-01-12T14:30:00Z",
        "merged_at": "2025-01-12T14:00:00Z",
        "closed_at": "2025-01-12T14:00:00Z",
        "additions": 50,
        "deletions": 20,
        "changed_files": 3,
        "commits": 2,
        "comments": 5,
        "review_comments": 10,
        "draft": False,
        "merge_commit_sha": "abc123",
    }
    base.update(overrides)
    return base


def make_review_data(**overrides) -> dict:
    base = {
        "id": 111,
        "user": make_user(login="reviewer"),
        "state": "APPROVED",
        "body": "",
        "submitted_at": "2025-01-11T10:00:00Z",
        "commit_id": "abc123",
    }
    base.update(overrides)
    return base


def make_comment_data(**overrides) -> dict:
    base = {
        "id": 222,
        "user": make_user(),
        "body": "Great work!",
        "created_at": "2025-01-11T10:00:00Z",
        "updated_at": "2025-01-11T10:00:00Z",
        "reactions": {"total_count": 3},
    }
    base.update(overrides)
    return base


def make_review_comment_data(**overrides) -> dict:
    base = {
        "id": 333,
        "user": make_user(),
        "body": "Consider refactoring",
        "path": "src/main.py",
        "line": 42,
        "original_line": 40,
        "position": 10,
        "created_at": "2025-01-11T10:00:00Z",
        "updated_at": "2025-01-11T10:00:00Z",
    }
    base.update(overrides)
    return base


def make_check_data(**overrides) -> dict:
    base = {
        "id": 444,
        "name": "CI / Build",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2025-01-11T10:00:00Z",
        "completed_at": "2025-01-11T10:05:00Z",
    }
    base.update(overrides)
    return base


def make_timeline_event(**overrides) -> dict:
    base = {"event": "ready_for_review", "actor": make_user(), "created_at": "2025-01-11T10:00:00Z"}
    base.update(overrides)
    return base


class TestExtractModule:
    """Test module path extraction from file paths.

    Uses default config patterns: src/{name}, packages/{name}, apps/{name}, .github
    """

    @pytest.fixture(autouse=True)
    def reset_config(self):
        """Reset module config singleton before each test."""
        from src.extractors.files import set_module_config
        from src.module_config import ModuleConfig

        set_module_config(ModuleConfig.default())

    @pytest.mark.parametrize(
        "path,expected",
        [
            # Default patterns
            ("src/utils/helper.py", "src/utils"),
            ("src/main.py", "src/main.py"),  # {name} captures filename
            ("packages/ui-kit/Button.tsx", "packages/ui-kit"),
            ("apps/web/pages/index.tsx", "apps/web"),
            (".github/workflows/ci.yml", ".github"),
            # Root files
            ("README.md", "root"),
            (".gitignore", "root"),
            ("", "root"),
            # Fallback to default_depth=2
            ("backend/py/tools/main.py", "backend/py"),
            ("some/deep/nested/file.py", "some/deep"),
        ],
    )
    def test_extract_module(self, path: str, expected: str):
        assert extract_module(path) == expected


class TestIsBot:
    """Test bot detection."""

    def test_github_app_bot(self):
        assert is_bot({"login": "dependabot[bot]", "type": "Bot"}) is True

    def test_bot_suffix_without_type(self):
        assert is_bot({"login": "renovate[bot]"}) is True

    def test_bot_type_without_suffix(self):
        assert is_bot({"login": "someservice", "type": "Bot"}) is True

    def test_human_user(self):
        assert is_bot({"login": "octocat", "type": "User"}) is False

    def test_empty_user(self):
        assert is_bot({}) is False


class TestParseDatetime:
    """Test datetime parsing."""

    def test_iso_with_z(self):
        result = parse_datetime("2025-01-15T10:30:00Z")
        assert result == datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_iso_with_offset(self):
        result = parse_datetime("2025-01-15T10:30:00+00:00")
        assert result == datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_none_returns_none(self):
        assert parse_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert parse_datetime("") is None


class TestExtractUser:
    """Test user extraction."""

    def test_human_user(self):
        user = extract_user(make_user())
        assert user.login == "octocat"
        assert user.is_bot is False
        assert user.bot_name is None

    def test_known_bot(self):
        user = extract_user(make_user(login="cursor[bot]", type="Bot"))
        assert user.is_bot is True
        assert user.bot_name == "cursor"

    def test_unknown_bot(self):
        user = extract_user(make_user(login="new-bot[bot]", type="Bot"))
        assert user.is_bot is True
        assert user.bot_name == "new-bot"

    def test_missing_fields_defaults(self):
        user = extract_user({})
        assert user.login == "unknown"
        assert user.user_id == 0


class TestExtractPr:
    """Test PR extraction."""

    def test_basic_pr(self):
        pr = extract_pr(make_pr_data())
        assert pr.pr_number == 123
        assert pr.author_login == "octocat"
        assert pr.author_is_bot is False
        assert pr.merged is True

    def test_bot_author(self):
        pr = extract_pr(make_pr_data(user=make_user(login="renovate[bot]", type="Bot")))
        assert pr.author_is_bot is True

    def test_merged_inferred_from_merged_at(self):
        pr = extract_pr(make_pr_data(merged=False, merged_at="2025-01-12T14:00:00Z"))
        assert pr.merged is True


class TestExtractFileChange:
    """Test file change extraction."""

    @pytest.fixture(autouse=True)
    def reset_config(self):
        """Reset module config singleton before each test."""
        from src.extractors.files import set_module_config
        from src.module_config import ModuleConfig

        set_module_config(ModuleConfig.default())

    def test_basic_file(self):
        # Uses default_depth=2 fallback for unmatched paths
        fc = extract_file_change(
            123,
            {
                "filename": "backend/py/api/handler.py",
                "status": "modified",
                "additions": 10,
                "deletions": 5,
                "changes": 15,
            },
        )
        assert fc.pr_number == 123
        assert fc.module == "backend/py"  # default_depth=2
        assert fc.additions == 10

    def test_defaults(self):
        fc = extract_file_change(123, {"filename": "README.md"})
        assert fc.status == "modified"
        assert fc.additions == 0
        assert fc.module == "root"  # Root file


class TestExtractReview:
    """Test review extraction."""

    def test_basic_approval(self):
        review = extract_review(123, make_review_data())
        assert review.pr_number == 123
        assert review.reviewer_login == "reviewer"
        assert review.state == "APPROVED"
        assert review.reviewer_is_bot is False

    def test_bot_reviewer(self):
        review = extract_review(
            123, make_review_data(user=make_user(login="cursor[bot]", type="Bot"))
        )
        assert review.reviewer_is_bot is True

    def test_changes_requested(self):
        review = extract_review(
            123, make_review_data(state="CHANGES_REQUESTED", body="Please fix the bug")
        )
        assert review.state == "CHANGES_REQUESTED"
        assert review.body == "Please fix the bug"

    def test_missing_user_defaults(self):
        data = make_review_data()
        data["user"] = {}
        review = extract_review(123, data)
        assert review.reviewer_login == "unknown"
        assert review.reviewer_id == 0


class TestExtractPrComment:
    """Test PR-level comment extraction."""

    def test_basic_comment(self):
        comment = extract_pr_comment(123, make_comment_data())
        assert comment.pr_number == 123
        assert comment.body == "Great work!"
        assert comment.reactions_total == 3

    def test_no_reactions(self):
        comment = extract_pr_comment(123, make_comment_data(reactions={}))
        assert comment.reactions_total == 0

    def test_bot_author(self):
        comment = extract_pr_comment(
            123, make_comment_data(user=make_user(login="bot[bot]", type="Bot"))
        )
        assert comment.author_is_bot is True


class TestExtractReviewComment:
    """Test inline review comment extraction."""

    def test_basic_comment(self):
        comment = extract_review_comment(123, make_review_comment_data())
        assert comment.comment_id == "333"  # Converted to str
        assert comment.line == 42
        assert comment.path == "src/main.py"
        assert comment.is_outdated is False

    def test_line_falls_back_to_original_line(self):
        comment = extract_review_comment(
            123, make_review_comment_data(line=None, original_line=100)
        )
        assert comment.line == 100

    def test_outdated_when_position_none(self):
        comment = extract_review_comment(123, make_review_comment_data(position=None))
        assert comment.is_outdated is True


class TestExtractCheckRun:
    """Test CI check run extraction."""

    def test_completed_check(self):
        check = extract_check_run(123, make_check_data())
        assert check.name == "CI / Build"
        assert check.conclusion == "success"
        assert check.duration_seconds == 300  # 5 minutes

    def test_in_progress_check(self):
        check = extract_check_run(
            123, make_check_data(status="in_progress", conclusion=None, completed_at=None)
        )
        assert check.conclusion is None
        assert check.duration_seconds is None

    def test_no_start_time(self):
        check = extract_check_run(123, make_check_data(started_at=None, completed_at=None))
        assert check.duration_seconds is None


class TestExtractTimelineEvent:
    """Test timeline event extraction."""

    @pytest.mark.parametrize("event_type", RELEVANT_EVENTS)
    def test_relevant_events_extracted(self, event_type: str):
        event = extract_timeline_event(123, make_timeline_event(event=event_type))
        assert event is not None
        assert event.event_type == event_type

    def test_irrelevant_event_returns_none(self):
        event = extract_timeline_event(123, make_timeline_event(event="labeled"))
        assert event is None

    def test_missing_created_at_returns_none(self):
        data = make_timeline_event()
        del data["created_at"]
        event = extract_timeline_event(123, data)
        assert event is None

    def test_actor_from_user_field(self):
        data = make_timeline_event()
        del data["actor"]
        data["user"] = make_user(login="other")
        event = extract_timeline_event(123, data)
        assert event.actor_login == "other"

    def test_submitted_at_fallback(self):
        data = make_timeline_event(event="reviewed")
        del data["created_at"]
        data["submitted_at"] = "2025-01-11T11:00:00Z"
        event = extract_timeline_event(123, data)
        assert event is not None
