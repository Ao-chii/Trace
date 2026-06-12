from app.workers.celery_app import celery_app
from app.workers.tasks import execute_run_task


def test_celery_registers_execute_run_task():
    assert execute_run_task.name == "trace.execute_run"
    assert "app.workers.tasks" in celery_app.conf.include
    assert celery_app.conf.task_default_queue == "trace_test_runs"
