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
