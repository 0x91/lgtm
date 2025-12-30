"""Sentiment and outcome analysis for code review comments.

Philosophy: Usefulness is best measured by outcomes (did feedback
lead to changes?) not by parsing text. LLMs handle nuanced
classification better than regex.

This module provides:
- Simple text signals (rubber stamp, has code block, etc.)
- Thread-level structures (conversations, resolution status)
- Outcome-based metrics
- VADER for basic sentiment polarity
"""

from .analyzer import (
    AggregateStats,
    CommentAnalysis,
    PRReviewOutcomes,
    ReviewThread,
    ThreadComment,
    analyze_comment,
    analyze_comments,
)
from .categories import CommentSignals, get_signals
from .vader import SentimentScores, get_sentiment_scores

__all__ = [
    # Analysis
    "analyze_comment",
    "analyze_comments",
    "CommentAnalysis",
    # Signals
    "get_signals",
    "CommentSignals",
    # Sentiment
    "get_sentiment_scores",
    "SentimentScores",
    # Thread-level
    "ReviewThread",
    "ThreadComment",
    "PRReviewOutcomes",
    # Aggregate
    "AggregateStats",
]
