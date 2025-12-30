"""File changes extractor with configurable module detection."""

from ..models import FileChange
from ..module_config import ModuleConfig

# Module-level config instance (singleton pattern)
_config: ModuleConfig | None = None


def get_module_config() -> ModuleConfig:
    """Get or create the module config singleton."""
    global _config
    if _config is None:
        _config = ModuleConfig.load()
    return _config


def set_module_config(config: ModuleConfig) -> None:
    """Set the module config (for testing or custom configs)."""
    global _config
    _config = config


def extract_module(path: str) -> str:
    """Extract module from file path using current config."""
    return get_module_config().extract_module(path)


def extract_file_change(pr_number: int, file_data: dict) -> FileChange:
    """Extract file change data from GitHub API response."""
    filename = file_data.get("filename", "")

    return FileChange(
        pr_number=pr_number,
        filename=filename,
        status=file_data.get("status", "modified"),
        additions=file_data.get("additions", 0),
        deletions=file_data.get("deletions", 0),
        changes=file_data.get("changes", 0),
        module=extract_module(filename),
    )
