"""Main orchestrator for GitHub data extraction with TUI.

Uses trio for concurrent API requests per PR.
"""

import json
import logging
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

import pyarrow as pa
import pyarrow.parquet as pq
import trio
from rich.console import Console
from rich.live import Live
from rich.table import Table

from .config import (
    CHECKPOINT_FILE,
    DATA_DIR,
    END_DATE,
    LOG_FILE,
    RAW_DATA_DIR,
    REPO_NAME,
    REPO_OWNER,
    START_DATE,
)
from .extractors.checks import extract_check_run
from .extractors.comments import extract_pr_comment, extract_review_comment
from .extractors.files import extract_file_change
from .extractors.prs import extract_pr, set_config as set_extractor_config
from .extractors.reviews import extract_review
from .extractors.timeline import extract_timeline_event
from .extractors.users import extract_user
from .github_client import GitHubClient
from .module_config import ModuleConfig
from .models import (
    CheckRun,
    FileChange,
    PRComment,
    PullRequest,
    Review,
    ReviewComment,
    TimelineEvent,
    User,
)

# Concurrency settings
CONCURRENT_PRS = 4  # Process this many PRs concurrently
PR_QUEUE_SIZE = 50  # Buffer size for PR queue
RATE_LIMIT_PRODUCER_PAUSE = 100  # Producer pauses if rate limit drops below this


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


