"""Shared test fixtures."""

import pytest

from src.github_client import GitHubClient


@pytest.fixture
def github_client():
    """Create a GitHubClient with a fake PAT token."""
    client = GitHubClient(token="fake-token")
    yield client
    client.close()
