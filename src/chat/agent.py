"""LLM agent for code review analysis.

Uses litellm for multi-provider support (Claude, OpenAI, Gemini).
Embodies the "compassionate code review" philosophy in its responses.
"""

from __future__ import annotations

import json
from typing import Any

# Check if AI dependencies are available
AI_AVAILABLE = False

try:
    import litellm

    AI_AVAILABLE = True
except ImportError:
    litellm = None

from ..mcp_server import (
    get_author_stats,
    get_overview,
    get_red_flags,
    get_reviewer_stats,
    query_data,
)

SYSTEM_PROMPT = """You are LGTM, an AI assistant that helps teams understand their code review patterns.

## Your Philosophy

You believe in **compassionate code review**. Your goal isn't to find "bad reviewers" or shame people—it's to help teams understand how they support each other through code review.

### Guiding Principles

1. **Metrics illuminate, they don't accuse**
   - "Empty approval rate" isn't about rubber-stamping—it might indicate trust, paired programming, or areas to explore.
   - Always provide context for numbers.

2. **Activity ≠ Quality**
   - Lots of comments doesn't mean good review.
   - Few comments might mean the code was excellent, or the reviewer was thoughtful and concise.

3. **Context matters**
   - An empty "LGTM" from a trusted colleague who paired on the code IS valid review.
   - Quick approvals on small, well-tested changes are healthy.

4. **Both perspectives matter**
   - Consider the author's experience waiting for feedback.
   - Consider the reviewer's workload and expertise.

5. **Code review is teaching**
   - Look for mentorship signals, not just gate-keeping.
   - Celebrate knowledge sharing.

### When Presenting Data

- Always explain what the numbers mean in context
- Highlight when low/high numbers might actually be healthy
- Suggest questions to dig deeper rather than jumping to conclusions
- Frame insights as "worth understanding" not "problems to fix"
- Use collaborative language ("we", "the team") not accusatory language

### Available Tools

You have tools to query the code review database. The database contains data about:
- Pull requests (prs): size, author, merge status, timing
- Reviews: approvals, change requests, comments
- Review comments: inline code feedback
- Files: what changed in each PR, modules
- Timeline events: PR lifecycle

Use the tools to answer questions. Always interpret results through the compassionate lens above.
"""

# Tool definitions for litellm
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_overview",
            "description": "Get a high-level overview of the code review data including total PRs, reviews, top reviewers, and date range. Good starting point for understanding the dataset.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reviewer_stats",
            "description": "Get detailed statistics for a specific reviewer including total reviews, approval rate, empty review rate, average time to review, and top authors they review. Use to understand someone's review patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reviewer": {
                        "type": "string",
                        "description": "GitHub username of the reviewer",
                    },
                },
                "required": ["reviewer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_author_stats",
            "description": "Get statistics for a PR author including total PRs, merge rate, average PR size, and top reviewers of their work. Use to understand how someone's code gets reviewed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "string",
                        "description": "GitHub username of the author",
                    },
                },
                "required": ["author"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_red_flags",
            "description": "Find PRs that might warrant a closer look - large PRs approved very quickly with no comments. Remember: these aren't necessarily problems, just patterns worth understanding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 20, max 100)",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sql",
            "description": "Run a custom DuckDB SQL query against the code review data. Available tables: prs, reviews, review_comments, pr_comments, files, checks, timeline_events. Use for questions the other tools can't answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "DuckDB SQL query to execute (read-only)",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]

# Map tool names to functions
TOOL_FUNCTIONS = {
    "get_overview": lambda **kwargs: get_overview(),
    "get_reviewer_stats": lambda reviewer, **kwargs: get_reviewer_stats(reviewer),
    "get_author_stats": lambda author, **kwargs: get_author_stats(author),
    "get_red_flags": lambda limit=20, **kwargs: get_red_flags(limit),
    "query_sql": lambda sql, **kwargs: query_data(sql),
}


class LGTMAgent:
    """AI agent for code review analysis conversations.

    Uses litellm for multi-provider LLM support and tools to query
    the code review database.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        custom_context: str | None = None,
    ):
        """Initialize the agent.

        Args:
            model: litellm model string (e.g., "claude-sonnet-4-20250514", "gpt-4o", "gemini/gemini-1.5-pro")
            custom_context: Optional additional context to add to system prompt
        """
        if not AI_AVAILABLE:
            raise ImportError(
                "AI chat requires optional dependencies. "
                "Install with: pip install lgtm[ai]"
            )

        self.model = model
        self.messages: list[dict[str, Any]] = []

        # Build system prompt
        system_prompt = SYSTEM_PROMPT
        if custom_context:
            system_prompt += f"\n\n## Team Context\n\n{custom_context}"

        self.messages.append({"role": "system", "content": system_prompt})

    def chat(self, user_input: str) -> str:
        """Process user input and return response.

        Handles tool calls automatically in a loop until the LLM
        produces a final response.

        Args:
            user_input: User's message

        Returns:
            Assistant's response text
        """
        self.messages.append({"role": "user", "content": user_input})

        # Keep calling until we get a response without tool calls
        max_iterations = 10
        for _ in range(max_iterations):
            response = litellm.completion(
                model=self.model,
                messages=self.messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            assistant_message = response.choices[0].message

            # Check for tool calls
            if assistant_message.tool_calls:
                # Add assistant's message with tool calls
                self.messages.append(assistant_message.model_dump())

                # Execute each tool call
                for tool_call in assistant_message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    # Execute the tool
                    try:
                        if tool_name in TOOL_FUNCTIONS:
                            result = TOOL_FUNCTIONS[tool_name](**tool_args)
                        else:
                            result = {"error": f"Unknown tool: {tool_name}"}
                    except Exception as e:
                        result = {"error": str(e)}

                    # Add tool result
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, default=str),
                    })
            else:
                # No tool calls, we have the final response
                content = assistant_message.content or ""
                self.messages.append({"role": "assistant", "content": content})
                return content

        return "I got stuck in a loop. Please try rephrasing your question."

    def reset(self):
        """Clear conversation history (keep system prompt)."""
        self.messages = [self.messages[0]]

    def get_history(self) -> list[dict[str, Any]]:
        """Get conversation history for export."""
        return [
            msg for msg in self.messages
            if msg.get("role") in ("user", "assistant")
        ]
