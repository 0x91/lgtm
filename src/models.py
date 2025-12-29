"""Pydantic models for data validation and Parquet schema."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PullRequest(BaseModel):
    """Pull request data."""
    pr_number: int
    pr_id: int
    title: str
    body: Optional[str]
    author_login: str
    author_id: int
    author_is_bot: bool
    state: str
    merged: bool
    created_at: datetime
    updated_at: datetime
    merged_at: Optional[datetime]
    closed_at: Optional[datetime]
    additions: int
    deletions: int
    changed_files: int
    commits: int
    comments_count: int
    review_comments_count: int
    draft: bool
    merge_commit_sha: Optional[str]


class Review(BaseModel):
    """PR review data."""
    review_id: int
    pr_number: int
    reviewer_login: str
    reviewer_id: int
    reviewer_is_bot: bool
    state: str
    body: Optional[str]
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
    author_is_bot: bool
    body: str
    path: str
    line: Optional[int]
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
    conclusion: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[int]


class TimelineEvent(BaseModel):
    """PR timeline event."""
    pr_number: int
    event_type: str
    actor_login: Optional[str]
    created_at: datetime


class User(BaseModel):
    """User dimension."""
    user_id: int
    login: str
    is_bot: bool
    bot_name: Optional[str]
