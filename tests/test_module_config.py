"""Tests for module configuration and path extraction."""

import pytest

from src.module_config import ModuleConfig, ModuleRule


class TestModuleRule:
    """Tests for individual rule matching."""

    def test_simple_pattern(self):
        """Basic pattern with capture."""
        rule = ModuleRule("backend/py/{name}/**", "backend/py/{name}")
        assert rule.match("backend/py/cogna-tools/src/main.py") == {"name": "cogna-tools"}
        assert rule.match("backend/go/something") is None

    def test_extract_module(self):
        """Extract module from filepath."""
        rule = ModuleRule("backend/py/{name}/**", "backend/py/{name}")
        assert rule.extract_module("backend/py/cogna-tools/src/main.py") == "backend/py/cogna-tools"
        assert rule.extract_module("frontend/app/page.tsx") is None

    def test_multiple_captures(self):
        """Pattern with multiple captures."""
        rule = ModuleRule("{area}/{lang}/{name}/**", "{area}/{lang}/{name}")
        assert rule.match("backend/py/cogna-tools/file.py") == {
            "area": "backend",
            "lang": "py",
            "name": "cogna-tools",
        }

    def test_no_trailing_wildcard(self):
        """Pattern without ** at end."""
        rule = ModuleRule("proto/{name}/**", "proto/{name}")
        assert rule.match("proto/foo/bar.proto") == {"name": "foo"}
        assert rule.match("proto/foo") == {"name": "foo"}

    def test_top_level_wildcard(self):
        """Pattern for top-level package."""
        rule = ModuleRule("app-runtime/**", "app-runtime")
        assert rule.extract_module("app-runtime/src/index.ts") == "app-runtime"
        assert rule.extract_module("app-runtime/package.json") == "app-runtime"


class TestModuleConfig:
    """Tests for the full config."""

    @pytest.fixture
    def config(self):
        """Default config for testing."""
        return ModuleConfig.default()

    def test_root_files(self, config):
        """Root-level dotfiles and configs go to 'root'."""
        assert config.extract_module(".gitignore") == "root"
        assert config.extract_module("README.md") == "root"
        assert config.extract_module("uv.lock") == "root"
        assert config.extract_module("pyproject.toml") == "root"
        # Build system files
        assert config.extract_module("go.mod") == "root"
        assert config.extract_module("go.sum") == "root"
        assert config.extract_module("WORKSPACE") == "root"
        assert config.extract_module("MODULE.bazel") == "root"
        assert config.extract_module("package.json") == "root"
        # Subdirectory files are NOT root
        assert config.extract_module(".github/workflows/ci.yml") != "root"

    def test_src_pattern(self, config):
        """src/{name} pattern from defaults."""
        assert config.extract_module("src/utils/helper.py") == "src/utils"
        # Direct files in src/ get the filename as module (src/{name} captures it)
        assert config.extract_module("src/main.py") == "src/main.py"

    def test_packages_pattern(self, config):
        """packages/{name} pattern from defaults."""
        assert config.extract_module("packages/ui-kit/src/Button.tsx") == "packages/ui-kit"
        assert config.extract_module("packages/shared/index.ts") == "packages/shared"

    def test_apps_pattern(self, config):
        """apps/{name} pattern from defaults."""
        assert config.extract_module("apps/web/pages/index.tsx") == "apps/web"
        assert config.extract_module("apps/api/server.py") == "apps/api"

    def test_github(self, config):
        """GitHub workflows."""
        assert config.extract_module(".github/workflows/ci.yml") == ".github"
        assert config.extract_module(".github/CODEOWNERS") == ".github"

    def test_fallback_depth(self, config):
        """Unknown paths use default depth."""
        assert config.extract_module("some/unknown/deep/path/file.py") == "some/unknown"
        assert config.extract_module("tools/scripts/build.sh") == "tools/scripts"


