"""Main orchestrator for GitHub data extraction with TUI."""

import json
import logging
import os
import signal
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.layout import Layout
from rich.text import Text

from .config import (
    CHECKPOINT_FILE,
    CHECKPOINT_INTERVAL,
    RAW_DATA_DIR,
    START_DATE,
    END_DATE,
    LOG_FILE,
    DATA_DIR,
    REPO_OWNER,
    REPO_NAME,
)
from .github_client import GitHubClient
from .extractors.prs import extract_pr
from .extractors.reviews import extract_review
from .extractors.comments import extract_pr_comment, extract_review_comment
from .extractors.files import extract_file_change
from .extractors.checks import extract_check_run
from .extractors.timeline import extract_timeline_event
from .extractors.users import extract_user
from .models import (
    PullRequest,
    Review,
    PRComment,
    ReviewComment,
    FileChange,
    CheckRun,
    TimelineEvent,
    User,
)


def setup_logging():
    """Setup file logging for debugging."""
    os.makedirs(DATA_DIR, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="a"),
        ],
    )
    return logging.getLogger(__name__)


logger = setup_logging()


class ExtractionState(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class ErrorRecord:
    """Record of a failed PR extraction."""
    pr_number: int
    error_type: str
    error_message: str
    timestamp: str
    retries: int = 0


@dataclass
class ExtractionStats:
    """Live extraction statistics."""
    total_prs: int = 0
    processed_prs: int = 0
    failed_prs: int = 0
    skipped_prs: int = 0  # Already processed in previous run

    reviews_count: int = 0
    pr_comments_count: int = 0
    review_comments_count: int = 0
    files_count: int = 0
    checks_count: int = 0
    timeline_events_count: int = 0
    users_count: int = 0

    api_requests: int = 0
    rate_limit_remaining: int = 0
    rate_limit_reset: str = ""
    auth_type: str = "pat"  # "pat" or "app"

    last_pr: int = 0
    last_error: str = ""
    state: ExtractionState = ExtractionState.RUNNING


class DataExtractor:
    """Main data extraction orchestrator with TUI."""

    def __init__(self, client: GitHubClient, console: Console):
        self.client = client
        self.console = console

        # Data storage
        self.prs: list[PullRequest] = []
        self.reviews: list[Review] = []
        self.pr_comments: list[PRComment] = []
        self.review_comments: list[ReviewComment] = []
        self.files: list[FileChange] = []
        self.checks: list[CheckRun] = []
        self.timeline_events: list[TimelineEvent] = []
        self.users: dict[int, User] = {}

        # State tracking
        self.processed_prs: set[int] = set()  # PRs saved to disk
        self.pending_prs: list[int] = []  # PRs extracted but not yet saved
        self.failed_prs: dict[int, ErrorRecord] = {}
        self.stats = ExtractionStats()

        # Control flag
        self._stop_requested = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        """Handle Ctrl+C gracefully - save state and exit."""
        self._stop_requested = True
        self.stats.state = ExtractionState.PAUSED
        logger.info("Interrupt received, stopping gracefully...")

    def load_checkpoint(self) -> bool:
        """Load checkpoint if exists."""
        if not os.path.exists(CHECKPOINT_FILE):
            return False

        try:
            with open(CHECKPOINT_FILE) as f:
                checkpoint = json.load(f)

            self.processed_prs = set(checkpoint.get("processed_prs", []))

            # Load failed PRs
            for error_data in checkpoint.get("failed_prs", []):
                pr_num = error_data["pr_number"]
                self.failed_prs[pr_num] = ErrorRecord(**error_data)

            self.stats.skipped_prs = len(self.processed_prs)
            return True
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to load checkpoint: {e}[/]")
            return False

    def save_checkpoint(self):
        """Save current progress to checkpoint file."""
        os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)

        checkpoint = {
            "processed_prs": list(self.processed_prs),
            "failed_prs": [
                {
                    "pr_number": e.pr_number,
                    "error_type": e.error_type,
                    "error_message": e.error_message,
                    "timestamp": e.timestamp,
                    "retries": e.retries,
                }
                for e in self.failed_prs.values()
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "total_processed": len(self.processed_prs),
                "total_failed": len(self.failed_prs),
            },
        }

        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(checkpoint, f, indent=2)

    def save_error_log(self):
        """Save detailed error log."""
        if not self.failed_prs:
            return

        error_log_path = f"{RAW_DATA_DIR}/extraction_errors.json"
        os.makedirs(RAW_DATA_DIR, exist_ok=True)

        with open(error_log_path, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_errors": len(self.failed_prs),
                    "errors": [
                        {
                            "pr_number": e.pr_number,
                            "error_type": e.error_type,
                            "error_message": e.error_message,
                            "timestamp": e.timestamp,
                            "retries": e.retries,
                        }
                        for e in self.failed_prs.values()
                    ],
                },
                f,
                indent=2,
            )

    def track_user(self, user_data: dict):
        """Track unique user."""
        if not user_data:
            return
        user_id = user_data.get("id")
        if user_id and user_id not in self.users:
            self.users[user_id] = extract_user(user_data)
            self.stats.users_count = len(self.users)

    def extract_pr_details(self, pr_number: int) -> bool:
        """Extract all details for a single PR. Returns True on success."""
        errors = []

        # Get full PR details
        pr_data = self.client.get(f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}")
        pr = extract_pr(pr_data)
        self.prs.append(pr)
        self.track_user(pr_data.get("user", {}))

        # Extract reviews
        try:
            reviews_data = self.client.get_pr_reviews(pr_number)
            for review_data in reviews_data:
                self.reviews.append(extract_review(pr_number, review_data))
                self.track_user(review_data.get("user", {}))
            self.stats.reviews_count = len(self.reviews)
        except Exception as e:
            errors.append(f"reviews: {e}")

        # Extract PR comments
        try:
            comments_data = self.client.get_pr_comments(pr_number)
            for comment_data in comments_data:
                self.pr_comments.append(extract_pr_comment(pr_number, comment_data))
                self.track_user(comment_data.get("user", {}))
            self.stats.pr_comments_count = len(self.pr_comments)
        except Exception as e:
            errors.append(f"pr_comments: {e}")

        # Extract review comments
        try:
            review_comments_data = self.client.get_pr_review_comments(pr_number)
            for comment_data in review_comments_data:
                self.review_comments.append(extract_review_comment(pr_number, comment_data))
                self.track_user(comment_data.get("user", {}))
            self.stats.review_comments_count = len(self.review_comments)
        except Exception as e:
            errors.append(f"review_comments: {e}")

        # Extract files
        try:
            files_data = self.client.get_pr_files(pr_number)
            for file_data in files_data:
                self.files.append(extract_file_change(pr_number, file_data))
            self.stats.files_count = len(self.files)
        except Exception as e:
            errors.append(f"files: {e}")

        # Extract check runs
        head_sha = pr_data.get("head", {}).get("sha")
        if head_sha:
            try:
                checks_data = self.client.get_check_runs(head_sha)
                for check_data in checks_data:
                    self.checks.append(extract_check_run(pr_number, check_data))
                self.stats.checks_count = len(self.checks)
            except Exception as e:
                errors.append(f"checks: {e}")

        # Extract timeline events
        try:
            timeline_data = self.client.get_pr_timeline(pr_number)
            for event_data in timeline_data:
                event = extract_timeline_event(pr_number, event_data)
                if event:
                    self.timeline_events.append(event)
                    actor = event_data.get("actor") or event_data.get("user")
                    if actor:
                        self.track_user(actor)
            self.stats.timeline_events_count = len(self.timeline_events)
        except Exception as e:
            errors.append(f"timeline: {e}")

        # Add to pending - only moves to processed after save to disk
        self.pending_prs.append(pr_number)
        self.stats.processed_prs = len(self.processed_prs) + len(self.pending_prs) - self.stats.skipped_prs
        self.stats.last_pr = pr_number
        self.stats.api_requests = self.client.request_count

        # Log partial errors but still mark as processed
        if errors:
            self.stats.last_error = f"PR #{pr_number}: {'; '.join(errors)}"

        return True

    def save_parquet_incremental(self):
        """Save all data to Parquet files (incremental/append mode)."""
        os.makedirs(RAW_DATA_DIR, exist_ok=True)

        def to_records(items: list) -> list[dict]:
            return [item.model_dump() for item in items]

        def save_table(items: list, filename: str):
            if not items:
                return
            path = f"{RAW_DATA_DIR}/{filename}"
            df = pa.Table.from_pylist(to_records(items))

            # Append to existing or create new
            if os.path.exists(path):
                existing = pq.read_table(path)
                # Unify schemas by promoting to handle null vs timestamp mismatches
                try:
                    df = pa.concat_tables([existing, df], promote_options="default")
                except Exception as e:
                    logger.warning(f"Schema mismatch for {filename}, rewriting: {e}")
                    # If promotion fails, just use new schema (lose old data types)
                    df = pa.concat_tables([existing.cast(df.schema), df])

            pq.write_table(df, path)

        save_table(self.prs, "prs.parquet")
        save_table(self.reviews, "reviews.parquet")
        save_table(self.pr_comments, "pr_comments.parquet")
        save_table(self.review_comments, "review_comments.parquet")
        save_table(self.files, "files.parquet")
        save_table(self.checks, "checks.parquet")
        save_table(self.timeline_events, "timeline_events.parquet")

        if self.users:
            # Users table is a dimension - merge with existing
            users_path = f"{RAW_DATA_DIR}/users.parquet"
            new_users = pa.Table.from_pylist(to_records(list(self.users.values())))
            if os.path.exists(users_path):
                existing = pq.read_table(users_path)
                # Merge by user_id, preferring new data
                existing_ids = set(existing.column("user_id").to_pylist())
                new_ids = set(new_users.column("user_id").to_pylist())
                # Keep existing users not in new batch
                keep_mask = [uid not in new_ids for uid in existing.column("user_id").to_pylist()]
                if any(keep_mask):
                    existing_to_keep = existing.filter(pa.array(keep_mask))
                    new_users = pa.concat_tables([existing_to_keep, new_users])
            pq.write_table(new_users, users_path)

        # Mark pending PRs as processed (now safely on disk)
        self.processed_prs.update(self.pending_prs)
        self.pending_prs = []

        # Clear in-memory data after saving
        self.prs = []
        self.reviews = []
        self.pr_comments = []
        self.review_comments = []
        self.files = []
        self.checks = []
        self.timeline_events = []

    def build_dashboard(self) -> Table:
        """Build the live dashboard display."""
        # Main stats table
        table = Table(title="GitHub PR Extraction", expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        state_color = {
            ExtractionState.RUNNING: "green",
            ExtractionState.PAUSED: "yellow",
            ExtractionState.COMPLETED: "blue",
            ExtractionState.ERROR: "red",
        }
        state_str = f"[{state_color[self.stats.state]}]{self.stats.state.value}[/]"

        auth_color = "magenta" if self.stats.auth_type == "app" else "dim"
        auth_str = f"[{auth_color}]{self.stats.auth_type.upper()}[/]"

        table.add_row(
            "State", state_str,
            "Auth", auth_str,
        )
        table.add_row(
            "Total PRs", str(self.stats.total_prs),
            "Rate Limit", f"{self.stats.rate_limit_remaining} remaining",
        )
        table.add_row(
            "Processed", f"{self.stats.processed_prs} ({self.stats.skipped_prs} skipped)",
            "API Requests", str(self.stats.api_requests),
        )
        table.add_row(
            "Failed", f"[red]{self.stats.failed_prs}[/]" if self.stats.failed_prs else "0",
            "Last PR", f"#{self.stats.last_pr}" if self.stats.last_pr else "-",
        )
        table.add_row(
            "Reviews", str(self.stats.reviews_count),
            "PR Comments", str(self.stats.pr_comments_count),
        )
        table.add_row(
            "Review Comments", str(self.stats.review_comments_count),
            "Files", str(self.stats.files_count),
        )
        table.add_row(
            "Checks", str(self.stats.checks_count),
            "Timeline Events", str(self.stats.timeline_events_count),
        )
        table.add_row(
            "Users", str(self.stats.users_count),
            "", "",
        )

        if self.stats.last_error:
            table.add_row(
                "[red]Last Error[/]",
                f"[red]{self.stats.last_error[:80]}...[/]" if len(self.stats.last_error) > 80 else f"[red]{self.stats.last_error}[/]",
                "", ""
            )

        return table

    def run(self, limit: int | None = None, retry_failed: bool = False):
        """Run the full extraction with live TUI."""
        self.stats.auth_type = self.client.auth_type
        logger.info("=" * 60)
        logger.info(f"Starting extraction: {START_DATE.date()} to {END_DATE.date()}")
        logger.info(f"Auth type: {self.stats.auth_type}, Limit: {limit}, Retry failed: {retry_failed}")

        self.console.print(f"[bold]Extracting PRs from {START_DATE.date()} to {END_DATE.date()}[/]")
        self.console.print(f"[dim]Auth: {self.stats.auth_type.upper()}[/]")

        # Load checkpoint
        if self.load_checkpoint():
            logger.info(f"Resumed from checkpoint: {len(self.processed_prs)} PRs already processed")
            self.console.print(f"[green]Resumed from checkpoint: {len(self.processed_prs)} PRs already processed[/]")

        # Update rate limit info
        try:
            rate_info = self.client.get_rate_limit()["rate"]
            self.stats.rate_limit_remaining = rate_info["remaining"]
        except:
            pass

        # Get all PRs in date range
        self.console.print("\n[bold]Fetching PR list...[/]")
        pr_numbers = []

        for pr in self.client.get_pull_requests(state="all", since=START_DATE):
            pr_created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            if pr_created >= START_DATE:
                pr_numbers.append(pr["number"])
            if limit and len(pr_numbers) >= limit:
                break

        # Optionally include failed PRs for retry
        if retry_failed:
            failed_to_retry = [n for n in self.failed_prs.keys() if n not in self.processed_prs]
            pr_numbers = list(set(pr_numbers + failed_to_retry))

        # Filter out already processed
        pr_numbers_to_process = [n for n in pr_numbers if n not in self.processed_prs]
        self.stats.total_prs = len(pr_numbers)

        self.console.print(f"[green]Found {len(pr_numbers_to_process)} PRs to process ({self.stats.skipped_prs} already done)[/]\n")

        if not pr_numbers_to_process:
            self.console.print("[yellow]No new PRs to process.[/]")
            return

        self.console.print("[dim]Press Ctrl+C to stop (progress is saved)[/]\n")

        # Process with live dashboard
        with Live(self.build_dashboard(), console=self.console, refresh_per_second=2) as live:
            for i, pr_number in enumerate(pr_numbers_to_process):
                # Check for stop
                if self._stop_requested:
                    break

                # Extract PR
                try:
                    self.extract_pr_details(pr_number)
                    logger.debug(f"PR #{pr_number} extracted successfully")
                except Exception as e:
                    self.stats.failed_prs += 1
                    error_record = ErrorRecord(
                        pr_number=pr_number,
                        error_type=type(e).__name__,
                        error_message=str(e)[:500],
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        retries=self.failed_prs.get(pr_number, ErrorRecord(0, "", "", "")).retries + 1,
                    )
                    self.failed_prs[pr_number] = error_record
                    self.stats.last_error = f"PR #{pr_number}: {type(e).__name__}: {str(e)[:100]}"
                    logger.error(f"PR #{pr_number} failed: {type(e).__name__}: {e}")
                    logger.error(traceback.format_exc())

                # Update rate limit periodically
                if (i + 1) % 50 == 0:
                    try:
                        rate_info = self.client.get_rate_limit()["rate"]
                        self.stats.rate_limit_remaining = rate_info["remaining"]
                    except:
                        pass

                # Incremental save and checkpoint (parquet first, then checkpoint)
                if (i + 1) % CHECKPOINT_INTERVAL == 0:
                    self.save_parquet_incremental()
                    self.save_checkpoint()

                live.update(self.build_dashboard())

        # Final save (parquet first, then checkpoint)
        was_interrupted = self._stop_requested
        self.stats.state = ExtractionState.PAUSED if was_interrupted else ExtractionState.COMPLETED
        self.save_parquet_incremental()
        self.save_checkpoint()
        self.save_error_log()

        status = "interrupted" if was_interrupted else "complete"
        logger.info(f"Extraction {status}: {self.stats.processed_prs} processed, {self.stats.failed_prs} failed, {self.stats.api_requests} API requests")

        if was_interrupted:
            self.console.print(f"\n[bold yellow]Extraction stopped - progress saved[/]")
        else:
            self.console.print(f"\n[bold green]Extraction complete![/]")

        self.console.print(f"  Processed: {self.stats.processed_prs} PRs")
        self.console.print(f"  Failed: {self.stats.failed_prs} PRs")
        self.console.print(f"  API Requests: {self.stats.api_requests}")
        self.console.print(f"  Log file: {LOG_FILE}")

        if was_interrupted:
            self.console.print(f"\n[dim]Run again to resume from checkpoint[/]")

        if self.failed_prs:
            self.console.print(f"\n[yellow]Failed PRs logged to {RAW_DATA_DIR}/extraction_errors.json[/]")
            self.console.print("[dim]Run with --retry-failed to retry them[/]")


def main(limit: int | None = None, retry_failed: bool = False):
    """Main entry point."""
    console = Console()

    with GitHubClient() as client:
        extractor = DataExtractor(client, console)
        extractor.run(limit=limit, retry_failed=retry_failed)


def cli():
    """CLI entry point."""
    import argparse
    import sys

    if not REPO_OWNER or not REPO_NAME:
        print("Error: REPO_OWNER and REPO_NAME must be set in .env")
        print("Example:")
        print("  REPO_OWNER=your-org")
        print("  REPO_NAME=your-repo")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Extract GitHub PR data for code review analysis")
    parser.add_argument("--limit", "-n", type=int, help="Limit number of PRs to process")
    parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed PRs")

    args = parser.parse_args()
    main(limit=args.limit, retry_failed=args.retry_failed)


if __name__ == "__main__":
    cli()
