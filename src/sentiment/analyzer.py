"""Comment analysis focused on outcomes, not text parsing.

Key insight from Bosu et al. 2015: usefulness is best measured by
whether comments led to code changes, not by parsing the text.

This module provides:
- Simple text signals (rubber stamp, has code, etc.)
- Thread-level analysis (conversations, resolution)
- Outcome metrics (did this lead to changes?)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .categories import CommentSignals, get_signals
from .vader import SentimentScores, get_sentiment_scores


@dataclass
class CommentAnalysis:
    """Analysis of a single comment."""

    text: str
    signals: CommentSignals
    sentiment: SentimentScores

    @property
    def is_rubber_stamp(self) -> bool:
        return self.signals.is_rubber_stamp

    @property
    def has_substance(self) -> bool:
        """Has code, link, or enough text to be substantive."""
        return self.signals.has_code_block or self.signals.has_link or self.signals.word_count >= 10


@dataclass
class ThreadComment:
    """A comment within a review thread."""

    comment_id: str
    author: str
    body: str
    created_at: datetime
    is_author_reply: bool  # True if PR author responding to feedback


@dataclass
class ReviewThread:
    """A conversation thread on a PR.

    Threads are the unit of analysis - a comment alone lacks context.
    """

    thread_id: str
    pr_number: int
    path: str | None  # File path if inline comment
    line: int | None
    comments: list[ThreadComment] = field(default_factory=list)
    is_resolved: bool = False
    is_outdated: bool = False  # Code changed since comment

    @property
    def initiator(self) -> str | None:
        """Who started the thread."""
        return self.comments[0].author if self.comments else None

    @property
    def comment_count(self) -> int:
        return len(self.comments)

    @property
    def has_back_and_forth(self) -> bool:
        """Did multiple people engage?"""
        if len(self.comments) < 2:
            return False
        authors = {c.author for c in self.comments}
        return len(authors) > 1

    @property
    def author_responded(self) -> bool:
        """Did PR author respond to feedback?"""
        return any(c.is_author_reply for c in self.comments)

    @property
    def resolution_signal(self) -> str:
        """What happened to this thread?

        Returns one of:
        - 'resolved': Explicitly marked resolved
        - 'outdated': Code changed (implicit resolution)
        - 'discussed': Back and forth but not resolved
        - 'ignored': No response from author
        - 'standalone': Single comment, no thread
        """
        if self.is_resolved:
            return "resolved"
        if self.is_outdated:
            return "outdated"
        if len(self.comments) == 1:
            return "standalone"
        if self.author_responded:
            return "discussed"
        return "ignored"


@dataclass
class PRReviewOutcomes:
    """Outcome-based metrics for a PR's review.

    Based on Bosu et al. 2015: usefulness correlates with
    whether feedback led to changes.
    """

    pr_number: int
    threads: list[ReviewThread] = field(default_factory=list)

    # Timing
    first_comment_at: datetime | None = None
    last_comment_at: datetime | None = None

    # Outcomes
    commits_after_review: int = 0  # Did author iterate?
    files_changed_after_review: int = 0

    @property
    def thread_count(self) -> int:
        return len(self.threads)

    @property
    def resolved_threads(self) -> int:
        return sum(1 for t in self.threads if t.is_resolved)

    @property
    def outdated_threads(self) -> int:
        return sum(1 for t in self.threads if t.is_outdated)

    @property
    def discussed_threads(self) -> int:
        return sum(1 for t in self.threads if t.has_back_and_forth)

    @property
    def ignored_threads(self) -> int:
        return sum(1 for t in self.threads if t.resolution_signal == "ignored")

    @property
    def resolution_rate(self) -> float:
        """Percentage of threads that got resolved or addressed."""
        if not self.threads:
            return 0.0
        resolved = sum(
            1 for t in self.threads if t.resolution_signal in ("resolved", "outdated", "discussed")
        )
        return 100.0 * resolved / len(self.threads)

    @property
    def led_to_changes(self) -> bool:
        """Did review feedback result in code changes?"""
        return self.commits_after_review > 0


def analyze_comment(text: str) -> CommentAnalysis:
    """Analyze a single comment.

    For quick filtering. Thread-level analysis is more meaningful.
    """
    return CommentAnalysis(
        text=text,
        signals=get_signals(text),
        sentiment=get_sentiment_scores(text),
    )


def analyze_comments(texts: list[str]) -> list[CommentAnalysis]:
    """Analyze multiple comments."""
    return [analyze_comment(t) for t in texts]


@dataclass
class AggregateStats:
    """Aggregate statistics across all PRs."""

    total_threads: int = 0
    total_comments: int = 0

    # Resolution outcomes
    resolved: int = 0
    outdated: int = 0
    discussed: int = 0
    ignored: int = 0
    standalone: int = 0

    # Comment signals
    rubber_stamps: int = 0
    with_code: int = 0
    with_links: int = 0
    questions: int = 0

    # Did review lead to iteration?
    prs_with_post_review_commits: int = 0
    total_prs: int = 0

    @property
    def resolution_rate(self) -> float:
        if not self.total_threads:
            return 0.0
        addressed = self.resolved + self.outdated + self.discussed
        return 100.0 * addressed / self.total_threads

    @property
    def rubber_stamp_rate(self) -> float:
        if not self.total_comments:
            return 0.0
        return 100.0 * self.rubber_stamps / self.total_comments

    @property
    def iteration_rate(self) -> float:
        """How often did PRs iterate after review?"""
        if not self.total_prs:
            return 0.0
        return 100.0 * self.prs_with_post_review_commits / self.total_prs
