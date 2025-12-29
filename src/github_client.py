"""GitHub API client with rate limiting, retry logic, and App auth support."""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Generator, Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

from .config import (
    GITHUB_TOKEN,
    GITHUB_APP_ID,
    GITHUB_APP_PRIVATE_KEY_PATH,
    GITHUB_APP_INSTALLATION_ID,
    PER_PAGE,
    REPO_OWNER,
    REPO_NAME,
)


class GitHubAppAuth:
    """GitHub App authentication manager with automatic token refresh."""

    def __init__(self, app_id: str, private_key_path: str, installation_id: str):
        self.app_id = app_id
        self.installation_id = installation_id

        # Load private key
        with open(private_key_path) as f:
            self.private_key = f.read()

        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = datetime.now(timezone.utc)
        payload = {
            "iat": int(now.timestamp()) - 60,  # Issued 60s ago (clock skew)
            "exp": int((now + timedelta(minutes=10)).timestamp()),  # Expires in 10 min
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _fetch_installation_token(self) -> tuple[str, datetime]:
        """Exchange JWT for an installation access token."""
        jwt_token = self._generate_jwt()

        response = httpx.post(
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

    def get_token(self) -> str:
        """Get a valid installation token, refreshing if needed."""
        now = datetime.now(timezone.utc)

        # Refresh if no token or expires in less than 5 minutes
        if (
            self._token is None
            or self._token_expires_at is None
            or self._token_expires_at <= now + timedelta(minutes=5)
        ):
            self._token, self._token_expires_at = self._fetch_installation_token()

        return self._token

    @property
    def token_expires_at(self) -> Optional[datetime]:
        return self._token_expires_at


class GitHubClient:
    """GitHub REST API client with automatic rate limit handling.

    Supports both PAT and GitHub App authentication.
    GitHub App auth provides higher rate limits (15,000+ req/hr vs 5,000).
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        app_auth: Optional[GitHubAppAuth] = None,
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
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self._request_count = 0

    @property
    def auth_type(self) -> str:
        """Return the authentication type being used."""
        return self._auth_type

    def _get_auth_header(self) -> str:
        """Get the current auth header value."""
        if self.app_auth:
            return f"Bearer {self.app_auth.get_token()}"
        return f"Bearer {self.pat_token}"

    @property
    def request_count(self) -> int:
        return self._request_count

    def _handle_rate_limit(self, response: httpx.Response) -> bool:
        """Handle rate limiting. Returns True if request should be retried."""
        if response.status_code == 403:
            remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
            if remaining == 0:
                reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                wait_seconds = max(reset_time - time.time(), 60)
                logger.warning(f"Rate limited (primary). Waiting {wait_seconds:.0f}s until reset...")
                print(f"\nRate limited. Waiting {wait_seconds:.0f}s until reset...")
                time.sleep(wait_seconds + 1)
                return True

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited (secondary). Waiting {retry_after}s...")
            print(f"\nSecondary rate limit. Waiting {retry_after}s...")
            time.sleep(retry_after)
            return True

        return False

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make request with automatic rate limit handling and token refresh."""
        for attempt in range(max_retries):
            # Update auth header (may refresh App token)
            self.client.headers["Authorization"] = self._get_auth_header()

            response = self.client.request(method, path, params=params)
            self._request_count += 1

            if self._handle_rate_limit(response):
                continue

            if response.status_code >= 500:
                wait = 2**attempt
                print(f"\nServer error {response.status_code}. Retrying in {wait}s...")
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        raise Exception(f"Max retries exceeded for {path}")

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET request returning JSON."""
        response = self._request("GET", path, params=params)
        return response.json()

    def paginate(
        self,
        path: str,
        params: Optional[dict] = None,
        max_pages: Optional[int] = None,
    ) -> Generator[Any, None, None]:
        """Paginate through results, yielding each item."""
        params = params or {}
        params["per_page"] = PER_PAGE
        page = 1

        while True:
            params["page"] = page
            response = self._request("GET", path, params=params)
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

    def get_pull_requests(
        self,
        state: str = "all",
        sort: str = "created",
        direction: str = "desc",
        since: Optional[datetime] = None,
    ) -> Generator[dict, None, None]:
        """Get all pull requests."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls"
        params = {"state": state, "sort": sort, "direction": direction}

        for pr in self.paginate(path, params):
            pr_created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))

            # Stop if we've gone past our date range
            if since and pr_created < since:
                return

            yield pr

    def get_pr_reviews(self, pr_number: int) -> list[dict]:
        """Get all reviews for a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/reviews"
        return list(self.paginate(path))

    def get_pr_comments(self, pr_number: int) -> list[dict]:
        """Get PR-level comments (issue comments)."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{pr_number}/comments"
        return list(self.paginate(path))

    def get_pr_review_comments(self, pr_number: int) -> list[dict]:
        """Get inline code review comments."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/comments"
        return list(self.paginate(path))

    def get_pr_files(self, pr_number: int) -> list[dict]:
        """Get files changed in a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/files"
        return list(self.paginate(path))

    def get_pr_commits(self, pr_number: int) -> list[dict]:
        """Get commits in a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/commits"
        return list(self.paginate(path))

    def get_check_runs(self, ref: str) -> list[dict]:
        """Get check runs for a commit ref."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{ref}/check-runs"
        response = self.get(path)
        return response.get("check_runs", [])

    def get_pr_timeline(self, pr_number: int) -> list[dict]:
        """Get timeline events for a PR."""
        path = f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{pr_number}/timeline"
        # Timeline API requires special accept header
        original_accept = self.client.headers["Accept"]
        self.client.headers["Accept"] = "application/vnd.github.mockingbird-preview+json"
        try:
            return list(self.paginate(path))
        finally:
            self.client.headers["Accept"] = original_accept

    def get_rate_limit(self) -> dict:
        """Get current rate limit status."""
        return self.get("/rate_limit")

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
