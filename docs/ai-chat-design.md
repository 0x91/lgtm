# AI Chat Interface Design

## North Star Reminder

> The goal isn't to find "bad reviewers" or shame people—it's to understand how we as humans compassionately review each other's work and maintain quality together.

The AI chat must embody this philosophy. Every insight should include context. Every metric should be framed as understanding, not judgment.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     lgtm chat (TUI)                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ You: Who reviews the most PRs?                           │  │
│  │                                                          │  │
│  │ LGTM: Based on the data, here are the most active        │  │
│  │ reviewers:                                               │  │
│  │                                                          │  │
│  │ 1. alice (342 reviews) - Mostly backend/go changes       │  │
│  │ 2. bob (298 reviews) - Frontend specialist               │  │
│  │                                                          │  │
│  │ Note: High review counts often indicate senior devs      │  │
│  │ or those in enabling/platform roles. It's worth checking │  │
│  │ if the load is sustainable.                              │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ > Ask about your code review patterns...                 │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

## Components

### 1. TUI Chat (`src/chat/tui.py`)

Uses `rich` for a clean terminal interface:
- Scrollable chat history
- Markdown rendering for responses
- Typing indicator while LLM thinks
- `/help`, `/clear`, `/export` commands

```python
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt

class ChatTUI:
    """Terminal chat interface for LGTM."""

    def __init__(self, agent: LGTMAgent):
        self.console = Console()
        self.agent = agent
        self.history = []

    def run(self):
        """Main chat loop."""
        self.console.print(Panel("LGTM - Code Review Analysis Chat"))
        self.console.print("Ask questions about your code review patterns.\n")

        while True:
            user_input = Prompt.ask("[bold blue]You[/]")
            if user_input.startswith("/"):
                self.handle_command(user_input)
            else:
                response = self.agent.chat(user_input)
                self.console.print(Markdown(response))
```

### 2. LLM Agent (`src/chat/agent.py`)

Uses `litellm` for multi-provider support:

```python
import litellm
from typing import Callable

class LGTMAgent:
    """AI agent for code review analysis."""

    SYSTEM_PROMPT = """You are LGTM, an assistant that helps teams understand their code review patterns.

Your philosophy:
- Metrics illuminate, they don't accuse. Always provide context.
- An empty "LGTM" from a trusted colleague who paired on the code IS valid review.
- Code review is teaching. Look for mentorship, not just gate-keeping.
- Both author and reviewer perspectives matter.

When presenting data:
- Always explain what the numbers mean in context
- Highlight when low/high numbers might actually be healthy
- Suggest questions to dig deeper rather than conclusions

You have access to tools to query the code review database. Use them to answer questions.
The database contains: prs, reviews, review_comments, pr_comments, files, checks, timeline_events.
"""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.model = model
        self.tools = self._build_tools()
        self.messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

    def chat(self, user_input: str) -> str:
        """Process user input and return response."""
        self.messages.append({"role": "user", "content": user_input})

        response = litellm.completion(
            model=self.model,
            messages=self.messages,
            tools=self.tools,
        )

        # Handle tool calls in a loop
        while response.choices[0].message.tool_calls:
            # Execute tools and add results
            ...

        assistant_msg = response.choices[0].message.content
        self.messages.append({"role": "assistant", "content": assistant_msg})
        return assistant_msg
```

### 3. Tool Definitions

Tools the LLM can use (building on existing MCP tools):

| Tool | Description | When to Use |
|------|-------------|-------------|
| `get_overview` | Summary stats with date range | "What's the overall picture?" |
| `get_reviewer_profile` | Deep dive on one reviewer with context | "Tell me about Alice's reviews" |
| `get_author_profile` | Author's PR patterns and feedback received | "How are Bob's PRs being reviewed?" |
| `find_collaboration_patterns` | Who works with whom, review pairs | "Who reviews frontend code?" |
| `find_concerning_patterns` | Red flags WITH context | "Are there any patterns to watch?" |
| `compare_timeframes` | Trend analysis | "How has review improved this quarter?" |
| `query_custom` | Run DuckDB SQL (sandboxed) | Advanced users only |

### 4. Contextual Framing

Every tool should return data WITH context. Example:

