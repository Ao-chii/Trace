import sys
import time
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

_BACKEND = Path(__file__).resolve().parent.parent
_TRACE_ROOT = _BACKEND.parent
os.environ.setdefault("TRACE_LLM_CONFIG_FILE", str(_BACKEND / "tests" / "fixtures" / "llm.mock.config.json"))
for p in (str(_BACKEND), str(_TRACE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.db.base import Base  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.models import load_all_models  # noqa: E402


TRACE_TABLES = [
    "test_reports",
    "run_artifacts",
    "trace_steps",
    "run_events",
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
]


def reset_trace_db() -> None:
    try:
        engine = get_engine()
        engine.dispose()
        load_all_models()
        Base.metadata.create_all(bind=engine)
        # Windows + TestClient + PostgreSQL 下，上一条测试刚释放连接时
        # 偶尔会和下一条测试的 TRUNCATE 撞上锁等待。这里做一个轻量重试，
        # 优先把“测试隔离中的短暂锁竞争”吃掉，避免误报为业务死锁。
        last_exc: OperationalError | None = None
        for _ in range(3):
            try:
                with Session(engine) as session:
                    session.execute(text("SET lock_timeout TO '2s'"))
                    for table in TRACE_TABLES:
                        session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                    session.commit()
                return
            except OperationalError as exc:
                last_exc = exc
                time.sleep(0.2)
        assert last_exc is not None
        raise last_exc
    except RuntimeError as exc:
        pytest.skip(f"TRACE_DB_URL 未配置，跳过 PostgreSQL 集成测试: {exc}")
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过数据库集成测试: {exc}")


@pytest.fixture
def clean_db():
    reset_trace_db()
