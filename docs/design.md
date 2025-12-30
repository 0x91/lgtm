# LGTM: Code Review Quality Analysis - Design Notes

## 1. Expanded Analysis Coverage

### Current Queries (8)
- rubber_stamp_rate - Empty approvals
- time_to_review - Time to first review
- review_coverage - Human vs bot vs none
- who_reviews_whom - Reviewer-author pairs
- substantive_reviewers - Inline comment counts
- bot_activity - Bot review stats
- module_coverage - Review rate by module
- pr_size_vs_review - Size vs review depth

### Proposed Additions

**Review Quality Metrics:**
- `review_depth` - Reviews with inline comments vs just approvals
- `review_iteration_count` - How many rounds of changes_requested → re-review
- `stale_approvals` - Approved but commits pushed after approval
- `drive_by_reviews` - Single-word comments ("lgtm", "nit", etc.)
- `self_review_ratio` - Authors commenting on their own PRs

**Temporal Patterns:**
- `review_by_time` - Review activity by hour/day of week
- `review_latency_by_author` - Who waits longest for reviews?
- `review_latency_by_module` - Which areas get slow reviews?
- `time_in_review` - Total time from first review to merge

**Team Dynamics:**
- `review_reciprocity` - Do people review each other equally?
- `reviewer_load_balance` - Distribution of review work
- `cross_team_reviews` - Reviews outside your usual module (if team data available)

**Risk Indicators:**
- `large_pr_no_comments` - Big changes with no inline feedback
- `quick_approve_large_pr` - Approved in <5min for 500+ line changes
- `single_reviewer_merges` - Only one human reviewer before merge

---

## 2. Sentiment Analysis on Review Comments

### Goal
Classify review comments by quality/tone to measure "how good are our reviews?":
- **Constructive**: Actionable feedback, explains why
- **Nitpick**: Style/minor issues, low value
- **Question**: Seeking clarification
- **Praise**: Positive feedback
- **Concern**: Flags potential issues
- **Blocking**: Must fix before merge

### Approaches

**Option A: Classic ML (scikit-learn / NLTK) ✅ Default**
```python
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from sklearn.naive_bayes import MultinomialNB

# VADER for tone (positive/negative/neutral)
sia = SentimentIntensityAnalyzer()
scores = sia.polarity_scores(comment)

# Trained classifier for category
# (nitpick, constructive, question, etc.)
category = classifier.predict([comment])[0]
```
- Pros: Fast, free, runs on any laptop, no API keys
- Cons: Less nuanced than LLM, needs training data for categories
- Training data: Can bootstrap from keyword patterns, then refine

**Option B: LLM via litellm (Haiku/Gemini Flash)**
```python
import litellm

async def classify_comment(comment: str) -> dict:
    """Use cheap/fast LLM for nuanced classification."""
    response = await litellm.acompletion(
        model="claude-3-haiku-20240307",  # or "gemini/gemini-1.5-flash"
        messages=[{
            "role": "user",
            "content": f"""Classify this code review comment:
            "{comment}"

            Return JSON: {{"category": "...", "tone": "...", "actionability": 1-5}}
            Categories: constructive, nitpick, question, praise, concern, blocking
            """
        }],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)
```
- Pros: High accuracy, handles nuance, no training data needed
- Cons: Cost (~$0.001/comment), requires API key

**Recommended: Hybrid**
1. Use VADER for tone (free, instant)
2. Use keyword patterns for obvious categories (free, instant)
3. Optional: LLM enrichment for ambiguous cases (configurable)

### Proposed Schema Addition
```sql
-- review_comment_analysis.parquet
comment_id: str
category: str  -- constructive, nitpick, question, praise, concern, blocking
specificity: int  -- 1-5, how specific is the feedback
actionability: int  -- 1-5, how actionable
tone: str  -- positive, neutral, negative
```

---

## 3. Module Configuration Interface

### Problem
Current `extract_module()` is hardcoded for cogna-co/core structure:
```python
if parts[0] in ('backend', 'frontend', 'app-runtime'):
    if len(parts) >= 3:
        return '/'.join(parts[:3])
```

### Requirements
- User-configurable without code changes
- Support different repo structures
- Fallback to sensible defaults
- Can be specified per-repo

### Proposed: YAML Config