class TestCustomConfig:
    """Tests for custom config scenarios."""

    def test_custom_rules(self):
        """Custom rules override defaults."""
        config = ModuleConfig(
            rules=[
                ModuleRule("backend/py/{name}/**", "backend/py/{name}"),
                ModuleRule("frontend/**", "frontend"),
            ]
        )
        assert config.extract_module("backend/py/tools/main.py") == "backend/py/tools"
        assert config.extract_module("frontend/src/App.tsx") == "frontend"

    def test_single_module_pattern(self):
        """Pattern without capture treats area as single module."""
        config = ModuleConfig(rules=[ModuleRule("proto/**", "proto")])
        assert config.extract_module("proto/users/user.proto") == "proto"
        assert config.extract_module("proto/common/types.proto") == "proto"


class TestIsGenerated:
    """Tests for generated file detection."""

    @pytest.fixture
    def config(self):
        return ModuleConfig.default()

    def test_protobuf_files(self, config):
        """Protobuf generated files."""
        assert config.is_generated("proto/gen/go/api.pb.go") is True
        assert config.is_generated("backend/api.pb.ts") is True
        assert config.is_generated("service_pb2.py") is True

    def test_lock_files(self, config):
        """Lock files are generated."""
        assert config.is_generated("package-lock.json") is True
        assert config.is_generated("pnpm-lock.yaml") is True
        assert config.is_generated("go.sum") is True
        assert config.is_generated("Gemfile.lock") is True

    def test_snapshot_files(self, config):
        """Test snapshots are generated."""
        assert config.is_generated("tests/__snapshots__/test.snap") is True
        assert config.is_generated("test_snapshot.json") is True

    def test_gen_directories(self, config):
        """Files in gen/ directories."""
        assert config.is_generated("proto/gen/python/api.py") is True
        assert config.is_generated("src/generated/types.ts") is True

    def test_regular_code(self, config):
        """Regular code is not generated."""
        assert config.is_generated("src/main.py") is False
        assert config.is_generated("backend/api/handler.go") is False
        assert config.is_generated("README.md") is False

    def test_custom_patterns(self):
        """Custom generated patterns."""
        config = ModuleConfig(
            generated_patterns=["custom/autogen/*"],
            include_default_generated=False,
        )
        assert config.is_generated("custom/autogen/file.py") is True
        assert config.is_generated("proto/gen/api.pb.go") is False  # Defaults disabled


class TestConfigLoading:
    """Tests for config file loading."""

    def test_from_dict(self):
        """Load config from dictionary."""
        data = {
            "modules": {
                "rules": [
                    {"pattern": "src/{name}/**", "module": "src/{name}"},
                ],
                "default_depth": 1,
            }
        }
        config = ModuleConfig.from_dict(data)
        assert len(config.rules) == 1
        assert config.default_depth == 1
        assert config.extract_module("src/utils/helper.py") == "src/utils"

    def test_to_yaml(self):
        """Serialize config to YAML."""
        config = ModuleConfig(
            rules=[ModuleRule("test/{name}/**", "test/{name}")],
            default_depth=3,
        )
        yaml_str = config.to_yaml()
        assert "test/{name}/**" in yaml_str
        assert "default_depth: 3" in yaml_str

    def test_default_returns_valid_config(self):
        """Default config is valid and non-empty."""
        config = ModuleConfig.default()
        assert len(config.rules) > 0
        assert config.default_depth >= 1


