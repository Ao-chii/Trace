from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api import api_router
from app.agents.llm_config import load_llm_config
from app.db.session import get_engine
from app.models import load_all_models
from app.services.strategies import seed_strategy_versions


def create_app(*, initialize: bool = True) -> FastAPI:
    load_all_models()
    if initialize:
        with Session(get_engine()) as session:
            seed_strategy_versions(session)

    app = FastAPI(title="TRACE Backend V1")
    app.include_router(api_router)

    @app.get("/healthz")
    def healthz():
        llm = load_llm_config()
        return {
            "ok": True,
            "llm": None if llm is None else {"provider": llm.provider, "model": llm.model, "base_url": llm.base_url},
        }

    return app


app = create_app(initialize=False)
