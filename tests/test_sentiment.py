"""Tests for sentiment/outcome analysis module."""

from datetime import datetime

from src.sentiment import (
    ReviewThread,
    ThreadComment,
    analyze_comment,
    get_sentiment_scores,
    get_signals,
)


class TestVaderSentiment:
    """Test VADER sentiment scoring."""

    def test_positive_sentiment(self):
        scores = get_sentiment_scores("This is a great improvement!")
        assert scores.is_positive
        assert scores.compound > 0

    def test_negative_sentiment(self):
        scores = get_sentiment_scores("This is wrong and broken.")
        assert scores.is_negative
        assert scores.compound < 0

    def test_neutral_sentiment(self):
        scores = get_sentiment_scores("The variable is set to 5.")
        assert scores.is_neutral

    def test_empty_text(self):
        scores = get_sentiment_scores("")
        assert scores.neutral == 1.0
        assert scores.compound == 0.0


class TestCommentSignals:
    """Test simple comment signal detection."""

    def test_rubber_stamp_lgtm(self):
        signals = get_signals("lgtm")
        assert signals.is_rubber_stamp
        assert signals.is_short

    def test_rubber_stamp_plus_one(self):
        signals = get_signals("+1")
        assert signals.is_rubber_stamp

    def test_rubber_stamp_looks_good(self):
        signals = get_signals("looks good!")
        assert signals.is_rubber_stamp

    def test_not_rubber_stamp(self):
        signals = get_signals("Consider using a list comprehension instead.")
        assert not signals.is_rubber_stamp

    def test_has_code_block(self):
        text = "Try this:\n```python\nprint('hello')\n```"
        signals = get_signals(text)
        assert signals.has_code_block

    def test_has_link(self):
        signals = get_signals("See https://docs.python.org for more info.")
        assert signals.has_link

    def test_is_question(self):
        signals = get_signals("Why did you choose this approach?")
        assert signals.is_question

    def test_not_question(self):
        signals = get_signals("Consider using a set instead.")
        assert not signals.is_question

    def test_word_count(self):
        signals = get_signals("This is a five word comment.")
        assert signals.word_count == 6

    def test_empty_text(self):
        signals = get_signals("")
        assert signals.is_rubber_stamp is False
        assert signals.word_count == 0


class TestCommentAnalysis:
    """Test comment analysis."""

    def test_rubber_stamp(self):
        result = analyze_comment("lgtm")
        assert result.is_rubber_stamp
        assert not result.has_substance

    def test_substantive_with_code(self):
        result = analyze_comment("Try this:\n```\ncode here\n```")
        assert result.has_substance
        assert not result.is_rubber_stamp

    def test_substantive_with_link(self):
        result = analyze_comment("See https://example.com for more info.")
        assert result.has_substance

    def test_substantive_by_length(self):
        result = analyze_comment(
            "This is a longer comment that has enough words to be considered "
            "substantive even without code blocks or links."
        )
        assert result.has_substance


class TestReviewThread:
    """Test thread-level analysis."""

    def make_comment(self, author: str, is_author_reply: bool = False) -> ThreadComment:
        return ThreadComment(
            comment_id="123",
            author=author,
            body="test",
            created_at=datetime.now(),
            is_author_reply=is_author_reply,
        )

    def test_standalone_thread(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path="src/main.py",
            line=10,
            comments=[self.make_comment("reviewer")],
        )
        assert thread.resolution_signal == "standalone"
        assert not thread.has_back_and_forth

    def test_resolved_thread(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path="src/main.py",
            line=10,
            comments=[self.make_comment("reviewer")],
            is_resolved=True,
        )
        assert thread.resolution_signal == "resolved"

    def test_outdated_thread(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path="src/main.py",
            line=10,
            comments=[self.make_comment("reviewer")],
            is_outdated=True,
        )
        assert thread.resolution_signal == "outdated"

    def test_discussed_thread(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path="src/main.py",
            line=10,
            comments=[
                self.make_comment("reviewer"),
                self.make_comment("author", is_author_reply=True),
            ],
        )
        assert thread.resolution_signal == "discussed"
        assert thread.has_back_and_forth
        assert thread.author_responded

    def test_ignored_thread(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path="src/main.py",
            line=10,
            comments=[
                self.make_comment("reviewer1"),
                self.make_comment("reviewer2"),  # Another reviewer, not author
            ],
        )
        assert thread.resolution_signal == "ignored"
        assert thread.has_back_and_forth
        assert not thread.author_responded

    def test_initiator(self):
        thread = ReviewThread(
            thread_id="t1",
            pr_number=1,
            path=None,
            line=None,
            comments=[
                self.make_comment("first_person"),
                self.make_comment("second_person"),
            ],
        )
        assert thread.initiator == "first_person"
