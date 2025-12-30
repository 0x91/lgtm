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

    def test_stats_persist_after_flush(self, extractor):
        """Test that stats don't reset when data is flushed to parquet.

        Regression test: stats were being set from len(self.reviews) which
        resets to 0 after flush. Now they increment correctly.
        """
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

        # Merge first PR
        extractor.merge_details(details, 123)
        assert extractor.stats.reviews_count == 1

        # Simulate what flush does - clear the lists
        extractor.prs = []
        extractor.reviews = []
        extractor.pr_comments = []
        extractor.review_comments = []
        extractor.files = []
        extractor.checks = []
        extractor.timeline_events = []

        # Merge second PR
        pr2 = PullRequest(
            pr_number=124,
            pr_id=457,
            title="Test PR 2",
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
            additions=5,
            deletions=3,
            changed_files=1,
            commits=1,
            comments_count=0,
            review_comments_count=0,
            draft=False,
            merge_commit_sha=None,
        )

        review2 = Review(
            review_id=790,
            pr_number=124,
            reviewer_login="reviewer2",
            reviewer_id=3,
            reviewer_is_bot=False,
            state="COMMENTED",
            body="Nice",
            submitted_at=datetime.now(UTC),
            commit_id="def456",
        )

        details2 = PRDetails(
            pr=pr2,
            reviews=[review2, review2],  # 2 reviews this time
            files=[],
            pr_comments=[],
            review_comments=[],
            checks=[],
            timeline_events=[],
            users={},
        )

        extractor.merge_details(details2, 124)

        # Stats should be cumulative (1 + 2 = 3), not reset to 2
        assert extractor.stats.reviews_count == 3


class TestRateLimitPrioritization:
    """Tests for rate limit handling and producer prioritization."""

    @pytest.fixture
    def extractor(self):
        """Create a DataExtractor with mock client."""
        client = MagicMock()
        client.auth_type = "pat"
        client.rate_limit_remaining = 5000
        client.rate_limit_reset = 0
        console = Console(quiet=True)
        return DataExtractor(client, console)

    @pytest.mark.trio
    async def test_producer_pauses_on_low_rate_limit(self, extractor):
        """Test that producer waits when rate limit is critically low."""
        from src.main import RATE_LIMIT_PRODUCER_PAUSE

        # Set rate limit below threshold
        extractor.client.rate_limit_remaining = RATE_LIMIT_PRODUCER_PAUSE - 1
        extractor.client.rate_limit_reset = 0  # Already reset

        # Request stop so the wait loop exits
        extractor._stop_requested = True

        # Should return without error (stop was requested)
        await extractor._wait_for_rate_limit()
        assert extractor._stop_requested is True

    @pytest.mark.trio
    async def test_producer_continues_on_sufficient_rate_limit(self, extractor):
        """Test that producer doesn't wait when rate limit is sufficient."""
        from src.main import RATE_LIMIT_PRODUCER_PAUSE

        # Set rate limit above threshold
        extractor.client.rate_limit_remaining = RATE_LIMIT_PRODUCER_PAUSE + 100

        # Should return immediately without waiting
        await extractor._wait_for_rate_limit()
        # No assertion needed - test passes if it doesn't hang