@dataclass
class PRDetails:
    """Results of extracting a single PR's details."""
    pr: PullRequest
    reviews: list[Review] = field(default_factory=list)
    pr_comments: list[PRComment] = field(default_factory=list)
    review_comments: list[ReviewComment] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    checks: list[CheckRun] = field(default_factory=list)
    timeline_events: list[TimelineEvent] = field(default_factory=list)
    users: dict[int, User] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


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

    async def _signal_watcher(self, nursery: trio.Nursery) -> None:
        """Watch for interrupt signals and cancel the nursery gracefully."""
        with trio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signal_aiter:
            async for sig in signal_aiter:
                self._stop_requested = True
                self.stats.state = ExtractionState.PAUSED
                logger.info(f"Signal {sig} received, stopping gracefully...")
                nursery.cancel_scope.cancel()
                break

    def load_checkpoint(self, refresh_days: int | None = None) -> bool:
        """Load checkpoint if exists.

        Args:
            refresh_days: If set, PRs created within the last N days will be
                         removed from processed_prs so they get re-extracted.
        """
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

            # If refresh_days is set, remove recent PRs from processed set
            if refresh_days and os.path.exists(f"{RAW_DATA_DIR}/prs.parquet"):
                cutoff = datetime.now(UTC) - timedelta(days=refresh_days)
                existing_prs = pq.read_table(f"{RAW_DATA_DIR}/prs.parquet")

                # Find PRs created after cutoff
                recent_prs = set()
                for i in range(len(existing_prs)):
                    pr_num = existing_prs.column("pr_number")[i].as_py()
                    created_at = existing_prs.column("created_at")[i].as_py()
                    if created_at and created_at >= cutoff:
                        recent_prs.add(pr_num)

                if recent_prs:
                    self.processed_prs -= recent_prs
                    self.console.print(f"[cyan]Refreshing {len(recent_prs)} PRs from last {refresh_days} days[/]")

            # Note: skipped_prs is counted dynamically in _pr_producer()
            return True
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to load checkpoint: {e}[/]")
            return False

    def save_checkpoint(self):
        """Save current progress to checkpoint file atomically.

        Writes to a temp file first, then renames to ensure atomicity.
        This prevents corruption if the process is killed mid-write.
        """
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
            "timestamp": datetime.now(UTC).isoformat(),
            "stats": {
                "total_processed": len(self.processed_prs),
                "total_failed": len(self.failed_prs),
            },
        }

        # Write to temp file first, then atomic rename
        temp_file = f"{CHECKPOINT_FILE}.tmp"
        with open(temp_file, "w") as f:
            json.dump(checkpoint, f, indent=2)
        os.replace(temp_file, CHECKPOINT_FILE)  # Atomic on POSIX

    def save_error_log(self):
        """Save detailed error log."""
        if not self.failed_prs:
            return

        error_log_path = f"{RAW_DATA_DIR}/extraction_errors.json"
        os.makedirs(RAW_DATA_DIR, exist_ok=True)

        with open(error_log_path, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
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

    def _track_user(self, user_data: dict, users: dict[int, User]):
        """Track unique user."""
        if not user_data:
            return
        user_id = user_data.get("id")
        if user_id and user_id not in users:
            users[user_id] = extract_user(user_data)

    async def extract_pr_details(self, pr_number: int, pr_data: dict) -> PRDetails:
        """Extract all details for a single PR using concurrent requests."""
        pr = extract_pr(pr_data)
        details = PRDetails(pr=pr)

        self._track_user(pr_data.get("user", {}), details.users)

        head_sha = pr_data.get("head", {}).get("sha")

        # Use trio nursery to fetch all PR details concurrently
        async def fetch_reviews():
            try:
                reviews_data = await self.client.get_pr_reviews(pr_number)
                for review_data in reviews_data:
                    details.reviews.append(extract_review(pr_number, review_data))
                    self._track_user(review_data.get("user", {}), details.users)
            except Exception as e:
                details.errors.append(f"reviews: {e}")

        async def fetch_comments():
            try:
                comments_data = await self.client.get_pr_comments(pr_number)
                for comment_data in comments_data:
                    details.pr_comments.append(extract_pr_comment(pr_number, comment_data))
                    self._track_user(comment_data.get("user", {}), details.users)
            except Exception as e:
                details.errors.append(f"pr_comments: {e}")

        async def fetch_review_comments():
            try:
                review_comments_data = await self.client.get_pr_review_comments(pr_number)
                for comment_data in review_comments_data:
                    details.review_comments.append(extract_review_comment(pr_number, comment_data))
                    self._track_user(comment_data.get("user", {}), details.users)
            except Exception as e:
                details.errors.append(f"review_comments: {e}")

        async def fetch_files():
            try:
                files_data = await self.client.get_pr_files(pr_number)
                for file_data in files_data:
                    details.files.append(extract_file_change(pr_number, file_data))
            except Exception as e:
                details.errors.append(f"files: {e}")

        async def fetch_checks():
            if not head_sha:
                return
            try:
                checks_data = await self.client.get_check_runs(head_sha)
                for check_data in checks_data:
                    details.checks.append(extract_check_run(pr_number, check_data))
            except Exception as e:
                details.errors.append(f"checks: {e}")

        async def fetch_timeline():
            try:
                timeline_data = await self.client.get_pr_timeline(pr_number)
                for event_data in timeline_data:
                    event = extract_timeline_event(pr_number, event_data)
                    if event:
                        details.timeline_events.append(event)
                        actor = event_data.get("actor") or event_data.get("user")
                        if actor:
                            self._track_user(actor, details.users)
            except Exception as e:
                details.errors.append(f"timeline: {e}")

        # Run all fetches concurrently
        async with trio.open_nursery() as nursery:
            nursery.start_soon(fetch_reviews)
            nursery.start_soon(fetch_comments)
            nursery.start_soon(fetch_review_comments)
            nursery.start_soon(fetch_files)
            nursery.start_soon(fetch_checks)
            nursery.start_soon(fetch_timeline)

        # Fix PR stats from actual file data (list endpoint doesn't include these)
        if details.files:
            details.pr.changed_files = len(details.files)
            details.pr.additions = sum(f.additions for f in details.files)
            details.pr.deletions = sum(f.deletions for f in details.files)

        return details

    def merge_details(self, details: PRDetails, pr_number: int):
        """Merge PR details into main storage."""
        self.prs.append(details.pr)
        self.reviews.extend(details.reviews)
        self.pr_comments.extend(details.pr_comments)
        self.review_comments.extend(details.review_comments)
        self.files.extend(details.files)
        self.checks.extend(details.checks)
        self.timeline_events.extend(details.timeline_events)

        # Merge users
        for user_id, user in details.users.items():
            if user_id not in self.users:
                self.users[user_id] = user

        # Update stats (increment by new counts, not list length - lists get cleared on flush)
        self.stats.reviews_count += len(details.reviews)
        self.stats.pr_comments_count += len(details.pr_comments)
        self.stats.review_comments_count += len(details.review_comments)
        self.stats.files_count += len(details.files)
        self.stats.checks_count += len(details.checks)
        self.stats.timeline_events_count += len(details.timeline_events)
        self.stats.users_count = len(self.users)  # users dict isn't cleared, so this is still correct

        # Add to pending
        self.pending_prs.append(pr_number)
        self.stats.processed_prs = len(self.processed_prs) + len(self.pending_prs) - self.stats.skipped_prs
        self.stats.last_pr = pr_number
        self.stats.api_requests = self.client.request_count

        # Log partial errors
        if details.errors:
            self.stats.last_error = f"PR #{pr_number}: {'; '.join(details.errors)}"

    def save_parquet_incremental(self):
        """Save all data to Parquet files with upsert semantics.

        When re-extracting PRs (e.g., with --refresh-days), existing data for
        those PRs is replaced with the new data.
        """
        os.makedirs(RAW_DATA_DIR, exist_ok=True)

        # Get the set of PR numbers we're updating
        updated_pr_numbers = {pr.pr_number for pr in self.prs}

        def to_records(items: list) -> list[dict]:
            return [item.model_dump() for item in items]

        def upsert_table(items: list, filename: str, key_column: str = "pr_number"):
            """Upsert items into a parquet table.

            For pr_number keys: removes all existing rows for PRs being updated.
            For unique keys (review_id, etc.): removes rows with matching keys.
            """
            if not items:
                return

            path = f"{RAW_DATA_DIR}/{filename}"
            new_table = pa.Table.from_pylist(to_records(items))

            if os.path.exists(path):
                existing = pq.read_table(path)

                if key_column == "pr_number":
                    # Remove all rows for PRs we're updating
                    keep_mask = [
                        pn not in updated_pr_numbers
                        for pn in existing.column("pr_number").to_pylist()
                    ]
                else:
                    # Remove rows with matching unique keys
                    new_keys = set(new_table.column(key_column).to_pylist())
                    keep_mask = [
                        k not in new_keys
                        for k in existing.column(key_column).to_pylist()
                    ]

                if any(keep_mask):
                    existing_to_keep = existing.filter(pa.array(keep_mask))
                    try:
                        new_table = pa.concat_tables(
                            [existing_to_keep, new_table], promote_options="default"
                        )
                    except Exception as e:
                        logger.warning(f"Schema mismatch for {filename}: {e}")
                        new_table = pa.concat_tables([existing_to_keep.cast(new_table.schema), new_table])

            pq.write_table(new_table, path)

        # Tables keyed by pr_number (replace all data for updated PRs)
        upsert_table(self.prs, "prs.parquet", "pr_number")
        upsert_table(self.reviews, "reviews.parquet", "pr_number")
        upsert_table(self.pr_comments, "pr_comments.parquet", "pr_number")
        upsert_table(self.review_comments, "review_comments.parquet", "pr_number")
        upsert_table(self.files, "files.parquet", "pr_number")
        upsert_table(self.checks, "checks.parquet", "pr_number")
        upsert_table(self.timeline_events, "timeline_events.parquet", "pr_number")

        # Users table - keyed by user_id
        if self.users:
            upsert_table(list(self.users.values()), "users.parquet", "user_id")

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
        # Show checkpoint count + new total
        checkpoint_count = len(self.processed_prs) - self.stats.processed_prs
        table.add_row(
            "Total PRs", str(self.stats.total_prs),
            "Rate Limit", f"{self.stats.rate_limit_remaining} remaining",
        )
        table.add_row(
            "Processed", f"{self.stats.processed_prs} new (+{checkpoint_count} from checkpoint)",
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

    async def _process_single_pr(self, pr_data: dict) -> None:
        """Process a single PR (called by worker tasks)."""
        if self._stop_requested:
            return

        pr_number = pr_data["number"]

        try:
            details = await self.extract_pr_details(pr_number, pr_data)
            self.merge_details(details, pr_number)
            logger.debug(f"PR #{pr_number} extracted successfully")
        except Exception as e:
            self.stats.failed_prs += 1
            error_record = ErrorRecord(
                pr_number=pr_number,
                error_type=type(e).__name__,
                error_message=str(e)[:500],
                timestamp=datetime.now(UTC).isoformat(),
                retries=self.failed_prs.get(pr_number, ErrorRecord(0, "", "", "")).retries + 1,
            )
            self.failed_prs[pr_number] = error_record
            self.stats.last_error = f"PR #{pr_number}: {type(e).__name__}: {str(e)[:100]}"
            logger.error(f"PR #{pr_number} failed: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())

    async def _wait_for_rate_limit(self) -> None:
        """Wait for rate limit to recover if it's critically low.

        Called by producer to prioritize in-flight worker requests over fetching new PRs.
        Workers continue processing while producer waits.
        """
        while self.client.rate_limit_remaining < RATE_LIMIT_PRODUCER_PAUSE:
            if self._stop_requested:
                return
            reset_time = self.client.rate_limit_reset
            wait_seconds = max(reset_time - time.time(), 10)
            logger.info(
                f"Rate limit low ({self.client.rate_limit_remaining}), "
                f"producer pausing {wait_seconds:.0f}s to let workers finish"
            )
            await trio.sleep(min(wait_seconds, 30))  # Check every 30s max

    async def _pr_producer(
        self,
        send_channel: trio.MemorySendChannel,
        limit: int | None,
    ) -> None:
        """Fetch PRs and queue them for processing.

        Prioritizes workers over fetching new pages when rate limit is low.
        """
        async with send_channel:
            queued = 0

            # Stream PRs from API and queue for processing immediately
            async for pr in self.client.get_pull_requests(state="all", since=START_DATE):
                if self._stop_requested:
                    break

                # Pause if rate limit is low - let workers drain the queue first
                await self._wait_for_rate_limit()

                pr_created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                if pr_created < START_DATE:
                    continue

                self.stats.total_prs += 1

                if pr["number"] in self.processed_prs:
                    self.stats.skipped_prs += 1
                    continue

                await send_channel.send(pr)
                queued += 1

                if limit and queued >= limit:
                    break

            # Also queue failed PRs for retry
            for pr_num in list(self.failed_prs.keys()):
                if self._stop_requested:
                    break
                if pr_num not in self.processed_prs:
                    try:
                        pr_data = await self.client.get(
                            f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_num}"
                        )
                        await send_channel.send(pr_data)
                    except Exception as e:
                        logger.warning(f"Could not fetch failed PR #{pr_num}: {e}")

    async def _pr_worker(self, receive_channel: trio.MemoryReceiveChannel) -> None:
        """Worker that processes PRs from the queue.

        Concurrency is controlled by the number of workers (CONCURRENT_PRS),
        not by a limiter - each worker processes one PR at a time.
        """
        async with receive_channel:
            async for pr_data in receive_channel:
                if self._stop_requested:
                    break
                await self._process_single_pr(pr_data)

    async def _checkpoint_task(self, interval: float = 30.0) -> None:
        """Periodically save checkpoints and update rate limit."""
        last_processed = 0
        while not self._stop_requested:
            await trio.sleep(interval)

            # Only checkpoint if we've made progress
            current_processed = self.stats.processed_prs
            if current_processed > last_processed:
                self.save_parquet_incremental()
                self.save_checkpoint()
                last_processed = current_processed

            # Update rate limit info
            try:
                rate_info = (await self.client.get_rate_limit())["rate"]
                self.stats.rate_limit_remaining = rate_info["remaining"]
            except Exception:
                pass

    async def _dashboard_task(self, live: Live) -> None:
        """Update dashboard periodically."""
        while not self._stop_requested:
            await trio.sleep(0.5)
            live.update(self.build_dashboard())

    async def run(self, limit: int | None = None, refresh_days: int | None = None):
        """Run extraction with concurrent PR processing.

        Uses producer/consumer pattern:
        - Producer: Fetches PR pages and queues them (no waiting for all pages)
        - Workers: Process PRs concurrently (CONCURRENT_PRS at a time)

        Args:
            limit: Max number of PRs to process
            refresh_days: Re-extract PRs from the last N days even if already processed
        """
        self.stats.auth_type = self.client.auth_type
        logger.info("=" * 60)
        logger.info(f"Starting extraction: {START_DATE.date()} to {END_DATE.date()}")
        logger.info(f"Auth type: {self.stats.auth_type}, Limit: {limit}, Refresh days: {refresh_days}")

        self.console.print(f"[bold]Extracting PRs from {START_DATE.date()} to {END_DATE.date()}[/]")
        self.console.print(f"[dim]Auth: {self.stats.auth_type.upper()} | Concurrency: {CONCURRENT_PRS} PRs[/]")

        # Load checkpoint
        if self.load_checkpoint(refresh_days=refresh_days):
            logger.info(f"Resumed from checkpoint: {len(self.processed_prs)} PRs already processed")
            self.console.print(
                f"[green]Resumed from checkpoint: {len(self.processed_prs)} PRs already processed[/]"
            )

        # Update rate limit info
        try:
            rate_info = (await self.client.get_rate_limit())["rate"]
            self.stats.rate_limit_remaining = rate_info["remaining"]
        except Exception:
            pass

        self.console.print("\n[dim]Press Ctrl+C to stop (progress is saved)[/]\n")

        # Create producer/consumer pipeline
        send_channel, receive_channel = trio.open_memory_channel[dict](PR_QUEUE_SIZE)

        with Live(self.build_dashboard(), console=self.console, refresh_per_second=2) as live:
            try:
                async with trio.open_nursery() as nursery:
                    # Start signal watcher (handles Ctrl+C properly)
                    nursery.start_soon(self._signal_watcher, nursery)

                    # Start producer (fetches PR pages, queues PRs immediately)
                    nursery.start_soon(self._pr_producer, send_channel, limit)

                    # Start workers (CONCURRENT_PRS workers = concurrent PR processing)
                    for _ in range(CONCURRENT_PRS):
                        nursery.start_soon(self._pr_worker, receive_channel.clone())

                    # Start checkpoint task (saves every 30s)
                    nursery.start_soon(self._checkpoint_task)

                    # Start dashboard updater
                    nursery.start_soon(self._dashboard_task, live)

                    # Close our copy of receive channel (workers have clones)
                    await receive_channel.aclose()

            except trio.Cancelled:
                pass  # Normal shutdown from signal

        # Final save
        was_interrupted = self._stop_requested
        self.stats.state = ExtractionState.PAUSED if was_interrupted else ExtractionState.COMPLETED
        self.save_parquet_incremental()
        self.save_checkpoint()
        self.save_error_log()

        status = "interrupted" if was_interrupted else "complete"
        logger.info(
            f"Extraction {status}: {self.stats.processed_prs} processed, "
            f"{self.stats.failed_prs} failed, {self.stats.api_requests} API requests"
        )

        if was_interrupted:
            self.console.print("\n[bold yellow]Extraction stopped - progress saved[/]")
        else:
            self.console.print("\n[bold green]Extraction complete![/]")

        self.console.print(f"  Processed: {self.stats.processed_prs} PRs")
        self.console.print(f"  Failed: {self.stats.failed_prs} PRs")
        self.console.print(f"  API Requests: {self.stats.api_requests}")
        self.console.print(f"  Log file: {LOG_FILE}")

        if was_interrupted:
            self.console.print("\n[dim]Run again to resume from checkpoint[/]")

        if self.failed_prs:
            self.console.print(f"\n[yellow]Failed PRs logged to {RAW_DATA_DIR}/extraction_errors.json[/]")
            self.console.print("[dim]Failed PRs will be retried automatically on next run[/]")


async def main(limit: int | None = None, refresh_days: int | None = None):
    """Main entry point."""
    console = Console()

    # Load module config and set it for extractors (for bot detection)
    config = ModuleConfig.load()
    set_extractor_config(config)

    async with GitHubClient() as client:
        extractor = DataExtractor(client, console)
        await extractor.run(limit=limit, refresh_days=refresh_days)


def cli():
    """CLI entry point."""
    import argparse

    if not REPO_OWNER or not REPO_NAME:
        print("Error: REPO_OWNER and REPO_NAME must be set in .env")
        print("Example:")
        print("  REPO_OWNER=your-org")
        print("  REPO_NAME=your-repo")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Extract GitHub PR data for code review analysis")
    parser.add_argument("--limit", "-n", type=int, help="Limit number of PRs to process")
    parser.add_argument(
        "--refresh-days", "-r", type=int,
        help="Re-extract PRs from the last N days (updates existing data)"
    )

    args = parser.parse_args()
    trio.run(main, args.limit, args.refresh_days)


if __name__ == "__main__":
    cli()
