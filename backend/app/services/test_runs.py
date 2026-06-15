from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agents.llm import MockLLM
from app.agents.llm_config import load_llm_config
from app.agents.llm_factory import create_llm
from app.agents.orchestrator import execute_run, freeze_runtime_snapshot, freeze_strategy_snapshot
from app.agents.runtime import PlanInput
from app.core.errors import ErrorCode
from app.core.ids import new_id
from app.models.trace import RunEvent
from app.models.project import ProjectSnapshot
from app.models.strategy import StrategyVersion
from app.models.test_run import TestRun
from app.recorders.sqlalchemy_recorder import SQLAlchemyRunRecorder
from app.repositories.projects import get_project
from app.repositories.strategies import get_strategy_version
from app.repositories.test_plans import get_test_plan
from app.repositories.test_runs import get_test_run, list_test_runs_for_plan, list_test_runs_for_project
from app.schemas.records import TestRunRecord
from app.schemas.strategy import StrategyVersionSpec
from app.services.path_policy import ensure_generated_tests_dir, validate_snapshot_root
from app.tools import default_registry
from app.tools.base import ToolContext
from sqlalchemy.orm import Session


def _default_mock_responder(messages):
    # B 线当前仍以 MockLLM 跑通后端闭环。
    # 这里的 responder 只是本地开发/测试支撑，后续可替换成真实 LLM 工厂。
    blob = "\n".join(m.content for m in messages)
    if "TASK: PLAN" in blob:
        return '{"items":[{"index":0,"target_type":"goal","target_ref":"all","goal":"覆盖目标","planned_assertions":["核心断言"]}]}'
    if "TASK: REFLECT" in blob:
        return '{"fixed_content":"from calc import add\\n\\ndef test_add():\\n    assert add(2, 3) == 5\\n","fix_reason":"修正断言","changed_points":["6 -> 5"],"lowered_assertion_strength":false,"suspected_code_bug":false}'
    return '{"test_file_content":"from calc import add\\n\\ndef test_add():\\n    assert add(2, 3) == 5\\n","cases":[{"test_name":"test_add","target_function":"add","assertion_summary":"2+3=5"}],"coverage_notes":"sync mock run"}'


def _mock_llm():
    # 统一封装默认 mock LLM，避免每个 worker/service 重复拼装。
    return MockLLM(_default_mock_responder)


def _strategy_spec_from_model(model: StrategyVersion) -> StrategyVersionSpec:
    # 把数据库中的策略模型还原成 A 线 execute_run 所需的 StrategyVersionSpec。
    return StrategyVersionSpec(
        id=model.id,
        name=model.name,
        workflow_type=model.workflow_type,
        model_provider=model.model_provider,
        model_name=model.model_name,
        model_params=model.model_params,
        allow_reflection=model.allow_reflection,
        max_tool_calls=model.max_tool_calls,
        prompt_ref=model.prompt_ref,
        tool_schema_ref=model.tool_schema_ref,
    )


def _effective_strategy_spec(model: StrategyVersion) -> StrategyVersionSpec:
    # V1 seeded strategy_versions 默认记录为 mock。运行时统一读取 backend/llm.config.json，
    # 并把 provider/model 冻结进 run snapshot；只有配置文件显式 provider=mock 才使用 MockLLM。
    spec = _strategy_spec_from_model(model)
    config = load_llm_config()
    if config is None:
        raise ValueError("缺少 backend/llm.config.json；后端默认使用真实 LLM，请先创建配置文件")
    return spec.model_copy(
        update={
            "model_provider": config.provider,
            "model_name": config.model,
            "model_params": {
                **dict(spec.model_params or {}),
                "base_url": config.base_url,
                "temperature": config.temperature,
                "max_output_tokens": config.max_output_tokens,
                "reasoning_effort": config.reasoning_effort,
            },
        }
    )


def _llm_for_strategy(strategy_spec: StrategyVersionSpec):
    provider = strategy_spec.model_provider
    if provider == "mock":
        return _mock_llm()
    config = load_llm_config()
    params = dict(strategy_spec.model_params or {})
    return create_llm(
        provider,
        strategy_spec.model_name,
        api_key=(config.api_key if config else None),
        temperature=params.get("temperature"),
        max_output_tokens=int(params.get("max_output_tokens", 8192)),
        base_url=params.get("base_url"),
        reasoning_effort=params.get("reasoning_effort"),
    )


