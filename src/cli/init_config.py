"""CLI tool to auto-generate module config from package manager workspaces.

Detects and parses:
- pnpm-workspace.yaml (pnpm workspaces)
- package.json workspaces (yarn/npm)
- pyproject.toml with uv workspaces
- BUILD.bazel files (bazel packages)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..module_config import DEFAULT_ROOT_PATTERNS, ModuleConfig, ModuleRule


def find_pnpm_workspaces(root: Path) -> list[str]:
    """Parse pnpm-workspace.yaml for workspace patterns."""
    workspace_file = root / "pnpm-workspace.yaml"
    if not workspace_file.exists():
        return []

    with open(workspace_file) as f:
        data = yaml.safe_load(f)

    packages = data.get("packages", [])
    return packages


def find_npm_workspaces(root: Path) -> list[str]:
    """Parse package.json for npm/yarn workspace patterns."""
    package_json = root / "package.json"
    if not package_json.exists():
        return []

    with open(package_json) as f:
        data = json.load(f)

    workspaces = data.get("workspaces", [])
    # Handle both array format and object format with packages key
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])

    return workspaces


def find_uv_workspaces(root: Path) -> list[str]:
    """Parse pyproject.toml for uv workspace members."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    # uv uses [tool.uv.workspace] with members
    uv_config = data.get("tool", {}).get("uv", {})
    workspace = uv_config.get("workspace", {})
    members = workspace.get("members", [])

    return members


def find_bazel_packages(root: Path, max_depth: int = 4) -> list[str]:
    """Find BUILD.bazel files and derive package patterns."""
    packages = []

    for build_file in root.rglob("BUILD.bazel"):
        # Get relative path to parent directory
        rel_path = build_file.parent.relative_to(root)
        if len(rel_path.parts) <= max_depth and len(rel_path.parts) > 0:
            packages.append(str(rel_path))

    # Also check BUILD files (without .bazel extension)
    for build_file in root.rglob("BUILD"):
        if build_file.name == "BUILD":
            rel_path = build_file.parent.relative_to(root)
            if len(rel_path.parts) <= max_depth and len(rel_path.parts) > 0:
                packages.append(str(rel_path))

    return sorted(set(packages))


def glob_to_rule(pattern: str) -> tuple[str, str] | None:
    """Convert workspace glob pattern to ModuleRule pattern/module pair.

    Examples:
        "packages/*" -> ("packages/{name}/**", "packages/{name}")
        "apps/*/packages/*" -> ("apps/{_1}/packages/{name}/**", "apps/{_1}/packages/{name}")
        "backend/py/*" -> ("backend/py/{name}/**", "backend/py/{name}")
    """
    # Normalize pattern
    pattern = pattern.rstrip("/")

    parts = pattern.split("/")
    new_parts = []
    capture_count = 0

    for part in parts:
        if part == "*":
            # Replace * with capture group - last one is {name}, others are numbered
            capture_count += 1
            new_parts.append(f"{{_placeholder_{capture_count}}}")
        elif part == "**":
            # Skip recursive patterns in the middle (we add our own at the end)
            continue
        else:
            new_parts.append(part)

    if not new_parts:
        return None

    # Rename placeholders: last one is {name}, earlier ones are {_1}, {_2}, etc.
    if capture_count > 0:
        final_parts = []
        placeholder_index = 0
        for part in new_parts:
            if part.startswith("{_placeholder_"):
                placeholder_index += 1
                if placeholder_index == capture_count:
                    final_parts.append("{name}")
                else:
                    final_parts.append(f"{{_{placeholder_index}}}")
            else:
                final_parts.append(part)
        new_parts = final_parts

    module_pattern = "/".join(new_parts) + "/**"
    module_name = "/".join(new_parts)

    return (module_pattern, module_name)


def dir_to_rule(directory: str) -> tuple[str, str]:
    """Convert a directory path to a ModuleRule pattern/module pair.

    For explicit directories (not globs), treat as single module.
    """
    directory = directory.rstrip("/")
    return (f"{directory}/**", directory)


def detect_workspaces(root: Path) -> dict[str, list[str]]:
    """Detect all workspace configurations in the repo."""
    results: dict[str, list[str]] = {}

    pnpm = find_pnpm_workspaces(root)
    if pnpm:
        results["pnpm"] = pnpm

    npm = find_npm_workspaces(root)
    if npm:
        results["npm/yarn"] = npm

    uv = find_uv_workspaces(root)
    if uv:
        results["uv"] = uv

    bazel = find_bazel_packages(root)
    if bazel:
        results["bazel"] = bazel

    return results


def generate_config(root: Path) -> ModuleConfig:
    """Generate ModuleConfig from detected workspaces."""
    workspaces = detect_workspaces(root)

    rules: list[ModuleRule] = []
    seen_patterns: set[str] = set()

    # Process each workspace type
    for source, patterns in workspaces.items():
        for pattern in patterns:
            # Check if it's a glob pattern or explicit directory
            if "*" in pattern:
                result = glob_to_rule(pattern)
            else:
                result = dir_to_rule(pattern)

            if result and result[0] not in seen_patterns:
                rule_pattern, module_name = result
                rules.append(ModuleRule(rule_pattern, module_name))
                seen_patterns.add(rule_pattern)

    # Add common defaults if not already covered
    default_patterns = [
        (".github/**", ".github"),
    ]

    for pattern, module in default_patterns:
        if pattern not in seen_patterns:
            rules.append(ModuleRule(pattern, module))

    return ModuleConfig(
        rules=rules,
        default_depth=2,
        root_patterns=DEFAULT_ROOT_PATTERNS.copy(),
    )


def init_config(root: Path | None = None, output: Path | None = None) -> str:
    """Initialize lgtm.yaml config from detected workspaces.

    Args:
        root: Repository root (defaults to cwd)
        output: Output file path (defaults to lgtm.yaml)

    Returns:
        YAML config string
    """
    if root is None:
        root = Path.cwd()
    if output is None:
        output = root / "lgtm.yaml"

    print(f"Scanning {root} for workspace configurations...")

    workspaces = detect_workspaces(root)

    if not workspaces:
        print("No workspace configurations found.")
        print("Supported: pnpm-workspace.yaml, package.json workspaces, pyproject.toml (uv), BUILD.bazel")
        print("\nGenerating minimal config with defaults...")

    for source, patterns in workspaces.items():
        print(f"\n{source}:")
        for p in patterns[:10]:
            print(f"  - {p}")
        if len(patterns) > 10:
            print(f"  ... and {len(patterns) - 10} more")

    config = generate_config(root)
    yaml_content = config.to_yaml()

    # Add header comment
    header = """# lgtm.yaml - Module configuration for code review analysis
# Generated by: uv run lgtm init
#
# Customize this file to define how file paths map to logical modules.
# Modules are used to analyze review patterns across different areas of the codebase.

"""

    full_content = header + yaml_content

    print(f"\nWriting config to {output}")
    with open(output, "w") as f:
        f.write(full_content)

    print(f"\nGenerated {len(config.rules)} module rules.")
    return full_content


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    init_config(root)