class TestBotDetection:
    """Tests for bot detection configuration."""

    @pytest.fixture
    def config(self):
        return ModuleConfig.default()

    def test_standard_bot_suffix(self, config):
        """Logins ending in [bot] are detected as bots."""
        assert config.is_bot("renovate[bot]") is True
        assert config.is_bot("dependabot[bot]") is True
        assert config.is_bot("cursor[bot]") is True
        assert config.is_bot("custom-bot[bot]") is True

    def test_human_logins(self, config):
        """Human logins are not detected as bots."""
        assert config.is_bot("octocat") is False
        assert config.is_bot("johndoe") is False
        assert config.is_bot("bot-lover") is False  # contains 'bot' but not suffix

    def test_github_api_type(self, config):
        """User type from GitHub API is respected."""
        assert config.is_bot("some-app", user_type="Bot") is True
        assert config.is_bot("some-user", user_type="User") is False

    def test_empty_login(self, config):
        """Empty login returns False."""
        assert config.is_bot("") is False
        assert config.is_bot("", user_type="Bot") is False

    def test_custom_bot_logins(self):
        """Custom bot logins list is checked."""
        config = ModuleConfig(
            bot_logins=["ci-bot", "deploy-automation"],
        )
        assert config.is_bot("ci-bot") is True
        assert config.is_bot("deploy-automation") is True
        assert config.is_bot("regular-user") is False

    def test_custom_bot_patterns(self):
        """Custom glob patterns for bot detection."""
        config = ModuleConfig(
            bot_patterns=["*-automation", "ci-*"],
            include_default_bots=False,
        )
        assert config.is_bot("deploy-automation") is True
        assert config.is_bot("ci-runner") is True
        assert config.is_bot("renovate[bot]") is False  # Default pattern disabled

    def test_combined_detection(self):
        """Patterns and logins are checked together."""
        config = ModuleConfig(
            bot_patterns=["*[bot]", "auto-*"],
            bot_logins=["specific-bot"],
        )
        assert config.is_bot("renovate[bot]") is True  # pattern
        assert config.is_bot("auto-merger") is True  # pattern
        assert config.is_bot("specific-bot") is True  # explicit login
        assert config.is_bot("human-user") is False

    def test_get_bot_name_known_bots(self, config):
        """Known bots return their friendly names."""
        assert config.get_bot_name("github-actions[bot]") == "github-actions"
        assert config.get_bot_name("renovate[bot]") == "renovate"

    def test_get_bot_name_unknown_bot(self, config):
        """Unknown bots with [bot] suffix get name extracted."""
        assert config.get_bot_name("custom-ci[bot]") == "custom-ci"
        assert config.get_bot_name("my-app[bot]") == "my-app"

    def test_get_bot_name_not_a_bot(self, config):
        """Non-bot logins return None."""
        assert config.get_bot_name("regular-user") is None
        assert config.get_bot_name("") is None


class TestBotConfigLoading:
    """Tests for loading bot config from YAML."""

    def test_from_dict_with_bots(self):
        """Load config with bots section."""
        data = {
            "bots": {
                "patterns": ["ci-*"],
                "logins": ["my-bot"],
            }
        }
        config = ModuleConfig.from_dict(data)
        # Should include default pattern plus custom
        assert "*[bot]" in config.bot_patterns
        assert "ci-*" in config.bot_patterns
        assert "my-bot" in config.bot_logins

    def test_from_dict_disable_defaults(self):
        """Disable default bot patterns."""
        data = {
            "bots": {
                "include_defaults": False,
                "patterns": ["custom-*"],
            }
        }
        config = ModuleConfig.from_dict(data)
        assert "*[bot]" not in config.bot_patterns
        assert "custom-*" in config.bot_patterns

    def test_from_dict_empty_bots(self):
        """Empty bots section uses defaults."""
        data = {"bots": {}}
        config = ModuleConfig.from_dict(data)
        assert "*[bot]" in config.bot_patterns
        assert config.bot_logins == []

    def test_to_yaml_with_custom_bots(self):
        """Serialize config with custom bot logins."""
        config = ModuleConfig(
            rules=[],
            bot_logins=["custom-bot"],
        )
        yaml_str = config.to_yaml()
        assert "bots:" in yaml_str
        assert "custom-bot" in yaml_str

    def test_to_yaml_defaults_not_included(self):
        """Default config doesn't include bots section in YAML."""
        config = ModuleConfig.default()
        yaml_str = config.to_yaml()
        assert "bots:" not in yaml_str
