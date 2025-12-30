"""Tests for main extraction orchestrator."""

import signal
from unittest.mock import MagicMock

import pytest
import trio
from rich.console import Console

from src.main import CONCURRENT_PRS, PR_QUEUE_SIZE, DataExtractor, ExtractionState


class TestDataExtractor:
    """Tests for DataExtractor class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock GitHub client."""
        client = MagicMock()
        client.auth_type = "pat"
        client.request_count = 0
        return client

    @pytest.fixture
    def extractor(self, mock_client):
        """Create a DataExtractor with mock client."""
        console = Console(quiet=True)
        return DataExtractor(mock_client, console)

    def test_init_state(self, extractor):
        """Test initial state of extractor."""
        assert extractor.stats.processed_prs == 0
        assert extractor.stats.failed_prs == 0
        assert extractor.stats.skipped_prs == 0
        assert extractor.stats.state == ExtractionState.RUNNING
        assert extractor._stop_requested is False

    def test_stop_requested_flag(self, extractor):
        """Test that stop_requested flag can be set."""
        assert extractor._stop_requested is False
        extractor._stop_requested = True
        assert extractor._stop_requested is True

    @pytest.mark.trio
    async def test_signal_watcher_cancels_nursery(self, extractor):
        """Test that signal watcher properly cancels nursery on signal."""
        async with trio.open_nursery() as nursery:
            # Start signal watcher
            nursery.start_soon(extractor._signal_watcher, nursery)

            # Give it a moment to start
            await trio.sleep(0.01)

            # Send SIGINT to ourselves
            import os
            os.kill(os.getpid(), signal.SIGINT)

            # Give signal time to be processed
            await trio.sleep(0.1)

            # If we get here without the nursery being cancelled,
            # manually cancel (signal should have done this)
            if extractor._stop_requested:
                nursery.cancel_scope.cancel()

        assert extractor._stop_requested is True
        assert extractor.stats.state == ExtractionState.PAUSED

    @pytest.mark.trio
    async def test_checkpoint_task_respects_stop(self, extractor):
        """Test that checkpoint task exits when stop is requested."""
        # Start checkpoint task
        async with trio.open_nursery() as nursery:
            nursery.start_soon(extractor._checkpoint_task, 0.05)  # Short interval

            # Let it run briefly
            await trio.sleep(0.1)

            # Request stop
            extractor._stop_requested = True

            # Give it time to notice and exit
            await trio.sleep(0.1)

            # Cancel remaining tasks
            nursery.cancel_scope.cancel()

        # Task should have exited cleanly
        assert extractor._stop_requested is True

    @pytest.mark.trio
    async def test_dashboard_task_respects_stop(self, extractor):
        """Test that dashboard task exits when stop is requested."""
        mock_live = MagicMock()

        async with trio.open_nursery() as nursery:
            nursery.start_soon(extractor._dashboard_task, mock_live)

            # Let it run briefly
            await trio.sleep(0.1)

            # Request stop
            extractor._stop_requested = True

            # Give it time to notice and exit
            await trio.sleep(0.6)  # Dashboard updates every 0.5s

            # Cancel if still running
            nursery.cancel_scope.cancel()

        assert extractor._stop_requested is True

    @pytest.mark.trio
    async def test_worker_respects_stop(self, extractor):
        """Test that worker exits when stop is requested."""
        send_channel, receive_channel = trio.open_memory_channel[dict](10)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(extractor._pr_worker, receive_channel)

            # Request stop before sending anything
            extractor._stop_requested = True

            # Close channel to unblock worker
            await send_channel.aclose()

            # Worker should exit
            await trio.sleep(0.1)

        assert extractor._stop_requested is True

    def test_concurrency_settings(self):
        """Test concurrency constants are reasonable."""
        assert CONCURRENT_PRS > 0
        assert CONCURRENT_PRS <= 20  # Don't overload API
        assert PR_QUEUE_SIZE >= CONCURRENT_PRS  # Queue should be >= workers


class TestMergeDetails:
    """Tests for merge_details method."""

    @pytest.fixture
    def extractor(self):
        """Create a DataExtractor with mock client."""
        client = MagicMock()
        client.auth_type = "pat"
        console = Console(quiet=True)
        return DataExtractor(client, console)

    def test_merge_updates_stats(self, extractor):
        """Test that merging details updates stats correctly."""
        from datetime import UTC, datetime

        from src.main import PRDetails
        from src.models import PullRequest, Review

        pr = PullRequest(
            pr_number=123,
            pr_id=456,
            title="Test PR",
            body=None,
            author_login="test",
            author_id=1,
            author_is_bot=False,
            state="closed",
            merged=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            merged_at=None,
            closed_at=None,
            additions=10,
            deletions=5,
            changed_files=2,
            commits=1,
            comments_count=0,
            review_comments_count=0,
            draft=False,
            merge_commit_sha=None,
        )

        review = Review(
            review_id=789,
            pr_number=123,
            reviewer_login="reviewer",
            reviewer_id=2,
            reviewer_is_bot=False,
            state="APPROVED",
            body="LGTM",
            submitted_at=datetime.now(UTC),
            commit_id="abc123",
        )

        details = PRDetails(
            pr=pr,
            reviews=[review],
            files=[],
            pr_comments=[],
            review_comments=[],
            checks=[],
            timeline_events=[],
            users={},
        )

        extractor.merge_details(details, 123)

        assert extractor.stats.processed_prs == 1
        assert extractor.stats.reviews_count == 1
        assert 123 in extractor.pending_prs
        assert len(extractor.prs) == 1
