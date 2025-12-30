"""Tests for repository detection and path management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.repo import (
    RepoInfo,
    get_cache_dir,
    get_repo,
    get_repo_from_config,
    get_repo_from_env,
    parse_git_remote_url,
)


class TestParseGitRemoteUrl:
    """Tests for parsing git remote URLs."""

    def test_ssh_format(self):
        """Parse SSH format: git@github.com:owner/repo.git"""
        result = parse_git_remote_url("git@github.com:myorg/myrepo.git")
        assert result is not None
        assert result.owner == "myorg"
        assert result.name == "myrepo"

    def test_ssh_format_no_git_suffix(self):
        """Parse SSH format without .git suffix."""
        result = parse_git_remote_url("git@github.com:myorg/myrepo")
        assert result is not None
        assert result.owner == "myorg"
        assert result.name == "myrepo"

    def test_https_format(self):
        """Parse HTTPS format: https://github.com/owner/repo.git"""
        result = parse_git_remote_url("https://github.com/myorg/myrepo.git")
        assert result is not None
        assert result.owner == "myorg"
        assert result.name == "myrepo"

    def test_https_format_no_git_suffix(self):
        """Parse HTTPS format without .git suffix."""
        result = parse_git_remote_url("https://github.com/myorg/myrepo")
        assert result is not None
        assert result.owner == "myorg"
        assert result.name == "myrepo"

    def test_ssh_url_format(self):
        """Parse ssh:// format: ssh://git@github.com/owner/repo.git"""
        result = parse_git_remote_url("ssh://git@github.com/myorg/myrepo.git")
        assert result is not None
        assert result.owner == "myorg"
        assert result.name == "myrepo"

    def test_gitlab_ssh(self):
        """Parse GitLab SSH URL."""
        result = parse_git_remote_url("git@gitlab.com:company/project.git")
        assert result is not None
        assert result.owner == "company"
        assert result.name == "project"

    def test_enterprise_github(self):
        """Parse enterprise GitHub URL."""
        result = parse_git_remote_url("https://github.mycompany.com/team/repo.git")
        assert result is not None
        assert result.owner == "team"
        assert result.name == "repo"

    def test_invalid_url(self):
        """Return None for invalid URLs."""
        assert parse_git_remote_url("not-a-url") is None
        assert parse_git_remote_url("") is None
        assert parse_git_remote_url("ftp://github.com/foo/bar") is None


class TestRepoInfo:
    """Tests for RepoInfo dataclass."""

    def test_full_name(self):
        """Test full_name property."""
        repo = RepoInfo(owner="myorg", name="myrepo")
        assert repo.full_name == "myorg/myrepo"

    def test_data_paths(self):
        """Test data path properties."""
        repo = RepoInfo(owner="myorg", name="myrepo")
        cache_dir = get_cache_dir()

        assert repo.data_dir == cache_dir / "myorg" / "myrepo"
        assert repo.raw_data_dir == cache_dir / "myorg" / "myrepo" / "raw"
        assert repo.checkpoint_dir == cache_dir / "myorg" / "myrepo" / "checkpoints"
        assert (
            repo.checkpoint_file
            == cache_dir / "myorg" / "myrepo" / "checkpoints" / "extraction_state.json"
        )
        assert repo.log_file == cache_dir / "myorg" / "myrepo" / "extraction.log"


class TestGetCacheDir:
    """Tests for cache directory resolution."""

    def test_default_cache_dir(self):
        """Default cache dir is ~/.cache/lgtm."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove XDG_CACHE_HOME if set
            if "XDG_CACHE_HOME" in os.environ:
                del os.environ["XDG_CACHE_HOME"]
            cache_dir = get_cache_dir()
            assert cache_dir == Path.home() / ".cache" / "lgtm"

    def test_xdg_cache_home(self):
        """Respect XDG_CACHE_HOME if set."""
        with patch.dict(os.environ, {"XDG_CACHE_HOME": "/custom/cache"}):
            cache_dir = get_cache_dir()
            assert cache_dir == Path("/custom/cache/lgtm")


class TestGetRepoFromEnv:
    """Tests for environment variable repo detection."""

    def test_env_vars_set(self):
        """Return RepoInfo when both env vars are set."""
        with patch.dict(os.environ, {"REPO_OWNER": "envorg", "REPO_NAME": "envrepo"}):
            repo = get_repo_from_env()
            assert repo is not None
            assert repo.owner == "envorg"
            assert repo.name == "envrepo"

    def test_env_vars_missing(self):
        """Return None when env vars are not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the vars are not set
            os.environ.pop("REPO_OWNER", None)
            os.environ.pop("REPO_NAME", None)
            repo = get_repo_from_env()
            assert repo is None

    def test_env_vars_partial(self):
        """Return None when only one env var is set."""
        with patch.dict(os.environ, {"REPO_OWNER": "myorg"}, clear=True):
            os.environ.pop("REPO_NAME", None)
            repo = get_repo_from_env()
            assert repo is None


class TestGetRepoFromConfig:
    """Tests for lgtm.yaml repo configuration."""

    def test_config_with_repo_section(self, tmp_path, monkeypatch):
        """Load repo from lgtm.yaml repo section."""
        config_file = tmp_path / "lgtm.yaml"
        config_file.write_text("""
repo:
  owner: configorg
  name: configrepo

modules:
  default_depth: 2
""")
        monkeypatch.chdir(tmp_path)
        repo = get_repo_from_config()
        assert repo is not None
        assert repo.owner == "configorg"
        assert repo.name == "configrepo"

    def test_config_without_repo_section(self, tmp_path, monkeypatch):
        """Return None when lgtm.yaml has no repo section."""
        config_file = tmp_path / "lgtm.yaml"
        config_file.write_text("""
modules:
  default_depth: 2
""")
        monkeypatch.chdir(tmp_path)
        repo = get_repo_from_config()
        assert repo is None

    def test_no_config_file(self, tmp_path, monkeypatch):
        """Return None when no lgtm.yaml exists."""
        monkeypatch.chdir(tmp_path)
        repo = get_repo_from_config()
        assert repo is None


class TestGetRepo:
    """Tests for the main get_repo function with fallback chain."""

    def test_env_takes_precedence(self, tmp_path, monkeypatch):
        """Environment variables take precedence over config and git."""
        # Set up config file
        config_file = tmp_path / "lgtm.yaml"
        config_file.write_text("""
repo:
  owner: configorg
  name: configrepo
""")
        monkeypatch.chdir(tmp_path)

        # Set env vars
        with patch.dict(os.environ, {"REPO_OWNER": "envorg", "REPO_NAME": "envrepo"}):
            repo = get_repo()
            assert repo.owner == "envorg"
            assert repo.name == "envrepo"

    def test_config_fallback(self, tmp_path, monkeypatch):
        """Fall back to config when env vars not set."""
        config_file = tmp_path / "lgtm.yaml"
        config_file.write_text("""
repo:
  owner: configorg
  name: configrepo
""")
        monkeypatch.chdir(tmp_path)

        # Clear env vars
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPO_OWNER", None)
            os.environ.pop("REPO_NAME", None)
            repo = get_repo()
            assert repo.owner == "configorg"
            assert repo.name == "configrepo"

    def test_raises_when_no_repo_found(self, tmp_path, monkeypatch):
        """Raise ValueError when repo cannot be determined."""
        monkeypatch.chdir(tmp_path)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPO_OWNER", None)
            os.environ.pop("REPO_NAME", None)
            with pytest.raises(ValueError, match="Could not determine repository"):
                get_repo()