```python
def get_reviewer_profile(reviewer: str) -> dict:
    """Get reviewer profile with compassionate framing."""
    stats = query_reviewer_stats(reviewer)

    # Add context to raw numbers
    context = []

    if stats["empty_approval_rate"] > 50:
        # Don't say "rubber stamps" - add context
        context.append(
            f"Many approvals without comments ({stats['empty_approval_rate']:.0f}%). "
            "This could indicate: trusting relationships with authors, "
            "paired programming culture, or areas to explore with the reviewer."
        )

    if stats["avg_time_to_review"] < 10:  # minutes
        context.append(
            f"Very fast reviews (avg {stats['avg_time_to_review']:.0f} min). "
            "Could be: responsive culture (great!), or worth checking "
            "if review depth matches the PR complexity."
        )

    return {
        "reviewer": reviewer,
        "stats": stats,
        "context": context,
        "suggested_questions": [
            f"What modules does {reviewer} typically review?",
            f"Who does {reviewer} review most often?",
            f"How does {reviewer}'s review depth compare to others?",
        ]
    }
```

### 5. Configuration

In `lgtm.yaml`:

```yaml
chat:
  # LLM provider (uses litellm model strings)
  model: "claude-sonnet-4-20250514"  # or "gpt-4o", "gemini/gemini-1.5-pro"

  # API key environment variable (litellm auto-detects from env)
  # ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY

  # Optional: Custom system prompt additions
  custom_context: |
    Our team values async code review. Fast approvals are expected
    for small changes that have been discussed in Slack.
```

## CLI Integration

```bash
# Start interactive chat
lgtm chat

# With specific model
lgtm chat --model gpt-4o

# One-shot query (for scripting)
lgtm ask "Who reviews the most PRs?"
```

## DuckDB MCP Integration

Two options:

### Option A: Use External MCP Server (Recommended for Claude Desktop)

The [motherduck/mcp-server-motherduck](https://github.com/motherduckdb/mcp-server-motherduck) or
[ktanaka101/mcp-server-duckdb](https://github.com/ktanaka101/mcp-server-duckdb) can be used
alongside `lgtm-mcp` for users who want raw SQL access.

Configure in Claude Desktop:
```json
{
  "mcpServers": {
    "lgtm": {
      "command": "lgtm-mcp"
    },
    "duckdb": {
      "command": "npx",
      "args": ["-y", "@motherduck/mcp-server-motherduck"],
      "env": {
        "DUCKDB_PATH": "~/.cache/lgtm/{owner}/{repo}/analysis.duckdb"
      }
    }
  }
}
```

### Option B: Built-in SQL Tool (For TUI Chat)

Our `query_custom` tool already provides SQL access with:
- Read-only mode
- Timeout limits
- Result size limits

## Implementation Phases

### Phase 1: Basic TUI Chat
- [ ] `src/chat/tui.py` - Rich-based chat interface
- [ ] `src/chat/agent.py` - litellm integration
- [ ] Basic tools: get_overview, query_custom
- [ ] `lgtm chat` command

### Phase 2: Contextual Tools
- [ ] Enhance tools with compassionate framing
- [ ] get_reviewer_profile, get_author_profile
- [ ] find_collaboration_patterns
- [ ] Suggested follow-up questions

### Phase 3: Configuration & Polish
- [ ] Model configuration in lgtm.yaml
- [ ] `/export` to save chat as markdown
- [ ] Conversation memory (save/load sessions)
- [ ] `lgtm ask` one-shot mode

## Example Conversations

**Understanding Review Load:**
```
You: Who does the most code reviews?

LGTM: Looking at the review data...

Top reviewers by volume:
1. alice - 342 reviews (32% of all human reviews)
2. bob - 298 reviews (28%)
3. carol - 156 reviews (15%)

Context: alice reviews across 12 different modules, suggesting a
senior/staff role. bob focuses almost entirely on frontend/ (94% of
their reviews).

Worth exploring: Is alice's load sustainable? Are there areas where
review knowledge could be spread more?

Would you like me to dig into any of these reviewers specifically?
```

**Finding Areas for Improvement:**
```
You: Are there any concerning patterns?

LGTM: Here are some patterns worth understanding:

1. **Large PRs with quick approvals** (23 cases)
   PRs with 500+ lines approved in under 5 minutes with no comments.
   - 18 of these were from trusted pairs (alice→bob, bob→carol)
   - 5 might be worth spot-checking: #1234, #1456, #1789...

2. **Module with declining review coverage** (proto/)
   Review rate dropped from 95% → 78% over Q4.
   - Coincides with carol (main proto reviewer) going on leave
   - Might want to spread proto knowledge

Note: "Concerning" doesn't mean "bad" - these are areas to understand
better, not necessarily problems to fix.
```

## Security Considerations

1. **SQL Injection**: query_custom uses read-only DuckDB with parameterized queries
2. **API Keys**: Never log or display API keys
3. **Data Privacy**: All processing is local; LLM only sees aggregated stats unless user asks for specific PRs

---

Sources:
- [motherduck/mcp-server-motherduck](https://github.com/motherduckdb/mcp-server-motherduck)
- [ktanaka101/mcp-server-duckdb](https://github.com/ktanaka101/mcp-server-duckdb)
