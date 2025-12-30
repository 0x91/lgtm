"""VADER sentiment analysis for code review comments.

VADER (Valence Aware Dictionary and sEntiment Reasoner) is designed for
social media text and handles things like emoticons, slang, and capitalization.

For code review, it gives us a baseline polarity score, but we need to
layer additional analysis on top (constructiveness, teaching, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer


@lru_cache(maxsize=1)
def _get_analyzer() -> SentimentIntensityAnalyzer:
    """Get or initialize the VADER analyzer.

    Downloads the vader_lexicon on first use if not present.
    """
    try:
        nltk.data.find("sentiment/vader_lexicon.zip")
    except LookupError:
        nltk.download("vader_lexicon", quiet=True)

    return SentimentIntensityAnalyzer()


@dataclass
class SentimentScores:
    """VADER sentiment scores for a piece of text."""

    positive: float  # 0.0 to 1.0
    negative: float  # 0.0 to 1.0
    neutral: float  # 0.0 to 1.0
    compound: float  # -1.0 to 1.0 (overall sentiment)

    @property
    def label(self) -> str:
        """Get a simple label based on compound score."""
        if self.compound >= 0.05:
            return "positive"
        elif self.compound <= -0.05:
            return "negative"
        else:
            return "neutral"

    @property
    def is_positive(self) -> bool:
        return self.compound >= 0.05

    @property
    def is_negative(self) -> bool:
        return self.compound <= -0.05

    @property
    def is_neutral(self) -> bool:
        return -0.05 < self.compound < 0.05


def get_sentiment_scores(text: str) -> SentimentScores:
    """Analyze sentiment of text using VADER.

    Args:
        text: The text to analyze.

    Returns:
        SentimentScores with positive, negative, neutral, and compound scores.
    """
    if not text or not text.strip():
        return SentimentScores(
            positive=0.0,
            negative=0.0,
            neutral=1.0,
            compound=0.0,
        )

    analyzer = _get_analyzer()
    scores = analyzer.polarity_scores(text)

    return SentimentScores(
        positive=scores["pos"],
        negative=scores["neg"],
        neutral=scores["neu"],
        compound=scores["compound"],
    )


def analyze_batch(texts: list[str]) -> list[SentimentScores]:
    """Analyze sentiment of multiple texts.

    More efficient than calling get_sentiment_scores repeatedly
    as the analyzer is only initialized once.
    """
    analyzer = _get_analyzer()
    results = []

    for text in texts:
        if not text or not text.strip():
            results.append(
                SentimentScores(
                    positive=0.0,
                    negative=0.0,
                    neutral=1.0,
                    compound=0.0,
                )
            )
        else:
            scores = analyzer.polarity_scores(text)
            results.append(
                SentimentScores(
                    positive=scores["pos"],
                    negative=scores["neg"],
                    neutral=scores["neu"],
                    compound=scores["compound"],
                )
            )

    return results
