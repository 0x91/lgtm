"""Tests for GitHub client."""

import pytest
import httpx
import respx

from src.github_client import GitHubClient


class TestGitHubClient:
    """Test GitHubClient with mocked HTTP."""

    @pytest.fixture
    def client(self):
        """Create a client with a fake PAT token."""
        client = GitHubClient(token="fake-token")
        yield client
        client.close()

    @respx.mock
    def test_get_request(self, client):
        respx.get("https://api.github.com/repos/test/repo").mock(
            return_value=httpx.Response(200, json={"id": 123, "name": "repo"})
        )

        result = client.get("/repos/test/repo")

        assert result["id"] == 123
        assert result["name"] == "repo"
        assert client.request_count == 1

    @respx.mock
    def test_pagination(self, client):
        # First page - full results
        respx.get("https://api.github.com/items").mock(
            side_effect=[
                httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
                httpx.Response(200, json=[{"id": 3}]),
                httpx.Response(200, json=[]),
            ]
        )

        # Override PER_PAGE for test
        import src.github_client
        original = src.github_client.PER_PAGE
        src.github_client.PER_PAGE = 2

        try:
            results = list(client.paginate("/items"))
        finally:
            src.github_client.PER_PAGE = original

        assert len(results) == 3
        assert results[0]["id"] == 1
        assert results[2]["id"] == 3

    @respx.mock
    def test_rate_limit_handling(self, client):
        import time

        # First request hits rate limit, second succeeds
        respx.get("https://api.github.com/test").mock(
            side_effect=[
                httpx.Response(
                    403,
                    json={"message": "rate limit"},
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + 1),
                    },
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        result = client.get("/test")
        assert result["ok"] is True
        assert client.request_count == 2

    @respx.mock
    def test_server_error_retry(self, client):
        respx.get("https://api.github.com/flaky").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(200, json={"recovered": True}),
            ]
        )

        result = client.get("/flaky")
        assert result["recovered"] is True

    def test_auth_type_pat(self, client):
        assert client.auth_type == "pat"

    def test_missing_auth_raises(self):
        import src.github_client

        # Temporarily clear env vars
        orig_token = src.github_client.GITHUB_TOKEN
        orig_app_id = src.github_client.GITHUB_APP_ID

        src.github_client.GITHUB_TOKEN = None
        src.github_client.GITHUB_APP_ID = None

        try:
            with pytest.raises(ValueError, match="GitHub auth required"):
                GitHubClient()
        finally:
            src.github_client.GITHUB_TOKEN = orig_token
            src.github_client.GITHUB_APP_ID = orig_app_id

    def test_context_manager(self):
        with GitHubClient(token="fake") as client:
            assert client.auth_type == "pat"
        # Client should be closed after context


class TestRateLimitParser:
    """Test rate limit header parsing."""

    @pytest.fixture
    def client(self):
        client = GitHubClient(token="fake-token")
        yield client
        client.close()

    def test_handle_rate_limit_returns_false_for_success(self, client):
        response = httpx.Response(200, json={})
        assert client._handle_rate_limit(response) is False

    def test_handle_rate_limit_returns_false_for_other_403(self, client):
        response = httpx.Response(
            403,
            json={"message": "forbidden"},
            headers={"X-RateLimit-Remaining": "100"},
        )
        assert client._handle_rate_limit(response) is False
