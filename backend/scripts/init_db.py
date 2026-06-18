from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
for path in (str(BACKEND_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.db.base import Base
from app.db.session import get_engine
from app.models import load_all_models


def _schema_matches_metadata(engine) -> bool:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables.keys())
    if not expected_tables.issubset(existing_tables):
        return False
    for table_name, table in Base.metadata.tables.items():
        existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
        expected_cols = {col.name for col in table.columns}
        if not expected_cols.issubset(existing_cols):
            return False
    return True


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    from app.db.settings import get_database_settings

    cfg.set_main_option("sqlalchemy.url", get_database_settings().database_url)
    return cfg


def main() -> int:
    load_all_models()
    engine = get_engine()

    with Session(engine) as session:
        has_version_table = bool(
            session.execute(text("select to_regclass('public.alembic_version') is not null")).scalar()
        )

    cfg = _alembic_config()
    if not has_version_table and _schema_matches_metadata(engine):
        print("schema already exists; stamping alembic head")
        command.stamp(cfg, "head")
    else:
        print("running alembic upgrade head")
        command.upgrade(cfg, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
