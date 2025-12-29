"""Tests for GitHub client."""

import pytest
import httpx
import respx


class TestGitHubClient:
    """Test GitHubClient with mocked HTTP."""

    @respx.mock
    def test_get_request(self, github_client):
        respx.get("https://api.github.com/repos/test/repo").mock(
            return_value=httpx.Response(200, json={"id": 123, "name": "repo"})
        )
        result = github_client.get("/repos/test/repo")
        assert result["id"] == 123
        assert github_client.request_count == 1

    @respx.mock
    def test_pagination(self, github_client, monkeypatch):
        monkeypatch.setattr("src.github_client.PER_PAGE", 2)
        respx.get("https://api.github.com/items").mock(
            side_effect=[
                httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
                httpx.Response(200, json=[{"id": 3}]),
                httpx.Response(200, json=[]),
            ]
        )
        results = list(github_client.paginate("/items"))
        assert len(results) == 3

    @respx.mock
    def test_rate_limit_handling(self, github_client):
        import time
        respx.get("https://api.github.com/test").mock(
            side_effect=[
                httpx.Response(403, json={"message": "rate limit"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1)}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = github_client.get("/test")
        assert result["ok"] is True

    @respx.mock
    def test_server_error_retry(self, github_client):
        respx.get("https://api.github.com/flaky").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(200, json={"recovered": True}),
            ]
        )
        result = github_client.get("/flaky")
        assert result["recovered"] is True

    def test_auth_type_pat(self, github_client):
        assert github_client.auth_type == "pat"

    def test_missing_auth_raises(self, monkeypatch):
        from src.github_client import GitHubClient
        monkeypatch.setattr("src.github_client.GITHUB_TOKEN", None)
        monkeypatch.setattr("src.github_client.GITHUB_APP_ID", None)
        with pytest.raises(ValueError, match="GitHub auth required"):
            GitHubClient()

    def test_handle_rate_limit_returns_false_for_success(self, github_client):
        response = httpx.Response(200, json={})
        assert github_client._handle_rate_limit(response) is False

    def test_handle_rate_limit_returns_false_for_other_403(self, github_client):
        response = httpx.Response(403, json={"message": "forbidden"}, headers={"X-RateLimit-Remaining": "100"})
        assert github_client._handle_rate_limit(response) is False