def _artifacts_dir(snapshot_root: Path, run_id: str) -> Path:
    # 每个 run 单独产出一个 artifacts 目录，避免报告和后续产物相互覆盖。
    return snapshot_root / ".trace_artifacts" / run_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_record_from_model(run: TestRun) -> TestRunRecord:
    # ORM 模型和 A 线 recorder/agent 使用的 Pydantic record 之间的桥接。
    return TestRunRecord(
        id=run.id,
        test_plan_id=run.test_plan_id,
        retry_of_run_id=run.retry_of_run_id,
        project_snapshot_id=run.project_snapshot_id,
        runtime_profile_id=run.runtime_profile_id,
        strategy_version_id=run.strategy_version_id,
        runtime_snapshot=run.runtime_snapshot or {},
        strategy_snapshot=run.strategy_snapshot or {},
        status=run.status,
        stage=run.stage,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        total_tokens=run.total_tokens,
        tool_call_count=run.tool_call_count,
        pytest_summary=run.pytest_summary or {},
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_snapshot(
    *,
    budget_override: dict | None = None,
    output_options: dict | None = None,
) -> dict:
    snap = freeze_runtime_snapshot()
    snap["budget_override"] = dict(budget_override or {})
    snap["output_options"] = dict(output_options or {})
    return snap


def create_run(
    session: Session,
    *,
    plan_id: str,
    snapshot_id: str,
    strategy_version_id: str | None = None,
    budget_override: dict | None = None,
    output_options: dict | None = None,
) -> TestRun:
    # 创建 run 只负责落一条“待执行”的主记录，不直接跑 Agent。
    # 这样 API 可以先快速返回，再由 enqueue_run 决定同步或异步执行策略。
    plan = get_test_plan(session, plan_id)
    if plan is None:
        raise ValueError("test plan not found")
    snapshot = session.get(ProjectSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("snapshot not found")
    if snapshot.project_id != plan.project_id:
        raise ValueError("snapshot does not belong to test plan project")
    project = get_project(session, snapshot.project_id)
    if project is None:
        raise ValueError("project not found")
    validate_snapshot_root(snapshot.root_path, project_root=project.local_path)
    strategy_id = strategy_version_id or plan.default_strategy_version_id
    if not strategy_id:
        raise ValueError("strategy_version_id is required when test plan has no default strategy")
    strategy = get_strategy_version(session, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    strategy_spec = _effective_strategy_spec(strategy)

    run = TestRun(
        id=new_id(),
        test_plan_id=plan.id,
        project_snapshot_id=snapshot.id,
        strategy_version_id=strategy.id,
        runtime_snapshot=_runtime_snapshot(budget_override=budget_override, output_options=output_options),
        strategy_snapshot=freeze_strategy_snapshot(strategy_spec),
        status="queued",
        pytest_summary={},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def enqueue_run(
    session: Session,
    *,
    run_id: str,
    budget_override: dict | None = None,
) -> TestRun:
    from app.workers.tasks import execute_run_task

    # 把 run 投递到 Celery 队列。
    # 注意这里只传 run_id 和预算覆盖，详细上下文一律由 worker 回库查询。
    run = get_test_run(session, run_id)
    if run is None:
        raise ValueError("run not found")
    session.add(
        RunEvent(
            id=new_id(),
            run_id=run.id,
            stage=None,
            event_type="run_enqueued",
            status_before=run.status,
            status_after="queued",
            message="运行已加入 Celery 队列",
        )
    )
    session.commit()
    execute_run_task.delay(run.id, budget_override or {})
    session.refresh(run)
    return run


def list_project_test_runs(session: Session, project_id: str) -> list[TestRun]:
    if get_project(session, project_id) is None:
        raise ValueError("project not found")
    return list_test_runs_for_project(session, project_id)


def list_plan_test_runs(session: Session, plan_id: str) -> list[TestRun]:
    if get_test_plan(session, plan_id) is None:
        raise ValueError("test plan not found")
    return list_test_runs_for_plan(session, plan_id)


def execute_run_sync(
    session: Session,
    *,
    run_id: str,
    budget_override: dict | None = None,
) -> TestRun:
    # 这是 run 的真正执行主链路。
    # worker 会调用这里：加载 DB 上下文 -> 构建 ToolContext/Recorder -> 调用 A 线 execute_run。
    run = get_test_run(session, run_id)
    if run is None:
        raise ValueError("run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        return run
    plan = get_test_plan(session, run.test_plan_id)
    try:
        if plan is None:
            raise ValueError("test plan not found")
        snapshot = session.get(ProjectSnapshot, run.project_snapshot_id)
        if snapshot is None:
            raise ValueError("snapshot not found")
        strategy = get_strategy_version(session, run.strategy_version_id)
        if strategy is None:
            raise ValueError("strategy not found")
        project = get_project(session, snapshot.project_id)
        if project is None:
            raise ValueError("project not found")

        # 为生成测试准备受控目录，A 线 write_test_file 只会写到这里。
        root = validate_snapshot_root(snapshot.root_path, project_root=project.local_path)
        test_write_dir = ensure_generated_tests_dir(root)
        recorder = SQLAlchemyRunRecorder(session)
        strategy_spec = _effective_strategy_spec(strategy)
        budget = dict(plan.budget or {})
        stored_override = dict((run.runtime_snapshot or {}).get("budget_override") or {})
        if stored_override:
            budget.update(stored_override)
        if budget_override:
            # 本次 run 允许对计划默认预算做局部覆盖，如更短超时或关闭 reflection。
            budget.update(budget_override)
        llm = _llm_for_strategy(strategy_spec)

        try:
            execute_run(
                tools=ToolContext(root=root, test_write_dir=test_write_dir),
                registry=default_registry(),
                llm=llm,
                recorder=recorder,
                strategy_spec=strategy_spec,
                plan_input=PlanInput(
                    target_scope=list(plan.target_scope or []),
                    goal=plan.goal,
                    allow_reflection=bool(budget.get("allow_reflection", False)),
                    timeout_seconds=int(budget.get("timeout_seconds", 120)),
                ),
                run=_run_record_from_model(run),
                artifacts_dir=_artifacts_dir(root, run.id),
            )
        finally:
            close = getattr(llm, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        _mark_run_failed(session, run.id, f"{type(exc).__name__}: {exc}")
        raise

    refreshed = get_test_run(session, run.id)
    assert refreshed is not None
    return refreshed


def retry_run(session: Session, *, run_id: str) -> TestRun:
    # 重试策略：复制一条新的 run，而不是污染原 run 的 attempt 历史。
    original = get_test_run(session, run_id)
    if original is None:
        raise ValueError("run not found")
    clone = TestRun(
        id=new_id(),
        test_plan_id=original.test_plan_id,
        retry_of_run_id=original.id,
        project_snapshot_id=original.project_snapshot_id,
        strategy_version_id=original.strategy_version_id,
        runtime_snapshot=original.runtime_snapshot,
        strategy_snapshot=original.strategy_snapshot,
        status="queued",
        pytest_summary={},
    )
    session.add(clone)
    session.commit()
    session.refresh(clone)
    return clone


def retry_run_async(session: Session, *, run_id: str) -> TestRun:
    # 先克隆 run，再直接入队，作为 retry 的完整后端动作。
    clone = retry_run(session, run_id=run_id)
    budget_override = dict((clone.runtime_snapshot or {}).get("budget_override") or {})
    return enqueue_run(session, run_id=clone.id, budget_override=budget_override)


def cancel_run(session: Session, *, run_id: str) -> TestRun:
    # V1 软取消：只改 DB 状态，不做进程级中断。
    # 这样接口语义先成立，后续再增强“正在跑的任务如何停止”。
    run = get_test_run(session, run_id)
    if run is None:
        raise ValueError("run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        return run
    session.add(
        RunEvent(
            id=new_id(),
            run_id=run.id,
            stage=run.stage,
            event_type="run_cancelled",
            status_before=run.status,
            status_after="cancelled",
            message="运行已标记为取消",
        )
    )
    run.status = "cancelled"
    run.finished_at = _now()
    session.commit()
    session.refresh(run)
    return run


def _mark_run_failed(session: Session, run_id: str, message: str) -> None:
    session.rollback()
    run = get_test_run(session, run_id)
    if run is None or run.status in {"completed", "failed", "cancelled"}:
        return
    code = ErrorCode.RUNNER_INTERNAL_ERROR.value
    session.add(
        RunEvent(
            id=new_id(),
            run_id=run.id,
            stage=run.stage,
            event_type="run_failed",
            status_before=run.status,
            status_after="failed",
            message=message,
        )
    )
    run.status = "failed"
    run.error_code = code
    run.error_message = message
    run.finished_at = _now()
    session.commit()
