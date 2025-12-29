"""File changes extractor with module detection."""

from ..models import FileChange


def extract_module(path: str) -> str:
    """Extract module from file path based on repo structure.

    Module structure:
    - backend/{lang}/{module}/... -> backend/{lang}/{module}
    - frontend/{lang}/{module}/... -> frontend/{lang}/{module}
    - app-runtime/{lang}/{module}/... -> app-runtime/{lang}/{module}
    - proto/{module}/... -> proto/{module}
    - charts/{module}/... -> charts/{module}
    - frontend-packages/{module}/... -> frontend-packages/{module}
    - shared-packages/{module}/... -> shared-packages/{module}
    """
    parts = path.split("/")

    if not parts:
        return "root"

    # Top-level directories with language subdirs
    if parts[0] in ("backend", "frontend", "app-runtime"):
        if len(parts) >= 3:
            return "/".join(parts[:3])
        elif len(parts) >= 2:
            return "/".join(parts[:2])
        return parts[0]

    # Top-level directories with direct module subdirs
    if parts[0] in ("proto", "charts", "frontend-packages", "shared-packages", "afw-runtime"):
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return parts[0]

    # Default: top-level directory or root
    return parts[0] if parts[0] else "root"


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
