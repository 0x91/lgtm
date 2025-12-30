"""Pydantic models for data validation and Parquet schema."""

from datetime import datetime

from pydantic import BaseModel


class PullRequest(BaseModel):
    """Pull request data."""
    pr_number: int
    pr_id: int
    title: str
    body: str | None
    author_login: str
    author_id: int
    author_is_bot: bool
    state: str
    merged: bool
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    additions: int
    deletions: int
    changed_files: int
    commits: int
    comments_count: int
    review_comments_count: int
    draft: bool
    merge_commit_sha: str | None


class Review(BaseModel):
    """PR review data."""
    review_id: int
    pr_number: int
    reviewer_login: str
    reviewer_id: int
    reviewer_is_bot: bool
    state: str
    body: str | None
    submitted_at: datetime
    commit_id: str


class PRComment(BaseModel):
    """PR-level comment (issue comment on PR)."""
    comment_id: int
    pr_number: int
    author_login: str
    author_id: int
    author_is_bot: bool
    body: str
    created_at: datetime
    updated_at: datetime
    reactions_total: int


class ReviewComment(BaseModel):
    """Inline code review comment."""
    comment_id: str
    pr_number: int
    author_login: str
    author_id: int
    author_is_bot: bool
    body: str
    path: str
    line: int | None
    created_at: datetime
    updated_at: datetime
    is_resolved: bool
    is_outdated: bool


class FileChange(BaseModel):
    """File changed in a PR."""
    pr_number: int
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    module: str


class CheckRun(BaseModel):
    """CI check run."""
    check_id: int
    pr_number: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None


class TimelineEvent(BaseModel):
    """PR timeline event."""
    pr_number: int
    event_type: str
    actor_login: str | None
    created_at: datetime


class User(BaseModel):
    """User dimension."""
    user_id: int
    login: str
    is_bot: bool
    bot_name: str | None
