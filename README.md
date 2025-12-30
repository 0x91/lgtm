# lgtm

Is code review actually adding value, or is it just a rubber stamp?

This tool extracts PR and review data from GitHub into Parquet files, then analyzes code review patterns to understand how your team reviews each other's work.

## Quick Start

```bash
# Install as a tool
uv tool install git+https://github.com/0x91/lgtm.git

# Go to any repo with a GitHub remote
cd your-repo

# Set your GitHub token
export GITHUB_TOKEN=ghp_xxx

# Fetch PR data from GitHub (auto-detects repo from git remote)
lgtm fetch

# Generate the narrative report
lgtm report
```

Or run from source:

```bash
git clone https://github.com/0x91/lgtm.git
cd lgtm
uv sync --all-extras  # Install all optional features

# Run against any repo
cd /path/to/your-repo
export GITHUB_TOKEN=ghp_xxx
uv run lgtm fetch
uv run lgtm report
```

### Optional Extras

Install only what you need:

```bash
uv tool install 'lgtm[sentiment]'  # Sentiment analysis for review comments
uv tool install 'lgtm[pdf]'        # PDF report export
uv tool install 'lgtm[ai]'         # AI/MCP server integration
uv tool install 'lgtm[all]'        # All optional features
```

## Commands

| Command | Description |
|---------|-------------|
| `lgtm fetch` | Pull PR/review data from GitHub API into `~/.cache/lgtm/{owner}/{repo}/` |
| `lgtm report` | Generate narrative report answering "Is review adding value?" |
| `lgtm analyze` | Run all 35 analysis queries (raw table output) |
| `lgtm init` | Auto-generate `lgtm.yaml` from package manager workspaces |
| `lgtm chat` | Interactive AI chat for exploring code review patterns (requires `lgtm[ai]`) |
| `lgtm ask` | Ask a single question about code review patterns (requires `lgtm[ai]`) |
| `lgtm mcp` | Start MCP server for AI assistant integration (requires `lgtm[ai]`) |

### Fetch Options

```bash
lgtm fetch                    # Incremental fetch (only new PRs since last run)
lgtm fetch --full             # Full fetch from start_date (ignore checkpoint)
lgtm fetch --since 2024-06-01 # Override start date
lgtm fetch --limit 100        # Limit to 100 PRs
lgtm fetch --refresh-days 7   # Re-fetch PRs from the last 7 days
```

### Report Options

```bash
lgtm report                   # Terminal output (default)
lgtm report --format pdf      # Export as PDF (requires lgtm[pdf])
lgtm report -f pdf -o report.pdf  # Custom output path
```

## Analysis Queries

The tool runs 35 queries organized into categories:

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
| `brief_comments` | Short comments analysis (length distribution) |
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

### Collaboration Context
| Query | Description |
|-------|-------------|
| `module_experts` | Top authors per module (who knows this code) |
| `module_reviewers` | Top reviewers per module (who reviews this area) |
| `collaboration_pairs` | Author-reviewer pairs with history (prs together, shared modules) |
| `module_collaboration` | Who reviews whom in which modules |
| `informed_approvals` | Empty approvals with context (expert vs first-time reviewer) |

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
# Fetch settings
fetch:
  start_date: "2024-01-01"  # Only fetch PRs after this date

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

## Bot Detection

The tool automatically identifies bot accounts to separate automated activity from human reviews. This helps answer questions like "what's our human review coverage?" and "which bots are most active?"

### Default behavior

By default, a user is considered a bot if:
- Their GitHub login ends with `[bot]` (e.g., `renovate[bot]`, `dependabot[bot]`)
- Their GitHub API user type is `"Bot"`

### Built-in known bots

The tool recognizes common CI/CD and automation bots:
- `cursor[bot]`, `github-actions[bot]`, `renovate[bot]`, `dependabot[bot]`
- `incident-io[bot]`, `aikido-security[bot]`, `linear[bot]`

### Custom bot configuration

Add a `bots` section to `lgtm.yaml` to configure additional bots:

```yaml
bots:
  # Glob patterns to match bot logins (merged with defaults)
  patterns:
    - "ci-*"              # Match ci-runner, ci-deploy, etc.
    - "*-automation"      # Match deploy-automation, test-automation

  # Specific logins to treat as bots
  logins:
    - "jenkins-user"
    - "internal-deploy-bot"

  # Set false to only use your custom patterns (disable *[bot] default)
  include_defaults: true
```

## Data Storage

Data is stored globally in `~/.cache/lgtm/{owner}/{repo}/raw/`:

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
FROM '~/.cache/lgtm/your-org/your-repo/raw/reviews.parquet'
WHERE state = 'APPROVED'
GROUP BY 1 ORDER BY 3 DESC;
```

## Repository Detection

The tool detects which repository to analyze in this order:

1. **Git remote** (default) - Run `lgtm fetch` from any directory with a GitHub remote
2. **Environment variables** - `REPO_OWNER` and `REPO_NAME`
3. **lgtm.yaml** - Add a `repo` section:

```yaml
repo:
  owner: your-org
  name: your-repo
```

This is useful when analyzing a different repo from the lgtm source directory.

## Environment Config

Required:

```bash
GITHUB_TOKEN=ghp_xxx   # Or use GitHub App (see below)
```

For higher rate limits (15k+/hr vs 5k), use a GitHub App:

```bash
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=12345678
GITHUB_APP_PRIVATE_KEY_PATH=./your-app.pem
```

## AI Chat Interface

Talk to your code review data using natural language. Supports Claude, OpenAI, and Gemini.

### Interactive Chat

```bash
# Install with AI extras
uv tool install 'lgtm[ai]'

# Start interactive chat (uses Claude by default)
lgtm chat

# Use a different model
lgtm chat --model gpt-4o
lgtm chat --model gemini/gemini-1.5-pro
```

### One-shot Questions

```bash
# Ask a single question
lgtm ask "Who does the most code reviews?"
lgtm ask "What modules have the slowest review times?" --model gpt-4o
```

### Example Conversations

```
You: Who reviews the most PRs?

LGTM: Looking at the review data...

Top reviewers by volume:
1. alice - 342 reviews (32% of all human reviews)
2. bob - 298 reviews (28%)

Context: alice reviews across 12 different modules, suggesting a
senior/staff role. bob focuses almost entirely on frontend/ (94%).

Worth exploring: Is alice's load sustainable?
```

### Configuration

Set your API key for your preferred provider:

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx  # For Claude
export OPENAI_API_KEY=sk-xxx         # For OpenAI
export GEMINI_API_KEY=xxx            # For Gemini
```

Optionally add team context in `lgtm.yaml`:

```yaml
chat:
  model: claude-sonnet-4-20250514
  custom_context: |
    Our team values async code review. Fast approvals
    are expected for small, well-tested changes.
```

## MCP Server Integration

For AI assistants like Claude Desktop, you can expose the tools via MCP:

```bash
# Start the MCP server
lgtm mcp
```

Add to Claude Desktop settings (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "lgtm": {
      "command": "lgtm-mcp"
    }
  }
}
```

### Available Tools

| Tool | Description |
|------|-------------|
| `get_overview` | Summary stats: total PRs, reviews, top reviewers, date range |
| `query` | Run DuckDB SQL queries against the analysis database |
| `get_red_flags` | Find PRs that may have slipped through review |
| `get_reviewer_stats` | Detailed stats for a specific reviewer |
| `get_author_stats` | Detailed stats for a specific PR author |

## License

MIT
