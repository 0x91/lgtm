"""Tests for data extractors."""

import pytest
from datetime import datetime, timezone

from src.extractors.files import extract_module, extract_file_change
from src.extractors.prs import is_bot, parse_datetime, extract_pr
from src.extractors.users import extract_user


class TestExtractModule:
    """Test module path extraction from file paths."""

    @pytest.mark.parametrize("path,expected", [
        # Backend with language subdir
        ("backend/py/cogna-tools/main.py", "backend/py/cogna-tools"),
        ("backend/go-servers/apiserver/cmd/main.go", "backend/go-servers/apiserver"),
        ("backend/ts/shared-lib/src/index.ts", "backend/ts/shared-lib"),

        # Frontend
        ("frontend/ts/webapp/src/App.tsx", "frontend/ts/webapp"),
        ("frontend/react/dashboard/components/Header.tsx", "frontend/react/dashboard"),

        # App runtime
        ("app-runtime/py/worker/handler.py", "app-runtime/py/worker"),

        # Proto
        ("proto/user/v1/user.proto", "proto/user"),
        ("proto/common/types.proto", "proto/common"),

        # Charts
        ("charts/webapp/values.yaml", "charts/webapp"),
        ("charts/api-gateway/templates/deployment.yaml", "charts/api-gateway"),

        # Packages
        ("frontend-packages/ui-kit/src/Button.tsx", "frontend-packages/ui-kit"),
        ("shared-packages/utils/index.ts", "shared-packages/utils"),

        # Edge cases
        ("backend/py", "backend/py"),
        ("backend", "backend"),
        ("README.md", "README.md"),
        ("", "root"),
        (".github/workflows/ci.yml", ".github"),
    ])
    def test_extract_module(self, path: str, expected: str):
        assert extract_module(path) == expected


class TestIsBot:
    """Test bot detection."""

    def test_github_app_bot(self):
        user = {"login": "dependabot[bot]", "type": "Bot", "id": 12345}
        assert is_bot(user) is True

    def test_bot_suffix_without_type(self):
        user = {"login": "renovate[bot]", "id": 12345}
        assert is_bot(user) is True

    def test_human_user(self):
        user = {"login": "octocat", "type": "User", "id": 12345}
        assert is_bot(user) is False

    def test_organization(self):
        user = {"login": "github", "type": "Organization", "id": 12345}
        assert is_bot(user) is False

    def test_empty_user(self):
        assert is_bot({}) is False


class TestParseDatetime:
    """Test datetime parsing."""

    def test_iso_with_z(self):
        result = parse_datetime("2025-01-15T10:30:00Z")
        assert result == datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_iso_with_offset(self):
        result = parse_datetime("2025-01-15T10:30:00+00:00")
        assert result == datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_none(self):
        assert parse_datetime(None) is None

    def test_empty_string(self):
        assert parse_datetime("") is None


class TestExtractUser:
    """Test user extraction."""

    def test_human_user(self):
        user_data = {"id": 12345, "login": "octocat", "type": "User"}
        user = extract_user(user_data)

        assert user.user_id == 12345
        assert user.login == "octocat"
        assert user.is_bot is False
        assert user.bot_name is None

    def test_known_bot(self):
        user_data = {"id": 99999, "login": "cursor[bot]", "type": "Bot"}
        user = extract_user(user_data)

        assert user.login == "cursor[bot]"
        assert user.is_bot is True
        assert user.bot_name == "cursor"

    def test_unknown_bot(self):
        user_data = {"id": 88888, "login": "new-fancy-bot[bot]", "type": "Bot"}
        user = extract_user(user_data)

        assert user.is_bot is True
        assert user.bot_name == "new-fancy-bot"

    def test_missing_fields(self):
        user = extract_user({})
        assert user.login == "unknown"
        assert user.user_id == 0


class TestExtractPr:
    """Test PR extraction."""

    @pytest.fixture
    def sample_pr_data(self):
        return {
            "number": 123,
            "id": 999999,
            "title": "Fix bug in auth",
            "body": "This fixes the auth bug",
            "user": {"login": "octocat", "id": 12345, "type": "User"},
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

    def test_extract_complete_pr(self, sample_pr_data):
        pr = extract_pr(sample_pr_data)

        assert pr.pr_number == 123
        assert pr.title == "Fix bug in auth"
        assert pr.author_login == "octocat"
        assert pr.author_is_bot is False
        assert pr.merged is True
        assert pr.additions == 50
        assert pr.deletions == 20

    def test_extract_pr_with_bot_author(self, sample_pr_data):
        sample_pr_data["user"] = {"login": "renovate[bot]", "id": 88888, "type": "Bot"}
        pr = extract_pr(sample_pr_data)

        assert pr.author_is_bot is True
        assert pr.author_login == "renovate[bot]"

    def test_merged_inferred_from_merged_at(self, sample_pr_data):
        sample_pr_data["merged"] = False
        sample_pr_data["merged_at"] = "2025-01-12T14:00:00Z"
        pr = extract_pr(sample_pr_data)

        assert pr.merged is True


class TestExtractFileChange:
    """Test file change extraction."""

    def test_basic_file(self):
        file_data = {
            "filename": "backend/py/api/handler.py",
            "status": "modified",
            "additions": 10,
            "deletions": 5,
            "changes": 15,
        }
        fc = extract_file_change(123, file_data)

        assert fc.pr_number == 123
        assert fc.filename == "backend/py/api/handler.py"
        assert fc.module == "backend/py/api"
        assert fc.additions == 10

    def test_defaults(self):
        fc = extract_file_change(123, {"filename": "README.md"})

        assert fc.status == "modified"
        assert fc.additions == 0
        assert fc.deletions == 0
