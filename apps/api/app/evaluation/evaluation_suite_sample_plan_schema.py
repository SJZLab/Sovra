"""ORM mirror of `evaluation_suite_sample_plans` (FTF-0603-F1).

The table, its CHECKs, grants and row-level security live in
`0039_eval_suite_sample_plans.py`; `evaluation_suite_lock_gate.py` is the only
writer (one insert-only row per suite, in the lock transaction).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = ["EvaluationSuiteSamplePlan"]


class EvaluationSuiteSamplePlan(Base):
    """The plan a suite was locked under, with the hash it was locked at.
    Mirrors `0039_eval_suite_sample_plans.py`."""

    __tablename__ = "evaluation_suite_sample_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    suite_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    required_categories: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    min_independent_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    min_critical_scenarios: Mapped[int] = mapped_column(Integer, nullable=False)
    min_cases_per_category: Mapped[int] = mapped_column(Integer, nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    independent_task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    critical_scenario_count: Mapped[int] = mapped_column(Integer, nullable=False)
    suite_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("suite_id", name="uq_evaluation_suite_sample_plans_suite_id"),
    )