```yaml
# lgtm.yaml (or .lgtm.yaml in repo root)
repo:
  owner: cogna-co
  name: core

modules:
  # Rule-based extraction
  rules:
    - pattern: "backend/{lang}/{name}/**"
      module: "backend/{lang}/{name}"
    - pattern: "frontend/{name}/**"
      module: "frontend/{name}"
    - pattern: "proto/{name}/**"
      module: "proto/{name}"
    - pattern: ".github/**"
      module: ".github"

  # Default: use first N path components
  default_depth: 2

  # Or: explicit mapping
  explicit:
    "package.json": "root"
    "uv.lock": "root"

# Optional: team ownership (for cross-team analysis)
teams:
  backend:
    - "backend/py/*"
    - "backend/go-servers/*"
  frontend:
    - "frontend/*"
    - "frontend-packages/*"
```

### Implementation

```python
# src/config.py
from dataclasses import dataclass
from pathlib import Path
import yaml
import re

@dataclass
class ModuleRule:
    pattern: str  # glob-like pattern with {captures}
    module: str   # output template

@dataclass
class ModuleConfig:
    rules: list[ModuleRule]
    default_depth: int = 2
    explicit: dict[str, str] = None

    @classmethod
    def load(cls, path: Path = None) -> "ModuleConfig":
        """Load from yaml or return defaults."""
        if path is None:
            path = Path("lgtm.yaml")
        if not path.exists():
            return cls.default()
        with open(path) as f:
            data = yaml.safe_load(f)
        # parse...

    @classmethod
    def default(cls) -> "ModuleConfig":
        """Sensible defaults for unknown repos."""
        return cls(
            rules=[],
            default_depth=2,
        )

    def extract_module(self, filepath: str) -> str:
        """Extract module from filepath using config."""
        # Check explicit mappings first
        if self.explicit and filepath in self.explicit:
            return self.explicit[filepath]

        # Try rules in order
        for rule in self.rules:
            if match := self._match_pattern(rule.pattern, filepath):
                return rule.module.format(**match)

        # Fallback to default depth
        parts = filepath.split("/")
        return "/".join(parts[:self.default_depth]) or parts[0]
```

### CLI Integration
```bash
# Generate config template
uv run lgtm init

# Use specific config
uv run extract --config lgtm.yaml

# Analyze with config
uv run analyze --config lgtm.yaml
```

---

## 4. AI Chat Interface (Future)

### Goal
Let users query their code review data conversationally after extraction.

### Architecture
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  User's LLM     │────▶│   MCP Server    │────▶│   DuckDB        │
│  (Claude/GPT/   │     │   (lgtm serve)  │     │   (parquet)     │
│   Gemini)       │◀────│                 │◀────│                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### MCP Server
```python
# src/mcp_server.py
from mcp import Server, Tool

server = Server("lgtm")

@server.tool()
def query_reviews(sql: str) -> str:
    """Run SQL query against code review data."""
    con = get_connection()
    return con.execute(sql).df().to_markdown()

@server.tool()
def get_reviewer_stats(reviewer: str) -> dict:
    """Get statistics for a specific reviewer."""
    ...

@server.tool()
def get_pr_details(pr_number: int) -> dict:
    """Get full details for a PR including reviews and comments."""
    ...

@server.tool()
def compare_reviewers(reviewer1: str, reviewer2: str) -> dict:
    """Compare two reviewers' patterns."""
    ...

# Resources for context
@server.resource("schema")
def get_schema() -> str:
    """Return database schema for LLM context."""
    return """
    Tables: prs, reviews, review_comments, pr_comments, files, ...
    Key columns: ...
    """
```

### CLI
```bash
# Start MCP server
uv run lgtm serve --port 8080

# Or stdio mode for Claude Desktop
uv run lgtm mcp
```

### User Experience
1. Run extraction: `uv run extract`
2. Start server: `uv run lgtm serve`
3. Connect Claude/GPT via MCP
4. Ask: "Who are my most thorough reviewers?" / "Show me PRs that got rubber-stamped" / "What modules have the slowest review times?"

### litellm Integration
For programmatic access without MCP:
```python
import litellm
from lgtm import get_connection

tools = [
    {"type": "function", "function": {"name": "query_reviews", ...}},
    ...
]

response = litellm.completion(
    model="gpt-4",  # or claude, gemini
    messages=[{"role": "user", "content": "Who reviews the most PRs?"}],
    tools=tools
)
```

---

## Implementation Order

1. **Module config** (pattern-based YAML)
2. **Expand queries** (low-hanging fruit first)
3. **Sentiment analysis** (NLTK/VADER default, LLM optional)
4. **MCP server** (after core analysis is solid)
5. **AI chat polish** (resources, better tools)
