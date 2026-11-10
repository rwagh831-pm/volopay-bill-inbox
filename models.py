"""Data model: one Bill row in the approval inbox."""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field


class Bill(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    vendor: str                                  # who we owe, e.g. "AWS"
    amount: float                                # how much
    currency: str = "USD"
    due_date: Optional[str] = None               # ISO date string "2026-11-30"
    category: Optional[str] = None               # e.g. "cloud", "travel"
    status: str = "pending"                       # pending | approved | paid | rejected
    notes: Optional[str] = None
    source: str = "claude"                        # where it came from
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
