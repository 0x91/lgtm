"""Terminal UI for code review chat.

Uses rich for a clean, beautiful terminal chat interface.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from .agent import AI_AVAILABLE, LGTMAgent


class ChatTUI:
    """Terminal chat interface for LGTM.

    Provides a conversational interface for exploring code review patterns.
    """

    WELCOME_MESSAGE = """
# LGTM - Code Review Analysis Chat

Ask questions about your code review patterns. I'll help you understand
how your team reviews code—with context and without judgment.

**Example questions:**
- "Who does the most code reviews?"
- "Tell me about alice's review patterns"
- "Are there any patterns worth looking at?"
- "What's the overview of our review data?"

**Commands:**
- `/help` - Show this help message
- `/clear` - Clear conversation history
- `/export` - Export chat to markdown file
- `/quit` or `/exit` - Exit the chat

---
"""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        custom_context: str | None = None,
    ):
        """Initialize the chat TUI.

        Args:
            model: litellm model string
            custom_context: Optional team-specific context
        """
        if not AI_AVAILABLE:
            raise ImportError(
                "AI chat requires optional dependencies. "
                "Install with: pip install lgtm[ai]"
            )

        self.console = Console()
        self.agent = LGTMAgent(model=model, custom_context=custom_context)
        self.model = model

    def run(self):
        """Run the interactive chat loop."""
        # Print welcome
        self.console.print(Markdown(self.WELCOME_MESSAGE))
        self.console.print(f"[dim]Using model: {self.model}[/dim]\n")

        while True:
            try:
                # Get user input
                user_input = self._get_input()

                if user_input is None:
                    # EOF (Ctrl+D)
                    self.console.print("\n[dim]Goodbye![/dim]")
                    break

                user_input = user_input.strip()

                if not user_input:
                    continue

                # Handle commands
                if user_input.startswith("/"):
                    if self._handle_command(user_input):
                        continue
                    else:
                        break  # Exit command

                # Process with agent
                self._process_message(user_input)

            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /quit to exit[/dim]")
                continue

    def _get_input(self) -> str | None:
        """Get input from user with styled prompt."""
        try:
            self.console.print("[bold blue]You:[/bold blue] ", end="")
            return input()
        except EOFError:
            return None

    def _handle_command(self, command: str) -> bool:
        """Handle a slash command.

        Returns:
            True to continue chat loop, False to exit
        """
        cmd = command.lower().split()[0]

        if cmd in ("/quit", "/exit", "/q"):
            self.console.print("[dim]Goodbye![/dim]")
            return False

        elif cmd in ("/help", "/h", "/?"):
            self.console.print(Markdown(self.WELCOME_MESSAGE))

        elif cmd in ("/clear", "/c"):
            self.agent.reset()
            self.console.clear()
            self.console.print("[dim]Conversation cleared.[/dim]\n")

        elif cmd == "/export":
            self._export_chat()

        elif cmd == "/model":
            parts = command.split(maxsplit=1)
            if len(parts) > 1:
                new_model = parts[1]
                self.agent = LGTMAgent(model=new_model)
                self.model = new_model
                self.console.print(f"[dim]Switched to model: {new_model}[/dim]\n")
            else:
                self.console.print(f"[dim]Current model: {self.model}[/dim]")
                self.console.print("[dim]Usage: /model <model_name>[/dim]\n")

        else:
            self.console.print(f"[yellow]Unknown command: {cmd}[/yellow]")
            self.console.print("[dim]Type /help for available commands[/dim]\n")

        return True

    def _process_message(self, user_input: str):
        """Process a user message and display response."""
        self.console.print()

        # Show thinking indicator
        with Live(
            Spinner("dots", text="Thinking...", style="dim"),
            console=self.console,
            transient=True,
        ):
            try:
                response = self.agent.chat(user_input)
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]\n")
                return

        # Display response
        self.console.print("[bold green]LGTM:[/bold green]")
        self.console.print(Markdown(response))
        self.console.print()

    def _export_chat(self):
        """Export conversation to markdown file."""
        history = self.agent.get_history()

        if not history:
            self.console.print("[yellow]No conversation to export.[/yellow]\n")
            return

        # Build markdown
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"lgtm_chat_{timestamp}.md"

        lines = [
            "# LGTM Chat Export",
            f"\nExported: {datetime.now().isoformat()}",
            f"Model: {self.model}",
            "\n---\n",
        ]

        for msg in history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user":
                lines.append(f"**You:** {content}\n")
            elif role == "assistant":
                lines.append(f"**LGTM:**\n\n{content}\n")
            lines.append("---\n")

        # Write file
        path = Path(filename)
        path.write_text("\n".join(lines))
        self.console.print(f"[green]Exported to {filename}[/green]\n")


def main(model: str = "claude-sonnet-4-20250514"):
    """Main entry point for chat TUI."""
    try:
        tui = ChatTUI(model=model)
        tui.run()
    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
