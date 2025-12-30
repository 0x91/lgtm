"""Configuration for GitHub data extraction."""

import os
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

# Auth: PAT (simple) or GitHub App (higher rate limits)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# GitHub App auth (optional, takes precedence over PAT if all are set)
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID")
GITHUB_APP_PRIVATE_KEY_PATH = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID")

# Date range (START_DATE as ISO string, e.g., "2025-01-01")
_start = os.environ.get("START_DATE", "2025-01-01")
START_DATE = datetime.fromisoformat(_start).replace(tzinfo=UTC)
END_DATE = datetime.now(UTC)

# Extraction settings
CHECKPOINT_INTERVAL = 100  # Save checkpoint every N PRs
PER_PAGE = 100  # Max items per API page
