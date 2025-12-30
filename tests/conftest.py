"""Shared test fixtures."""

import pytest


@pytest.fixture
def github_client_uninit():
    """Create an uninitialized GitHubClient with a fake PAT token.

    Use this for sync tests that don't need the async context manager.
    """
    from src.github_client import GitHubClient

    return GitHubClient(token="fake-token")
