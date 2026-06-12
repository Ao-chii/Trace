from __future__ import annotations

from celery import Celery

from app.db.redis_settings import get_redis_url


def create_celery_app() -> Celery:
    redis_url = get_redis_url()
    app = Celery(
        "trace_backend",
        broker=redis_url,
        backend=redis_url,
        include=["app.workers.tasks"],
    )
    app.conf.task_default_queue = "trace_test_runs"
    app.conf.task_serializer = "json"
    app.conf.result_serializer = "json"
    app.conf.accept_content = ["json"]
    return app


celery_app = create_celery_app()
