"""Configuration for GitHub data extraction.

Only secrets (auth tokens) are configured via environment variables.
All other config goes in lgtm.yaml.
"""

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

# End date is always "now" - start date comes from lgtm.yaml or CLI
END_DATE = datetime.now(UTC)

# Extraction settings
PER_PAGE = 100  # Max items per API page
