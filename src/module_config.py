"""Module configuration for extracting logical modules from file paths.

Supports pattern-based rules with glob-like syntax:
- `backend/py/{name}/**` matches `backend/py/cogna-tools/src/main.py`
- `{name}` captures the segment as a named group
- `**` matches any remaining path segments
- `*` matches a single segment
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModuleRule:
    """A pattern-to-module mapping rule."""

    pattern: str  # e.g., "backend/py/{name}/**"
    module: str  # e.g., "backend/py/{name}"

    def __post_init__(self):
        # Pre-compile the regex for matching
        self._regex = self._pattern_to_regex(self.pattern)

    def _pattern_to_regex(self, pattern: str) -> re.Pattern:
        """Convert glob-like pattern to regex.

        Supported syntax:
        - {name} - captures a single path segment as named group
        - ** - matches any remaining path segments (including zero)
        - * - matches a single segment
        """
        segments = pattern.split("/")
        regex_parts = []

        for i, segment in enumerate(segments):
            if segment == "**":
                # ** matches anything remaining (or nothing)
                # This must be the last segment
                regex_parts.append(r"(?:/.*)?")
            elif segment == "*":
                regex_parts.append(r"[^/]+")
            elif "{" in segment:
                # Named capture: {name} -> (?P<name>[^/]+)
                converted = re.sub(
                    r"\{(\w+)\}",
                    lambda m: f"(?P<{m.group(1)}>[^/]+)",
                    segment,
                )
                regex_parts.append(converted)
            else:
                regex_parts.append(re.escape(segment))

        # Build the final regex
        # Join non-** parts with /, then add ** suffix if present
        if segments[-1] == "**":
            # The ** pattern is already added, just join the preceding parts
            prefix_parts = regex_parts[:-1]
            suffix = regex_parts[-1]
            regex_str = "^" + "/".join(prefix_parts) + suffix + "$"
        else:
            # No **, allow trailing path segments
            regex_str = "^" + "/".join(regex_parts) + r"(?:/.*)?$"

        return re.compile(regex_str)

    def match(self, filepath: str) -> dict[str, str] | None:
        """Match filepath against pattern, returning captured groups or None."""
        m = self._regex.match(filepath)
        if m:
            return m.groupdict()
        return None

    def extract_module(self, filepath: str) -> str | None:
        """Extract module name from filepath using this rule."""
        captures = self.match(filepath)
        if captures is None:
            return None

        # Format the module template with captured values
        try:
            return self.module.format(**captures)
        except KeyError:
            return None


DEFAULT_ROOT_PATTERNS = [
    # Dotfiles and docs
    ".*",
    "*.md",
    "*.txt",
    # Lock files and configs
    "*.lock",
    "*.toml",
    "*.yaml",
    "*.yml",
    "*.json",
    # Build files
    "Makefile",
    "Dockerfile*",
    # Go modules
    "go.mod",
    "go.sum",
    # Bazel
    "WORKSPACE",
    "WORKSPACE.bazel",
    "MODULE.bazel",
    "BUILD",
    "BUILD.bazel",
    # Other common root files
    "Gemfile",
    "Gemfile.lock",
    "Cargo.toml",
    "Cargo.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
]


DEFAULT_GENERATED_PATTERNS = [
    # Generated code directories
    "*/gen/*",
    "*/generated/*",
    "*/__generated__/*",
    # Protobuf
    "*.pb.go",
    "*.pb.ts",
    "*.pb.js",
    "*.pb.py",
    "*_pb2.py",
    "*_pb2_grpc.py",
    # Other codegen
    "*.generated.*",
    "*.gen.*",
    "*_generated.*",
    "*_gen.*",
    # Lock files
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
    # Snapshots
    "*snapshot*",
    "*/__snapshots__/*",
    "*.snap",
    # Minified
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
]


# Default bot patterns to match common GitHub bot accounts
DEFAULT_BOT_PATTERNS = [
    "*[bot]",  # Standard GitHub bot suffix (e.g., renovate[bot], dependabot[bot])
]


# Known bots with their friendly names (login -> display name)
DEFAULT_BOT_LOGINS: dict[str, str] = {
    "cursor[bot]": "cursor",
    "github-actions[bot]": "github-actions",
    "renovate[bot]": "renovate",
    "dependabot[bot]": "dependabot",
    "datadog-*[bot]": "datadog",  # Note: This is a pattern hint, actual matching uses patterns
    "incident-io[bot]": "incident-io",
    "aikido-security[bot]": "aikido-security",
    "aikido-autofix[bot]": "aikido-autofix",
    "linear[bot]": "linear",
}


@dataclass
class ModuleConfig:
    """Configuration for module extraction."""

    rules: list[ModuleRule] = field(default_factory=list)
    default_depth: int = 2
    root_patterns: list[str] = field(default_factory=lambda: DEFAULT_ROOT_PATTERNS.copy())
    generated_patterns: list[str] = field(default_factory=lambda: DEFAULT_GENERATED_PATTERNS.copy())
    include_default_generated: bool = True  # Set False to only use custom patterns

    # Bot detection configuration
    bot_patterns: list[str] = field(default_factory=lambda: DEFAULT_BOT_PATTERNS.copy())
    bot_logins: list[str] = field(default_factory=list)
    include_default_bots: bool = True  # Set False to only use custom bot config

    @classmethod
    def load(cls, path: Path | str | None = None) -> ModuleConfig:
        """Load config from YAML file or return defaults."""
        if path is None:
            # Try common locations
            for candidate in ["lgtm.yaml", ".lgtm.yaml", "lgtm.yml", ".lgtm.yml"]:
                if Path(candidate).exists():
                    path = candidate
                    break

        if path is None or not Path(path).exists():
            return cls.default()

        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data or {})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleConfig:
        """Create config from dictionary (e.g., parsed YAML)."""
        modules_data = data.get("modules", {})
        bots_data = data.get("bots", {})

        rules = []
        for rule_data in modules_data.get("rules", []):
            rules.append(
                ModuleRule(
                    pattern=rule_data["pattern"],
                    module=rule_data["module"],
                )
            )

        include_default_generated = modules_data.get("include_default_generated", True)
        custom_generated = modules_data.get("generated_patterns", [])

        # Merge custom patterns with defaults if enabled
        if include_default_generated:
            generated_patterns = DEFAULT_GENERATED_PATTERNS.copy() + custom_generated
        else:
            generated_patterns = custom_generated

        # Bot configuration
        include_default_bots = bots_data.get("include_defaults", True)
        custom_bot_patterns = bots_data.get("patterns", [])
        custom_bot_logins = bots_data.get("logins", [])

        # Merge bot patterns with defaults if enabled
        if include_default_bots:
            bot_patterns = DEFAULT_BOT_PATTERNS.copy() + custom_bot_patterns
        else:
            bot_patterns = custom_bot_patterns

        return cls(
            rules=rules,
            default_depth=modules_data.get("default_depth", 2),
            root_patterns=modules_data.get("root_patterns", DEFAULT_ROOT_PATTERNS.copy()),
            generated_patterns=generated_patterns,
            include_default_generated=include_default_generated,
            bot_patterns=bot_patterns,
            bot_logins=custom_bot_logins,
            include_default_bots=include_default_bots,
        )

    @classmethod
    def default(cls) -> ModuleConfig:
        """Minimal default config - use `lgtm init` to generate repo-specific rules."""
        return cls(
            rules=[
                # Common patterns most repos have
                ModuleRule("src/{name}/**", "src/{name}"),
                ModuleRule("packages/{name}/**", "packages/{name}"),
                ModuleRule("apps/{name}/**", "apps/{name}"),
                ModuleRule(".github/**", ".github"),
            ],
            default_depth=2,
        )

    def _is_root_file(self, filepath: str) -> bool:
        """Check if filepath is a root-level config/dotfile."""
        # Only check files in root directory
        if "/" in filepath:
            return False

        # Match against root patterns
        for pattern in self.root_patterns:
            if fnmatch.fnmatch(filepath, pattern):
                return True

        return False

    def is_generated(self, filepath: str) -> bool:
        """Check if filepath matches generated/lock/snapshot patterns.

        Use this to filter out autogenerated churn from review analysis.
        """
        if not filepath:
            return False

        filename = filepath.split("/")[-1]

        for pattern in self.generated_patterns:
            # Handle patterns with path separators (e.g., "*/gen/*")
            if "/" in pattern:
                if fnmatch.fnmatch(filepath, pattern):
                    return True
            else:
                # Match just the filename (e.g., "*.pb.go")
                if fnmatch.fnmatch(filename, pattern):
                    return True

        return False

    def is_bot(self, login: str, user_type: str | None = None) -> bool:
        """Check if a login represents a bot account.

        Args:
            login: GitHub login/username
            user_type: Optional user type from GitHub API (e.g., "Bot", "User")

        Returns:
            True if the login matches bot patterns, is in bot_logins list,
            or user_type is "Bot"
        """
        if not login:
            return False

        # Check user type from GitHub API
        if user_type == "Bot":
            return True

        # Check exact login matches
        if login in self.bot_logins:
            return True

        # Check glob patterns
        # Note: We need to escape [ and ] in patterns for fnmatch since it treats them
        # as character classes. We use a simple regex-based approach instead.
        for pattern in self.bot_patterns:
            if self._match_bot_pattern(pattern, login):
                return True

        return False

    def _match_bot_pattern(self, pattern: str, login: str) -> bool:
        """Match a bot pattern against a login.

        Handles patterns like '*[bot]' which fnmatch would interpret as character class.
        """
        # Convert glob pattern to regex, treating [] as literal brackets
        # First escape regex special chars, then convert glob wildcards
        regex_pattern = re.escape(pattern)
        regex_pattern = regex_pattern.replace(r"\*", ".*")
        regex_pattern = regex_pattern.replace(r"\?", ".")
        regex_pattern = f"^{regex_pattern}$"
        return bool(re.match(regex_pattern, login))

    def get_bot_name(self, login: str) -> str | None:
        """Extract a friendly bot name from login.

        Returns the portion before [bot] suffix, or None if not a known bot pattern.
        """
        if not login:
            return None

        # Check known bot logins for friendly names
        if login in DEFAULT_BOT_LOGINS:
            return DEFAULT_BOT_LOGINS[login]

        # Extract name from [bot] suffix
        if login.endswith("[bot]"):
            return login.replace("[bot]", "")

        return None

    def extract_module(self, filepath: str) -> str:
        """Extract module from filepath using config rules.

        Order of precedence:
        1. Empty path -> "root"
        2. Root files (dotfiles, configs) -> "root"
        3. Pattern rules (first match wins)
        4. Default depth fallback
        """
        # Handle empty path
        if not filepath:
            return "root"

        # Handle root-level files
        if self._is_root_file(filepath):
            return "root"

        # Try rules in order
        for rule in self.rules:
            module = rule.extract_module(filepath)
            if module is not None:
                return module

        # Fallback to default depth
        parts = filepath.split("/")
        if len(parts) <= self.default_depth:
            return "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
        return "/".join(parts[: self.default_depth])

    def to_yaml(self) -> str:
        """Serialize config to YAML."""
        data: dict[str, Any] = {
            "modules": {
                "rules": [{"pattern": r.pattern, "module": r.module} for r in self.rules],
                "default_depth": self.default_depth,
                "root_patterns": self.root_patterns,
            }
        }

        # Only include generated config if customized
        if not self.include_default_generated:
            data["modules"]["include_default_generated"] = False
            data["modules"]["generated_patterns"] = self.generated_patterns

        # Only include bots config if customized
        if not self.include_default_bots or self.bot_logins:
            data["bots"] = {}
            if not self.include_default_bots:
                data["bots"]["include_defaults"] = False
                data["bots"]["patterns"] = self.bot_patterns
            if self.bot_logins:
                data["bots"]["logins"] = self.bot_logins

        return yaml.dump(data, default_flow_style=False, sort_keys=False)
