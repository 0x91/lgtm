# lgtm

Is code review actually adding value, or is it just a rubber stamp?

This tool extracts PR and review data from GitHub into Parquet files, then analyzes code review patterns to understand how your team reviews each other's work.

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
| `lgtm analyze` | Run all 30 analysis queries on extracted data |

## Analysis Queries

The tool runs 30 queries organized into categories:

### Core Metrics
| Query | Description |
|-------|-------------|
| `rubber_stamp_rate` | Empty approval rate by reviewer (no comment, just click approve) |
| `time_to_review` | Time to first human review (avg, median, p90) |
| `review_coverage` | % of PRs with human review vs bot-only vs none |
| `who_reviews_whom` | Top reviewer-author pairs (who reviews whose code) |
| `substantive_reviewers` | Reviewers who leave inline code comments |
| `bot_activity` | Review activity by bots (cursor, renovate, etc.) |
| `module_coverage` | Review activity by module/area of codebase |
| `pr_size_vs_review` | Does PR size correlate with review depth? |

### Review Quality
| Query | Description |
|-------|-------------|
| `review_depth` | Inline comments per reviewer (engagement metric) |
| `review_iterations` | Rounds of changes_requested before approval |
| `stale_approvals` | PRs with commits pushed after approval |
| `drive_by_reviews` | Short/low-value comments ("lgtm", "nice", "+1") |
| `self_review_activity` | Authors commenting on their own PRs |

### Temporal Patterns
| Query | Description |
|-------|-------------|
| `review_by_time` | Review activity by day of week |
| `review_latency_by_author` | Which authors wait longest for reviews? |
| `review_latency_by_module` | Which modules get slow reviews? |
| `time_in_review` | Total time from first review to merge |

### Team Dynamics
| Query | Description |
|-------|-------------|
| `review_reciprocity` | Do people review each other equally? |
| `reviewer_load_balance` | Distribution of review work across team |

### Risk Indicators
| Query | Description |
|-------|-------------|
| `large_pr_no_comments` | Large PRs merged with no inline feedback |
| `quick_approve_large_pr` | Large PRs approved in <5 minutes |
| `single_reviewer_merges` | PRs merged with only one reviewer |

### Code Review Quality
| Query | Description |
|-------|-------------|
| `code_review_depth` | Review depth on real code (excluding generated) |
| `pr_type_review_depth` | Review depth by PR type (new-code vs refactor) |
| `conventional_commits` | Conventional commit adoption by author |
| `underreviewed_code` | Large code PRs with no substantive review |

## Module Configuration

Modules group files into logical areas of your codebase. This helps answer "which parts of the system get reviewed most thoroughly?"

### Auto-generate from workspaces

```bash
uv run lgtm init
```

Detects and parses:
- `pnpm-workspace.yaml` (pnpm)
- `package.json` workspaces (npm/yarn)
- `pyproject.toml` with `[tool.uv.workspace]` (uv)
- `BUILD.bazel` files (bazel)

### Built-in defaults

The tool includes sensible defaults that work for most monorepos:

**Module patterns** (first match wins):
- `src/{name}/**` → `src/{name}`
- `packages/{name}/**` → `packages/{name}`
- `apps/{name}/**` → `apps/{name}`
- `.github/**` → `.github`

**Root files** (assigned to "root" module):
- Dotfiles (`.*`), docs (`*.md`), lock files (`*.lock`)
- Build configs (`Makefile`, `Dockerfile*`, `*.toml`, `*.yaml`)
- Go modules (`go.mod`, `go.sum`)
- Bazel (`WORKSPACE`, `BUILD.bazel`, `MODULE.bazel`)
- Package managers (`package.json`, `Cargo.toml`, `pyproject.toml`)

**Generated file detection** (excluded from code review metrics):
- Lock files (`*.lock`, `package-lock.json`, `go.sum`)
- Protobuf (`*.pb.go`, `*.pb.ts`, `*_pb2.py`)
- Codegen directories (`*/gen/*`, `*/generated/*`)
- Snapshots (`*/__snapshots__/*`, `*.snap`)
- Minified (`*.min.js`, `*.bundle.js`)

### Manual configuration

Create `lgtm.yaml` in your repo root to customize:

```yaml
modules:
  rules:
    # Capture patterns: {name} matches a single path segment
    - pattern: backend/py/{name}/**
      module: backend/py/{name}
    - pattern: frontend/**
      module: frontend
    - pattern: proto/gen/**
      module: proto/gen

  # Fallback depth for unmatched paths (default: 2)
  default_depth: 2

  # Custom generated patterns (merged with defaults)
  generated_patterns:
    - "custom/autogen/*"

  # Set false to only use your custom patterns
  include_default_generated: true
```

Only include repo-specific rules—the built-in defaults handle common patterns.

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
