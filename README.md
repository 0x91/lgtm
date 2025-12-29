# lgtm

Is code review actually adding value, or is it just a rubber stamp?

This tool extracts all PR and review data from a GitHub repo into Parquet files so you can answer that question with data instead of opinions.

## Quick Start

```bash
git clone https://github.com/0x91/lgtm.git
cd lgtm
uv sync

cp .env.example .env
# Edit .env with your repo and credentials

uv run extract
```

## What You Get

Eight normalized Parquet tables: PRs, reviews, comments, files, checks, timeline events. Query with DuckDB:

```sql
-- Who rubber-stamps the most?
SELECT reviewer_login, COUNT(*) as approvals,
       SUM(CASE WHEN body = '' THEN 1 ELSE 0 END) as empty_approvals
FROM 'data/raw/reviews.parquet'
WHERE state = 'APPROVED'
GROUP BY 1 ORDER BY 3 DESC;
```

## Config

All config lives in `.env` (gitignored):

```bash
REPO_OWNER=your-org
REPO_NAME=your-repo
START_DATE=2025-01-01
GITHUB_TOKEN=ghp_xxx
```

For higher rate limits, use a GitHub App instead of a PAT.

## License

MIT
