"""GitHub API client with rate limiting, retry logic, and App auth support.

Uses httpx.AsyncClient with trio for concurrent requests.
"""

import logging
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import trio

from .config import (
    GITHUB_APP_ID,
    GITHUB_APP_INSTALLATION_ID,
    GITHUB_APP_PRIVATE_KEY_PATH,
    GITHUB_TOKEN,
    PER_PAGE,
    REPO_NAME,
    REPO_OWNER,
)

logger = logging.getLogger(__name__)


class GitHubAppAuth:
    """GitHub App authentication manager with automatic token refresh."""

    def __init__(self, app_id: str, private_key_path: str, installation_id: str):
        self.app_id = app_id
        self.installation_id = installation_id

        # Load private key
        with open(private_key_path) as f:
            self.private_key = f.read()

        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._refresh_lock: trio.Lock | None = None  # Lazy init to avoid trio import at module level

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = datetime.now(UTC)
        payload = {
            "iat": int(now.timestamp()) - 60,  # Issued 60s ago (clock skew)
            "exp": int((now + timedelta(minutes=10)).timestamp()),  # Expires in 10 min
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _fetch_installation_token(self) -> tuple[str, datetime]:
        """Exchange JWT for an installation access token."""
        jwt_token = self._generate_jwt()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
            response.raise_for_status()

        data = response.json()
        token = data["token"]
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

        return token, expires_at

    async def get_token(self) -> str:
        """Get a valid installation token, refreshing if needed.

        Uses a lock to prevent multiple concurrent refresh attempts.
        """
        # Lazy init lock (can't create trio.Lock outside async context)
        if self._refresh_lock is None:
            self._refresh_lock = trio.Lock()

        now = datetime.now(UTC)

        # Quick check without lock - if token is valid, return it
        if (
            self._token is not None
            and self._token_expires_at is not None
            and self._token_expires_at > now + timedelta(minutes=5)
        ):
            return self._token

        # Need to refresh - acquire lock to prevent concurrent refreshes
        async with self._refresh_lock:
            # Re-check after acquiring lock (another task may have refreshed)
            now = datetime.now(UTC)
            if (
                self._token is not None
                and self._token_expires_at is not None
                and self._token_expires_at > now + timedelta(minutes=5)
            ):
                return self._token

            self._token, self._token_expires_at = await self._fetch_installation_token()
            return self._token

    @property
    def token_expires_at(self) -> datetime | None:
        return self._token_expires_at


class GitHubClient:
    """Async GitHub REST API client with automatic rate limit handling.

    Supports both PAT and GitHub App authentication.
    GitHub App auth provides higher rate limits (15,000+ req/hr vs 5,000).
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str | None = None,
        app_auth: GitHubAppAuth | None = None,
    ):
        """Initialize the client.

        Args:
            token: Personal access token (PAT) for authentication
            app_auth: GitHubAppAuth instance for App authentication

        If neither is provided, tries to use environment variables:
        - GITHUB_APP_* vars for App auth (preferred)
        - GITHUB_TOKEN for PAT auth (fallback)
        """
        self.app_auth = app_auth
        self.pat_token = token

        # Auto-configure from environment if not provided
        if self.app_auth is None and self.pat_token is None:
            if GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_APP_INSTALLATION_ID:
                self.app_auth = GitHubAppAuth(
                    GITHUB_APP_ID,
                    GITHUB_APP_PRIVATE_KEY_PATH,
                    GITHUB_APP_INSTALLATION_ID,
                )
            elif GITHUB_TOKEN:
                self.pat_token = GITHUB_TOKEN
            else:
                raise ValueError(
                    "GitHub auth required. Set GITHUB_TOKEN or "
                    "GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_PATH + GITHUB_APP_INSTALLATION_ID"
                )

        self._auth_type = "app" if self.app_auth else "pat"
        self.client: httpx.AsyncClient | None = None
        self._request_count = 0

    async def __aenter__(self):
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            http2=True,
        )
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    @property
    def auth_type(self) -> str:
        """Return the authentication type being used."""
        return self._auth_type

    async def _get_auth_header(self) -> str:
        """Get the current auth header value."""
        if self.app_auth:
            return f"Bearer {await self.app_auth.get_token()}"
        return f"Bearer {self.pat_token}"

    @property
    def request_count(self) -> int:
        return self._request_count

    async def _handle_rate_limit(self, response: httpx.Response) -> bool:
        """Handle rate limiting. Returns True if request should be retried."""
        if response.status_code == 403:
            remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
            if remaining == 0:
                reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                wait_seconds = max(reset_time - time.time(), 60)
                logger.warning(f"Rate limited (primary). Waiting {wait_seconds:.0f}s until reset...")
                await trio.sleep(wait_seconds + 1)
                return True

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited (secondary). Waiting {retry_after}s...")
            await trio.sleep(retry_after)
            return True

        return False

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        headers: dict | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make request with automatic rate limit handling and token refresh."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        request_headers = {"Authorization": await self._get_auth_header()}
        if headers:
            request_headers.update(headers)

        for attempt in range(max_retries):
            response = await self.client.request(
                method, path, params=params, headers=request_headers
            )
            self._request_count += 1

            if await self._handle_rate_limit(response):
                continue

            if response.status_code >= 500:
                wait = 2**attempt
                logger.warning(f"Server error {response.status_code}. Retrying in {wait}s...")
                await trio.sleep(wait)
                continue

            response.raise_for_status()
            return response

        raise Exception(f"Max retries exceeded for {path}")

    async def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> Any:
        """GET request returning JSON."""
        response = await self._request("GET", path, params=params, headers=headers)
        return response.json()

    async def paginate(
        self,
        path: str,
        params: dict | None = None,
        headers: dict | None = None,
        max_pages: int | None = None,
    ) -> AsyncGenerator[Any]:
        """Paginate through results, yielding each item."""
        params = params.copy() if params else {}
        params["per_page"] = PER_PAGE
        page = 1

        while True:
            params["page"] = page
            response = await self._request("GET", path, params=params, headers=headers)
            items = response.json()

            if not items:
                break

            for item in items:
                yield item

            if len(items) < PER_PAGE:
                break

            if max_pages and page >= max_pages:
                break

            page += 1

    async def paginate_all(
        self,
        path: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> list[dict]:
        """Paginate through all results, returning a list."""
        results = []
        async for item in self.paginate(path, params, headers):
            results.append(item)
        return results

    async def get_pull_requests(
        self,
        state: str = "all",
        sort: str = "created",
        direction: str = "asc",
        since: datetime | None = None,
    ) -> AsyncGenerator[dict]:
        """Get all pull requests, oldest first by default."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls"
        params = {"state": state, "sort": sort, "direction": direction}

        async for pr in self.paginate(path, params):
            pr_created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))

            # Skip PRs before our start date (when going oldest first)
            if since and pr_created < since:
                continue

            yield pr

    async def get_pr_reviews(self, pr_number: int) -> list[dict]:
        """Get all reviews for a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/reviews"
        return await self.paginate_all(path)

    async def get_pr_comments(self, pr_number: int) -> list[dict]:
        """Get PR-level comments (issue comments)."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{pr_number}/comments"
        return await self.paginate_all(path)

    async def get_pr_review_comments(self, pr_number: int) -> list[dict]:
        """Get inline code review comments."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/comments"
        return await self.paginate_all(path)

    async def get_pr_files(self, pr_number: int) -> list[dict]:
        """Get files changed in a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/files"
        return await self.paginate_all(path)

    async def get_pr_commits(self, pr_number: int) -> list[dict]:
        """Get commits in a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/commits"
        return await self.paginate_all(path)

    async def get_check_runs(self, ref: str) -> list[dict]:
        """Get check runs for a commit ref."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{ref}/check-runs"
        response = await self.get(path)
        return response.get("check_runs", [])

    async def get_pr_timeline(self, pr_number: int) -> list[dict]:
        """Get timeline events for a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{pr_number}/timeline"
        # Timeline API requires special accept header
        headers = {"Accept": "application/vnd.github.mockingbird-preview+json"}
        return await self.paginate_all(path, headers=headers)

    async def get_rate_limit(self) -> dict:
        """Get current rate limit status."""
        return await self.get("/rate_limit")
