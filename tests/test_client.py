"""Tests for GitHub client."""

import time

import httpx
import pytest
import respx

from src.github_client import GitHubClient


class TestGitHubClient:
    """Test GitHubClient with mocked HTTP."""

    @pytest.fixture
    async def github_client(self):
        """Create async GitHubClient for tests."""
        client = GitHubClient(token="fake-token")
        async with client:
            yield client

    @pytest.mark.trio
    @respx.mock
    async def test_get_request(self, github_client):
        respx.get("https://api.github.com/repos/test/repo").mock(
            return_value=httpx.Response(200, json={"id": 123, "name": "repo"})
        )
        result = await github_client.get("/repos/test/repo")
        assert result["id"] == 123
        assert github_client.request_count == 1

    @pytest.mark.trio
    @respx.mock
    async def test_pagination(self, github_client, monkeypatch):
        monkeypatch.setattr("src.github_client.PER_PAGE", 2)
        respx.get("https://api.github.com/items").mock(
            side_effect=[
                httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
                httpx.Response(200, json=[{"id": 3}]),
                httpx.Response(200, json=[]),
            ]
        )
        results = []
        async for item in github_client.paginate("/items"):
            results.append(item)
        assert len(results) == 3

    @pytest.mark.trio
    @respx.mock
    async def test_rate_limit_handling(self, github_client, monkeypatch, autojump_clock):
        respx.get("https://api.github.com/test").mock(
            side_effect=[
                httpx.Response(403, json={"message": "rate limit"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1)}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await github_client.get("/test")
        assert result["ok"] is True

    @pytest.mark.trio
    @respx.mock
    async def test_server_error_retry(self, github_client, autojump_clock):
        respx.get("https://api.github.com/flaky").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(200, json={"recovered": True}),
            ]
        )
        result = await github_client.get("/flaky")
        assert result["recovered"] is True

    def test_auth_type_pat(self, github_client_uninit):
        assert github_client_uninit.auth_type == "pat"

    def test_missing_auth_raises(self, monkeypatch):
        monkeypatch.setattr("src.github_client.GITHUB_TOKEN", None)
        monkeypatch.setattr("src.github_client.GITHUB_APP_ID", None)
        with pytest.raises(ValueError, match="GitHub auth required"):
            GitHubClient()

    @pytest.mark.trio
    @respx.mock
    async def test_handle_rate_limit_returns_false_for_success(self, github_client):
        response = httpx.Response(200, json={})
        assert await github_client._handle_rate_limit(response) is False

    @pytest.mark.trio
    @respx.mock
    async def test_handle_rate_limit_returns_false_for_other_403(self, github_client):
        response = httpx.Response(403, json={"message": "forbidden"}, headers={"X-RateLimit-Remaining": "100"})
        assert await github_client._handle_rate_limit(response) is False

    @pytest.mark.trio
    @respx.mock
    async def test_secondary_rate_limit_403_with_retry_after(self, github_client, autojump_clock):
        """Test that 403 with Retry-After header is handled as secondary rate limit."""
        respx.get("https://api.github.com/test").mock(
            side_effect=[
                httpx.Response(
                    403,
                    json={"message": "secondary rate limit"},
                    headers={"Retry-After": "2", "X-RateLimit-Remaining": "100"},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await github_client.get("/test")
        assert result["ok"] is True

    @pytest.mark.trio
    @respx.mock
    async def test_secondary_rate_limit_429_with_retry_after(self, github_client, autojump_clock):
        """Test that 429 with Retry-After header triggers proper wait."""
        respx.get("https://api.github.com/test").mock(
            side_effect=[
                httpx.Response(
                    429,
                    json={"message": "too many requests"},
                    headers={"Retry-After": "3"},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await github_client.get("/test")
        assert result["ok"] is True

    def test_rate_limit_tracking(self, github_client_uninit):
        """Test that rate limit properties have sensible defaults."""
        assert github_client_uninit.rate_limit_remaining == 5000
        assert github_client_uninit.rate_limit_reset == 0
