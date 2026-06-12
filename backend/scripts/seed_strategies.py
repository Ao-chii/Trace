from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_engine
from app.services.strategies import seed_strategy_versions


def main() -> None:
    with Session(get_engine()) as session:
        seed_strategy_versions(session)
    print("strategy seeds ready")


if __name__ == "__main__":
    main()
