"""v1 baseline

Revision ID: 20260612_0001
Revises: None
Create Date: 2026-06-12
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "20260612_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[2] / "sql" / "init_v1.sql"
    sql = sql_path.read_text(encoding="utf-8")
    for stmt in [x.strip() for x in sql.split(";") if x.strip()]:
        op.execute(stmt)


def downgrade() -> None:
    for table in [
        "test_reports",
        "run_artifacts",
        "run_events",
        "trace_steps",
        "pytest_case_results",
        "generated_test_cases",
        "generated_test_files",
        "run_attempts",
        "run_plan_items",
        "test_runs",
        "test_plans",
        "strategy_versions",
        "project_snapshots",
        "projects",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
