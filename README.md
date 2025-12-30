# lgtm

Is code review actually adding value, or is it just a rubber stamp?

This tool extracts PR and review data from GitHub into Parquet files, then analyzes code review patterns to identify risk, complexity, and review effort across your codebase.

## Quick Start

```bash
git clone https://github.com/0x91/lgtm.git
cd lgtm
uv sync

cp .env.example .env
# Edit .env with your repo and credentials

# Generate module config from your monorepo structure
uv run lgtm init

# Extract PR data from GitHub
uv run lgtm extract

# Run analysis queries
uv run lgtm analyze
```

## Commands

| Command | Description |
|---------|-------------|
| `lgtm init` | Auto-generate `lgtm.yaml` from package manager workspaces |
| `lgtm extract` | Pull PR/review data from GitHub API into Parquet files |
| `lgtm analyze` | Run analysis queries on extracted data |

## Module Configuration

Modules group files into logical areas of your codebase for analysis. This helps answer "which parts of the system get reviewed most thoroughly?"

### Auto-generate from workspaces

```bash
uv run lgtm init
```

Detects and parses:
- `pnpm-workspace.yaml` (pnpm)
- `package.json` workspaces (npm/yarn)
- `pyproject.toml` with `[tool.uv.workspace]` (uv)
- `BUILD.bazel` files (bazel)

### Manual configuration

Create `lgtm.yaml` in your repo root:

```yaml
modules:
  rules:
    # Capture patterns: {name} matches a path segment
    - pattern: backend/py/{name}/**
      module: backend/py/{name}
    - pattern: frontend/**
      module: frontend
    - pattern: proto/gen/**
      module: proto/gen

  # Fallback for unmatched paths
  default_depth: 2

  # Root-level files (dotfiles, configs) -> "root" module
  root_patterns:
    - ".*"
    - "*.md"
    - "*.lock"
```

If no `lgtm.yaml` exists, defaults are used (configured for cogna-co/core monorepo).

## What You Get

Eight normalized Parquet tables in `data/raw/`:

| Table | Description |
|-------|-------------|
| `prs.parquet` | Pull requests with metadata |
| `reviews.parquet` | Review submissions (approve/comment/request changes) |
| `review_comments.parquet` | Inline code comments |
| `pr_comments.parquet` | PR-level discussion comments |
| `files.parquet` | Files changed per PR with module assignment |
| `checks.parquet` | CI check runs |
| `timeline_events.parquet` | PR lifecycle events |
| `users.parquet` | User dimension (human vs bot) |

Query with DuckDB:

```sql
-- Who rubber-stamps the most?
SELECT reviewer_login, COUNT(*) as approvals,
       SUM(CASE WHEN body = '' THEN 1 ELSE 0 END) as empty_approvals
FROM 'data/raw/reviews.parquet'
WHERE state = 'APPROVED'
GROUP BY 1 ORDER BY 3 DESC;

-- Which modules get the least review attention?
SELECT module, COUNT(DISTINCT pr_number) as prs,
       AVG(review_count) as avg_reviews
FROM 'data/raw/files.parquet' f
JOIN (SELECT pr_number, COUNT(*) as review_count
      FROM 'data/raw/reviews.parquet' GROUP BY 1) r
  ON f.pr_number = r.pr_number
GROUP BY 1 ORDER BY 3;
```

## Environment Config

All secrets live in `.env` (gitignored):

```bash
REPO_OWNER=your-org
REPO_NAME=your-repo
START_DATE=2025-01-01
GITHUB_TOKEN=ghp_xxx
```

For higher rate limits (15k+/hr vs 5k), use a GitHub App instead of a PAT:

```bash
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=12345678
GITHUB_APP_PRIVATE_KEY_PATH=./your-app.pem
```

## License

MIT
