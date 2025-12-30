"""Tests for lgtm init config generation."""

import json
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from src.cli.init_config import (
    detect_workspaces,
    dir_to_rule,
    find_npm_workspaces,
    find_pnpm_workspaces,
    find_uv_workspaces,
    generate_config,
    glob_to_rule,
)


class TestGlobToRule:
    """Test glob pattern to rule conversion."""

    def test_simple_wildcard(self):
        """packages/* -> packages/{name}/**"""
        pattern, module = glob_to_rule("packages/*")
        assert pattern == "packages/{name}/**"
        assert module == "packages/{name}"

    def test_nested_wildcard(self):
        """apps/*/packages/* -> apps/{_1}/packages/{name}/**"""
        pattern, module = glob_to_rule("apps/*/packages/*")
        assert "{name}" in pattern
        assert "{_1}" in pattern

    def test_deep_path(self):
        """backend/py/* -> backend/py/{name}/**"""
        pattern, module = glob_to_rule("backend/py/*")
        assert pattern == "backend/py/{name}/**"
        assert module == "backend/py/{name}"

    def test_trailing_double_star(self):
        """packages/** -> packages/**"""
        pattern, module = glob_to_rule("packages/**")
        assert pattern == "packages/**"
        assert module == "packages"


class TestDirToRule:
    """Test directory to rule conversion."""

    def test_simple_dir(self):
        """Direct directory path."""
        pattern, module = dir_to_rule("backend/core")
        assert pattern == "backend/core/**"
        assert module == "backend/core"

    def test_trailing_slash(self):
        """Trailing slash is stripped."""
        pattern, module = dir_to_rule("frontend/")
        assert pattern == "frontend/**"
        assert module == "frontend"


class TestFindPnpmWorkspaces:
    """Test pnpm workspace detection."""

    def test_finds_packages(self, tmp_path):
        """Parses pnpm-workspace.yaml correctly."""
        workspace_yaml = tmp_path / "pnpm-workspace.yaml"
        workspace_yaml.write_text(
            dedent("""
            packages:
              - 'packages/*'
              - 'apps/*'
              - 'tools/shared'
        """)
        )

        result = find_pnpm_workspaces(tmp_path)
        assert "packages/*" in result
        assert "apps/*" in result
        assert "tools/shared" in result

    def test_missing_file(self, tmp_path):
        """Returns empty list when file doesn't exist."""
        result = find_pnpm_workspaces(tmp_path)
        assert result == []


class TestFindNpmWorkspaces:
    """Test npm/yarn workspace detection."""

    def test_array_format(self, tmp_path):
        """Parses array workspaces format."""
        package_json = tmp_path / "package.json"
        package_json.write_text(json.dumps({"workspaces": ["packages/*", "apps/*"]}))

        result = find_npm_workspaces(tmp_path)
        assert "packages/*" in result
        assert "apps/*" in result

    def test_object_format(self, tmp_path):
        """Parses object workspaces format with packages key."""
        package_json = tmp_path / "package.json"
        package_json.write_text(json.dumps({"workspaces": {"packages": ["packages/*"], "nohoist": ["**/react"]}}))

        result = find_npm_workspaces(tmp_path)
        assert "packages/*" in result

    def test_missing_file(self, tmp_path):
        """Returns empty list when file doesn't exist."""
        result = find_npm_workspaces(tmp_path)
        assert result == []


class TestFindUvWorkspaces:
    """Test uv workspace detection."""

    def test_finds_members(self, tmp_path):
        """Parses uv workspace members."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            dedent("""
            [tool.uv.workspace]
            members = ["backend/py/*", "shared/*"]
        """)
        )

        result = find_uv_workspaces(tmp_path)
        assert "backend/py/*" in result
        assert "shared/*" in result

    def test_missing_file(self, tmp_path):
        """Returns empty list when file doesn't exist."""
        result = find_uv_workspaces(tmp_path)
        assert result == []


class TestGenerateConfig:
    """Test full config generation."""

    def test_combines_workspaces(self, tmp_path):
        """Generates rules from multiple workspace sources."""
        # Create pnpm workspace
        workspace_yaml = tmp_path / "pnpm-workspace.yaml"
        workspace_yaml.write_text("packages:\n  - 'frontend/*'")

        # Create uv workspace
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.uv.workspace]\nmembers = ["backend/py/*"]')

        config = generate_config(tmp_path)

        # Should have rules from both
        patterns = [r.pattern for r in config.rules]
        assert any("frontend" in p for p in patterns)
        assert any("backend/py" in p for p in patterns)

    def test_deduplicates_rules(self, tmp_path):
        """Doesn't create duplicate rules."""
        # Same pattern in two sources
        workspace_yaml = tmp_path / "pnpm-workspace.yaml"
        workspace_yaml.write_text("packages:\n  - 'packages/*'")

        package_json = tmp_path / "package.json"
        package_json.write_text(json.dumps({"workspaces": ["packages/*"]}))

        config = generate_config(tmp_path)

        # Should only have one rule for packages
        patterns = [r.pattern for r in config.rules]
        assert patterns.count("packages/{name}/**") == 1

    def test_includes_github_default(self, tmp_path):
        """Always includes .github rule."""
        config = generate_config(tmp_path)

        patterns = [r.pattern for r in config.rules]
        assert ".github/**" in patterns
