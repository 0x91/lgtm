"""Sentiment and outcome analysis for code review comments.

Philosophy: Usefulness is best measured by outcomes (did feedback
lead to changes?) not by parsing text. LLMs handle nuanced
classification better than regex.

This module provides:
- Simple text signals (rubber stamp, has code block, etc.)
- Thread-level structures (conversations, resolution status)
- Outcome-based metrics
- SentiCR-style sentiment (TF-IDF + GBT trained on code review data)
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
from .senticr import SENTICR_AVAILABLE, SentimentScores, get_sentiment_scores

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
    "SENTICR_AVAILABLE",
    # Thread-level
    "ReviewThread",
    "ThreadComment",
    "PRReviewOutcomes",
    # Aggregate
    "AggregateStats",
]
