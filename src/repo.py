"""Repository detection and data path management.

Detects repo owner/name from git remote or config, and manages
global data storage paths.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoInfo:
    """Repository information."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def data_dir(self) -> Path:
        """Global data directory for this repo."""
        return get_cache_dir() / self.owner / self.name

    @property
    def raw_data_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def checkpoint_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @property
    def checkpoint_file(self) -> Path:
        return self.checkpoint_dir / "extraction_state.json"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "extraction.log"


def get_cache_dir() -> Path:
    """Get the global cache directory for lgtm data."""
    # Use XDG_CACHE_HOME if set, otherwise ~/.cache
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "lgtm"
    return Path.home() / ".cache" / "lgtm"


def parse_git_remote_url(url: str) -> RepoInfo | None:
    """Parse owner/repo from git remote URL.

    Supports:
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo
    - ssh://git@github.com/owner/repo.git
    """
    # SSH format: git@github.com:owner/repo.git
    ssh_match = re.match(r"git@[\w.-]+:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return RepoInfo(owner=ssh_match.group(1), name=ssh_match.group(2))

    # HTTPS format: https://github.com/owner/repo.git
    https_match = re.match(r"https?://[\w.-]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return RepoInfo(owner=https_match.group(1), name=https_match.group(2))

    # SSH with ssh:// prefix
    ssh_url_match = re.match(r"ssh://git@[\w.-]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_url_match:
        return RepoInfo(owner=ssh_url_match.group(1), name=ssh_url_match.group(2))

    return None


def get_git_remote_url(remote: str = "origin") -> str | None:
    """Get the URL of a git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def detect_repo_from_git() -> RepoInfo | None:
    """Detect repo from git remote in current directory."""
    url = get_git_remote_url("origin")
    if url:
        return parse_git_remote_url(url)
    return None


def get_repo_from_config() -> RepoInfo | None:
    """Get repo from lgtm.yaml config if specified."""
    from .module_config import ModuleConfig

    config = ModuleConfig.load()
    if config.repo_owner and config.repo_name:
        return RepoInfo(owner=config.repo_owner, name=config.repo_name)
    return None


def get_repo_from_env() -> RepoInfo | None:
    """Get repo from environment variables."""
    owner = os.environ.get("REPO_OWNER")
    name = os.environ.get("REPO_NAME")
    if owner and name:
        return RepoInfo(owner=owner, name=name)
    return None


def get_repo() -> RepoInfo:
    """Get repo info with fallback chain.

    Priority:
    1. Environment variables (REPO_OWNER, REPO_NAME)
    2. lgtm.yaml config (repo.owner, repo.name)
    3. Git remote detection

    Raises ValueError if repo cannot be determined.
    """
    # Try env vars first (highest priority, allows override)
    repo = get_repo_from_env()
    if repo:
        return repo

    # Try lgtm.yaml config
    repo = get_repo_from_config()
    if repo:
        return repo

    # Try git remote detection
    repo = detect_repo_from_git()
    if repo:
        return repo

    raise ValueError(
        "Could not determine repository. Either:\n"
        "  1. Run from a git repo with a GitHub remote, or\n"
        "  2. Set REPO_OWNER and REPO_NAME env vars, or\n"
        "  3. Add repo section to lgtm.yaml:\n"
        "     repo:\n"
        "       owner: your-org\n"
        "       name: your-repo"
    )
