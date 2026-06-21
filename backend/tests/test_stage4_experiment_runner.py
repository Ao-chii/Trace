from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.final_attempts import final_attempts_from_records
from app.db.session import get_engine
from app.main import create_app
from app.models import (
    BugVariant,
    Experiment,
    ExperimentCleanRun,
    ExperimentReplayRun,
    ProjectSnapshot,
    PytestCaseResult,
    RunArtifact,
    RunAttempt,
    RunPlanItem,
    TestPlan,
    TestReplay as ReplayModel,
    TestRun,
    TraceStep,
)
from app.core.ids import new_id
from app.services.evaluation import create_bug_variant, create_eval_dataset, create_eval_task, create_seeded_bug
from app.services.evaluation_seed import DEMO_DATASET_ID, DEMO_SNAPSHOT_ID, seed_demo_dataset
from app.services.experiments import (
    ExperimentError,
    _copy_clean_snapshot,
    _empty_final_test_set,
    _store_generated_test_set_artifact,
    _run_replay,
    _replay_timeout_seconds,
    get_experiment_metrics,
    run_experiment,
)
from app.services.strategies import seed_strategy_versions
from app.workers.celery_app import celery_app
from eval.demo.bugs import BUGS
from eval.harness.run_eval import run_full_eval, run_full_eval_via_service


def _seed_v2_demo() -> None:
    with Session(get_engine()) as session:
        seed_strategy_versions(session)
        seed_demo_dataset(session)


def _mark_wrong_status_as_confirmed_auto_mutation() -> None:
    bug = next(item for item in BUGS if item.id == "wrong-status")
    with Session(get_engine()) as session:
        variant = session.get(BugVariant, "variant-wrong-status")
        assert variant is not None
        ground_truth = dict(variant.ground_truth or {})
        ground_truth["source"] = "auto_mutation"
        ground_truth["mutation"] = {
            "candidate_id": "mut-demo-wrong-status",
            "eval_task_id": "task-demo-shop-pricing-v2",
            "source_snapshot_id": DEMO_SNAPSHOT_ID,
            "operator": "unknown",
            "patch": {"file": bug.file, "old": bug.old, "new": bug.new},
            "matcher": {
                "matcher_kind": "operator_signature",
                "source_path": bug.file,
                "start_line": 1,
                "end_line": 1,
                "original_content_hash": "sha256-demo-wrong-status",
                "operator": "unknown",
                "target_symbol": bug.target,
            },
            "selection": {
                "status": "selected",
                "selected_by": "auto_sampler",
                "reason": "stage4 sampled mutation score fixture",
                "sample_seed": 7,
                "sample_index": 0,
            },
            "probe": {},
        }
        ground_truth["probe"] = {
            "target_kind": bug.kind,
            "probe": bug.probe,
            "clean_value": bug.clean_value,
            "buggy_value": bug.buggy_value,
            "probe_check": {
                "status": "passed",
                "target_kind": bug.kind,
                "probe": bug.probe,
                "clean_expected": bug.clean_value,
                "clean_actual": bug.clean_value,
                "buggy_expected": bug.buggy_value,
                "buggy_actual": bug.buggy_value,
            },
        }
        variant.ground_truth = ground_truth
        session.commit()


def _mark_variant_as_auto_mutation_probe_status(
    session: Session,
    variant_id: str,
    *,
    probe_status: str | None,
) -> None:
    bug_id = variant_id.removeprefix("variant-")
    bug = next(item for item in BUGS if item.id == bug_id)
    variant = session.get(BugVariant, variant_id)
    assert variant is not None
    ground_truth = dict(variant.ground_truth or {})
    ground_truth["source"] = "auto_mutation"
    ground_truth["mutation"] = {
        "candidate_id": f"mut-demo-{bug.id}",
        "eval_task_id": "task-demo-shop-pricing-v2",
        "source_snapshot_id": DEMO_SNAPSHOT_ID,
        "operator": "unknown",
        "patch": {"file": bug.file, "old": bug.old, "new": bug.new},
        "matcher": {
            "matcher_kind": "operator_signature",
            "source_path": bug.file,
            "start_line": 1,
            "end_line": 1,
            "original_content_hash": f"sha256-demo-{bug.id}",
            "operator": "unknown",
            "target_symbol": bug.target,
        },
        "selection": {
            "status": "selected",
            "selected_by": "auto_sampler",
            "reason": "sampled mutation denominator fixture",
            "sample_seed": 7,
            "sample_index": 0,
        },
        "probe": {},
    }
    probe = {
        "target_kind": bug.kind,
        "probe": bug.probe,
        "clean_value": bug.clean_value,
        "buggy_value": bug.buggy_value,
    }
    if probe_status is not None:
        probe["probe_check"] = {
            "status": probe_status,
            "target_kind": bug.kind,
            "probe": bug.probe,
            "clean_expected": bug.clean_value,
            "clean_actual": bug.clean_value,
            "buggy_expected": bug.buggy_value,
            "buggy_actual": bug.buggy_value if probe_status == "passed" else bug.clean_value,
            "reason": "denominator fixture failed probe" if probe_status != "passed" else "",
        }
    ground_truth["probe"] = probe
    variant.ground_truth = ground_truth
    session.add(variant)


def _run_celery_eager(monkeypatch) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def _passed_nodeids(summary: dict) -> set[str]:
    return {
        str(case.get("nodeid"))
        for case in summary.get("case_results", [])
        if isinstance(case, dict) and case.get("status") == "passed" and case.get("nodeid")
    }


def _assertion_failure_nodeids(summary: dict) -> set[str]:
    return {
        str(case.get("nodeid"))
        for case in summary.get("case_results", [])
        if isinstance(case, dict)
        and case.get("status") == "failed"
        and case.get("failure_type") == "assertion"
        and case.get("nodeid")
    }


def test_experiment_runner_creates_clean_runs_replays_and_db_metrics(monkeypatch, tmp_path, clean_db):
    _run_celery_eager(monkeypatch)
    _seed_v2_demo()
    _mark_wrong_status_as_confirmed_auto_mutation()
    app = create_app()

    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-stage4-demo",
                "name": "stage4 demo",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1", "sv-plan-v1", "sv-react-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["status"] == "draft"

        run_response = client.post("/api/v1/experiments/exp-stage4-demo/runs")
        assert run_response.status_code == 200, run_response.text
        experiment = run_response.json()
        assert experiment["status"] == "completed"

        metrics_response = client.get("/api/v1/experiments/exp-stage4-demo/metrics")
        assert metrics_response.status_code == 200, metrics_response.text
        metrics = metrics_response.json()

    rows = {row["strategy_id"]: row for row in metrics["rows"]}
    assert rows["direct"]["captured_per_repeat"] == [5]
    assert rows["direct"]["false_positive_rate"] == 1.0
    assert rows["direct"]["cost_per_captured_bug_status"] == "ok"
    assert rows["direct"]["avg_duration_ms"] > 0
    assert rows["direct"]["pytest_collection_success_rate"] == 1.0
    assert rows["direct"]["reflection_contract_pass_rate"] is None
    assert rows["direct"]["reflection_acceptance_rate"] is None
    assert rows["plan_execute"]["captured_per_repeat"] == [6]
    assert rows["plan_execute"]["false_positive_rate"] == 0.0
    assert rows["react_reflection"]["captured_per_repeat"] == [6]
    assert rows["react_reflection"]["reflection_used"] is True
    assert rows["react_reflection"]["reflection_contract_pass_rate"] == 1.0
    assert rows["react_reflection"]["reflection_acceptance_rate"] == 1.0

    matrix = metrics["capture_matrix"]
    matrix_counts = metrics["capture_matrix_counts"]
    assert matrix["variant-wrong-status"]["direct"] is False
    assert matrix["variant-wrong-status"]["plan_execute"] is True
    assert matrix["variant-wrong-status"]["react_reflection"] is True
    assert matrix_counts["variant-wrong-status"]["direct"]["captured_count"] == 0
    assert matrix_counts["variant-wrong-status"]["direct"]["repeat_count"] == 1
    assert matrix_counts["variant-wrong-status"]["plan_execute"]["capture_rate"] == 1.0
    assert metrics["capture_matrix_counts"] == matrix_counts
    assert metrics["capture_scope"]["unit"] == "bug_variant"
    assert metrics["capture_scope"]["single_variant_per_seeded_bug"] is True
    assert metrics["data_source"]["kind"] == "mock"
    sampled = metrics["sampled_mutation_score"]
    sampled_rows = {row["strategy_id"]: row for row in sampled["rows"]}
    assert sampled["status"] == "ok"
    assert sampled["included_variant_ids"] == ["variant-wrong-status"]
    assert sampled["excluded_variant_counts"]["non_auto_mutation"] == 5
    assert sampled_rows["direct"]["score"] == 0.0
    assert sampled_rows["direct"]["captured_mutant_count"] == 0
    assert sampled_rows["plan_execute"]["score"] == 1.0
    assert sampled_rows["react_reflection"]["score"] == 1.0
    assert sampled["matrix_counts"]["variant-wrong-status"]["direct"]["replayed_count"] == 1

    harness = run_full_eval(tmp_path / "harness", repeats=1)
    harness_rows = {row["strategy_id"]: row for row in harness["rows"]}
    assert rows["direct"]["captured_per_repeat"] == harness_rows["sv-direct-v1"]["captured_per_repeat"]
    assert rows["plan_execute"]["captured_per_repeat"] == harness_rows["sv-plan-v1"]["captured_per_repeat"]
    assert rows["react_reflection"]["captured_per_repeat"] == harness_rows["sv-react-v1"]["captured_per_repeat"]

    assert len(metrics["clean_runs"]) == 3
    assert len(metrics["experiment_replay_runs"]) == 18
    assert all(replay["llm_calls"] == 0 for replay in metrics["replay_runs"])
    assert any(replay["bug_variant_id"] is None for replay in metrics["replay_runs"])
    events = metrics["evaluation_events"]
    event_types = {event["event_type"] for event in events}
    assert {"probe_check_passed", "replay_captured", "replay_uncaptured"}.issubset(event_types)
    assert all(event["event_id"].startswith("evt-") for event in events)
    assert all(event["created_from"] == "experiment_service_projection" for event in events)
    assert all("clean_metrics" not in event["payload"] for event in events)
    for clean in metrics["clean_runs"]:
        clean_replay = clean["clean_metrics"]["clean_replay"]
        assert clean["clean_metrics"]["clean_replay_id"]
        assert clean_replay["collection_errors"] == 0
        assert clean_replay["duration_ms"] >= 0
        assert clean["clean_metrics"]["flaky_check"]["stable"] is True
        assert clean["clean_metrics"]["flaky_check"]["required_runs"] == 3
    for replay_result in metrics["experiment_replay_runs"]:
        assert replay_result["replay_metrics"]["clean_replay_id"]
        probe_check = replay_result["replay_metrics"]["probe_check"]
        assert probe_check["status"] == "passed"
        assert probe_check["clean_actual"] == probe_check["clean_expected"]
        assert probe_check["buggy_actual"] == probe_check["buggy_expected"]

    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(ExperimentCleanRun)) == 3
        assert session.scalar(select(func.count()).select_from(ExperimentReplayRun)) == 18
        assert session.scalar(select(func.count()).select_from(ReplayModel)) == 27

        strategy_key = {
            "sv-direct-v1": "direct",
            "sv-plan-v1": "plan_execute",
            "sv-react-v1": "react_reflection",
        }
        audited_captures: dict[str, dict[int, int]] = {key: {} for key in strategy_key.values()}
        audited_matrix: dict[str, dict[str, bool]] = {variant_id: {} for variant_id in matrix}
        artifacts = list(session.scalars(select(RunArtifact).where(RunArtifact.artifact_type == "generated_test_set")))
        assert len(artifacts) == 3
        for artifact in artifacts:
            data = Path(artifact.uri).read_bytes()
            assert hashlib.sha256(data).hexdigest() == artifact.content_hash
            payload = json.loads(data.decode("utf-8"))
            assert payload["files"]
            assert artifact.metadata_json["kind"] == "generated_test_set"
            assert artifact.metadata_json["experiment_id"] == "exp-stage4-demo"

        clean_runs = list(
            session.scalars(
                select(ExperimentCleanRun)
                .where(ExperimentCleanRun.experiment_id == "exp-stage4-demo")
                .order_by(ExperimentCleanRun.strategy_version_id.asc(), ExperimentCleanRun.repeat_index.asc())
            )
        )
        for clean in clean_runs:
            run = session.get(TestRun, clean.clean_run_id)
            assert run is not None
            assert run.status == "completed"
            assert clean.clean_metrics["total_tokens"] == run.total_tokens
            assert clean.clean_metrics["tool_call_count"] == run.tool_call_count

            attempts = list(
                session.scalars(
                    select(RunAttempt)
                    .join_from(RunAttempt, RunPlanItem)
                    .where(RunPlanItem.run_id == clean.clean_run_id)
                )
            )
            results = list(
                session.scalars(
                    select(PytestCaseResult).where(PytestCaseResult.attempt_id.in_([attempt.id for attempt in attempts]))
                )
            )
            final_ids = {attempt.id for attempt in final_attempts_from_records(attempts, results).values()}
            final_results = [result for result in results if result.attempt_id in final_ids]
            assert sum(1 for result in final_results if result.status == "passed") == clean.clean_metrics["final_passed"]
            assert sum(1 for result in final_results if result.status in {"failed", "error"}) == clean.clean_metrics["final_failed"]

            clean_replay = session.scalar(
                select(ReplayModel).where(
                    ReplayModel.experiment_clean_run_id == clean.id,
                    ReplayModel.bug_variant_id.is_(None),
                )
            )
            assert clean_replay is not None
            clean_passed = _passed_nodeids(clean_replay.pytest_summary or {})
            key = strategy_key[clean.strategy_version_id]
            audited_captures[key].setdefault(clean.repeat_index, 0)
            variant_replays = list(
                session.scalars(
                    select(ReplayModel)
                    .where(
                        ReplayModel.experiment_clean_run_id == clean.id,
                        ReplayModel.bug_variant_id.is_not(None),
                    )
                    .order_by(ReplayModel.bug_variant_id.asc())
                )
            )
            for replay in variant_replays:
                assertion_failures = _assertion_failure_nodeids(replay.pytest_summary or {})
                capturing = clean_passed & assertion_failures
                captured = bool(capturing)
                audited_captures[key][clean.repeat_index] += int(captured)
                audited_matrix.setdefault(replay.bug_variant_id, {})[key] = (
                    audited_matrix.setdefault(replay.bug_variant_id, {}).get(key, False) or captured
                )
                replay_result = session.scalar(
                    select(ExperimentReplayRun).where(ExperimentReplayRun.replay_id == replay.id)
                )
                assert replay_result is not None
                assert replay_result.captured_bug is captured
                assert set(replay_result.replay_metrics["capturing_nodeids"]) == capturing
                assert replay_result.replay_metrics["probe_check"]["status"] == "passed"

        for row in metrics["rows"]:
            per_repeat = audited_captures[row["strategy_id"]]
            assert row["captured_per_repeat"] == [per_repeat[index] for index in sorted(per_repeat)]
        assert audited_matrix == metrics["capture_matrix"]


def test_sampled_mutation_score_denominator_requires_confirmed_probe_passed(clean_db):
    _seed_v2_demo()

    with Session(get_engine()) as session:
        _mark_variant_as_auto_mutation_probe_status(
            session,
            "variant-wrong-status",
            probe_status="passed",
        )
        _mark_variant_as_auto_mutation_probe_status(
            session,
            "variant-cmp-flip-discount",
            probe_status="failed",
        )
        _mark_variant_as_auto_mutation_probe_status(
            session,
            "variant-boundary-shipping",
            probe_status=None,
        )
        session.commit()

        import app.services.experiments as experiment_module

        experiment_module.create_experiment(
            session,
            experiment_id="exp-sampled-denominator",
            name="sampled denominator",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        metrics = get_experiment_metrics(session, "exp-sampled-denominator")

    sampled = metrics["sampled_mutation_score"]
    assert sampled["status"] == "incomplete_replay"
    assert sampled["denominator_source"] == (
        "bug_variants.ground_truth.source=auto_mutation + "
        "ground_truth.probe.probe_check.status=passed"
    )
    assert sampled["mutant_count"] == 1
    assert sampled["included_variant_ids"] == ["variant-wrong-status"]
    assert sampled["excluded_variant_counts"] == {
        "non_auto_mutation": 3,
        "probe_check_not_passed": 2,
    }
    assert "variant-cmp-flip-discount" not in sampled["included_variant_ids"]
    assert "variant-boundary-shipping" not in sampled["included_variant_ids"]

def test_experiment_fails_if_generated_test_set_artifact_hash_changes(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    original_store = experiment_module._store_generated_test_set_artifact

    def store_and_corrupt(*args, **kwargs):
        artifact = original_store(*args, **kwargs)
        Path(artifact.uri).write_bytes(b'{"files":{"tests/generated/test_corrupted.py":"def test_corrupted():\\n    assert False\\n"}}')
        return artifact

    monkeypatch.setattr(experiment_module, "_store_generated_test_set_artifact", store_and_corrupt)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-corrupt-artifact",
            name="corrupt artifact",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        with pytest.raises(ExperimentError, match="generated test set artifact hash mismatch"):
            run_experiment(session, "exp-corrupt-artifact")

        experiment = session.get(Experiment, "exp-corrupt-artifact")
        assert experiment is not None
        assert experiment.status == "failed"
        assert experiment.error_code == "ExperimentError"
        assert "generated test set artifact hash mismatch" in (experiment.error_message or "")
        metrics = get_experiment_metrics(session, "exp-corrupt-artifact")
        assert any(event["event_type"] == "artifact_hash_mismatch" for event in metrics["evaluation_events"])


def test_experiment_fails_when_variant_probe_does_not_match_ground_truth(clean_db):
    _seed_v2_demo()

    with Session(get_engine()) as session:
        variant = session.get(BugVariant, "variant-cmp-flip-discount")
        assert variant is not None
        ground_truth = dict(variant.ground_truth or {})
        ground_truth["buggy_value"] = ground_truth["clean_value"]
        variant.ground_truth = ground_truth
        session.commit()

        import app.services.experiments as experiment_module

        experiment_module.create_experiment(
            session,
            experiment_id="exp-bad-probe",
            name="bad probe",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        with pytest.raises(ExperimentError, match="probe clean_value and buggy_value must differ"):
            run_experiment(session, "exp-bad-probe")

        experiment = session.get(Experiment, "exp-bad-probe")
        assert experiment is not None
        assert experiment.status == "failed"
        assert experiment.error_code == "ExperimentError"
        assert "variant-cmp-flip-discount" in (experiment.error_message or "")
        replay = session.scalar(
            select(ReplayModel).where(ReplayModel.bug_variant_id == "variant-cmp-flip-discount")
        )
        assert replay is not None
        assert replay.status == "failed"
        assert replay.error_code == "ExperimentError"
        metrics = get_experiment_metrics(session, "exp-bad-probe")
        assert any(event["event_type"] == "probe_check_failed" for event in metrics["evaluation_events"])


def test_experiment_fails_when_clean_run_provider_call_fails(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    def fail_clean_run(session, *, run_id, budget_override=None, make_llm=None, evaluation_events=None):
        run = session.get(TestRun, run_id)
        run.status = "failed"
        run.error_code = "LLM_PROVIDER_ERROR"
        run.error_message = "provider rejected request"
        session.commit()
        return run

    monkeypatch.setattr(experiment_module, "execute_run_sync", fail_clean_run)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-provider-failure",
            name="provider failure",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "openai", "model": "gpt-5"},
        )
        with pytest.raises(ExperimentError, match="clean run failed before replay: LLM_PROVIDER_ERROR"):
            run_experiment(session, "exp-provider-failure")

        experiment = session.get(Experiment, "exp-provider-failure")
        assert experiment is not None
        assert experiment.status == "failed"
        assert experiment.error_code == "ExperimentError"
        assert "provider rejected request" in (experiment.error_message or "")
        assert session.scalar(
            select(func.count()).select_from(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-provider-failure")
        ) == 0
        metrics = get_experiment_metrics(session, "exp-provider-failure")
        assert any(event["event_type"] == "provider_failure" for event in metrics["evaluation_events"])


def test_experiment_run_route_enqueues_without_sync(monkeypatch, clean_db):
    calls = []

    class DummyTask:
        def delay(self, experiment_id):
            calls.append(experiment_id)

    import app.workers.tasks as task_module

    monkeypatch.setattr(task_module, "execute_experiment_task", DummyTask())
    _seed_v2_demo()
    app = create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-stage4-queued",
                "name": "stage4 queued",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert created.status_code == 200
        run = client.post("/api/v1/experiments/exp-stage4-queued/runs")
        assert run.status_code == 200
        assert run.json()["status"] == "queued"
        assert calls == ["exp-stage4-queued"]


def test_experiment_metrics_missing_experiment_returns_404(clean_db):
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/experiments/missing-experiment/metrics")

    assert response.status_code == 404
    assert response.json()["detail"] == "experiment not found"


def test_experiment_progress_reports_running_strategy_and_trace_step(clean_db):
    _seed_v2_demo()
    app = create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-progress-running",
                "name": "progress running",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1", "sv-plan-v1", "sv-react-v1"],
                "repeat_count": 2,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert created.status_code == 200, created.text

        with Session(get_engine()) as session:
            snapshot = session.get(ProjectSnapshot, DEMO_SNAPSHOT_ID)
            assert snapshot is not None
            experiment = session.get(Experiment, "exp-progress-running")
            assert experiment is not None
            experiment.status = "running"
            plan = TestPlan(
                id="plan-progress-running",
                project_id=snapshot.project_id,
                name="progress running",
                target_scope=[],
                goal="progress",
                budget={},
                output_options={},
                default_strategy_version_id="sv-plan-v1",
            )
            run = TestRun(
                id=new_id(),
                test_plan_id=plan.id,
                project_snapshot_id=snapshot.id,
                strategy_version_id="sv-plan-v1",
                runtime_snapshot={
                    "output_options": {
                        "experiment_id": experiment.id,
                        "eval_task_id": "task-demo-shop-pricing-v2",
                        "repeat_index": 1,
                    }
                },
                strategy_snapshot={},
                status="running",
                stage="generating",
                pytest_summary={},
            )
            session.add_all([plan, run])
            session.flush()
            session.add(
                TraceStep(
                    id=new_id(),
                    run_id=run.id,
                    step_index=3,
                    step_type="tool_call",
                    name="read source",
                    tool_name="read_file",
                    status="ok",
                )
            )
            session.commit()

        response = client.get("/api/v1/experiments/exp-progress-running/progress")
        assert response.status_code == 200, response.text
        progress = response.json()
        assert progress["status"] == "running"
        assert progress["current_strategy_version_id"] == "sv-plan-v1"
        assert progress["current_strategy_index"] == 2
        assert progress["current_eval_task_id"] == "task-demo-shop-pricing-v2"
        assert progress["current_repeat_index"] == 1
        assert progress["run_stage"] == "generating"
        assert progress["latest_trace_step"]["tool_name"] == "read_file"
        assert progress["clean_runs_total_estimate"] == 6


def test_completed_experiment_is_not_rerun_in_place(monkeypatch, clean_db):
    _run_celery_eager(monkeypatch)
    _seed_v2_demo()
    app = create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-no-rerun",
                "name": "no rerun",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert created.status_code == 200
        assert client.post("/api/v1/experiments/exp-no-rerun/runs").json()["status"] == "completed"
        again = client.post("/api/v1/experiments/exp-no-rerun/runs")
        assert again.status_code == 200
        assert again.json()["status"] == "completed"

    with Session(get_engine()) as session:
        clean_count = session.scalar(
            select(func.count()).select_from(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-no-rerun")
        )
        assert clean_count == 1


def test_run_experiment_observes_mid_run_cancel(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    def cancel_during_execute(session, *, run_id, budget_override=None, make_llm=None, evaluation_events=None):
        experiment = session.get(experiment_module.Experiment, "exp-cancel-mid-run")
        experiment.status = "cancelled"
        experiment.finished_at = experiment_module._now()
        session.commit()
        return session.get(experiment_module.TestRun, run_id)

    monkeypatch.setattr(experiment_module, "execute_run_sync", cancel_during_execute)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-cancel-mid-run",
            name="cancel mid run",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-cancel-mid-run")
        assert result["status"] == "cancelled"
        assert session.scalar(
            select(func.count()).select_from(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-cancel-mid-run")
        ) == 0


def test_experiment_workdir_rejects_paths_outside_root(tmp_path):
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    (clean_root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    snapshot = ProjectSnapshot(
        id="snapshot-safe-copy",
        project_id="project-safe-copy",
        source_kind="local_path",
        root_path=str(clean_root),
    )

    with pytest.raises(ExperimentError, match="escapes"):
        _copy_clean_snapshot(snapshot, tmp_path / "outside")


def test_experiment_create_rejects_unsafe_id(clean_db):
    _seed_v2_demo()
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/experiments",
            json={
                "id": "../bad",
                "name": "bad id",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert response.status_code == 422


def _add_second_demo_task() -> None:
    with Session(get_engine()) as session:
        task = create_eval_task(
            session,
            dataset_id=DEMO_DATASET_ID,
            task_id="task-demo-shop-pricing-v2-extra",
            project_snapshot_id=DEMO_SNAPSHOT_ID,
            target_scope={"targets": [bug.target for bug in BUGS], "bug_count": len(BUGS), "source": "test-extra-task"},
            goal="Generate tests for the duplicated task scope.",
            expected_capabilities=["boundary_value", "comparison_logic", "input_validation", "http_status_contract"],
        )
        for bug in BUGS:
            seeded = create_seeded_bug(
                session,
                eval_task_id=task.id,
                bug_id=f"{bug.id}-extra",
                bug_type=bug.bug_type,
                description=bug.description,
                expected_detection=bug.expected_detection,
            )
            create_bug_variant(
                session,
                seeded_bug_id=seeded.id,
                variant_id=f"variant-{bug.id}-extra",
                variant_name=f"{bug.id} duplicate canonical patch",
                patch={"file": bug.file, "old": bug.old, "new": bug.new},
                ground_truth={
                    "target": bug.target,
                    "target_kind": bug.kind,
                    "probe": bug.probe,
                    "clean_value": bug.clean_value,
                    "buggy_value": bug.buggy_value,
                },
            )


def test_metrics_group_multiple_tasks_into_one_repeat(monkeypatch, clean_db):
    _run_celery_eager(monkeypatch)
    _seed_v2_demo()
    _add_second_demo_task()
    app = create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-multi-task",
                "name": "multi task",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert created.status_code == 200
        assert client.post("/api/v1/experiments/exp-multi-task/runs").json()["status"] == "completed"
        metrics = client.get("/api/v1/experiments/exp-multi-task/metrics").json()

    [row] = metrics["rows"]
    assert row["repeats"] == 1
    assert row["total_in_scope"] == 12
    assert len(row["captured_per_repeat"]) == 1
    assert len(metrics["clean_runs"]) == 2


def test_experiment_metrics_use_null_cost_when_no_bug_is_captured(monkeypatch, clean_db):
    _run_celery_eager(monkeypatch)
    _seed_v2_demo()
    app = create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "id": "exp-zero-capture",
                "name": "zero capture",
                "dataset_id": DEMO_DATASET_ID,
                "strategy_version_ids": ["sv-direct-v1"],
                "repeat_count": 1,
                "llm_override": {"provider": "mock", "model": "mock-1"},
            },
        )
        assert created.status_code == 200
        assert client.post("/api/v1/experiments/exp-zero-capture/runs").status_code == 200

    with Session(get_engine()) as session:
        for replay_result in session.scalars(select(ExperimentReplayRun)):
            replay_result.captured_bug = False
            replay_result.replay_metrics = {
                "capture_rule": "clean_passed_variant_assertion_failure_same_nodeid",
                "clean_passed_nodeids": [],
                "variant_assertion_failure_nodeids": [],
                "capturing_nodeids": [],
            }
        session.commit()

    with TestClient(app) as client:
        metrics = client.get("/api/v1/experiments/exp-zero-capture/metrics").json()
    [row] = metrics["rows"]
    assert row["captured_per_repeat"] == [0]
    assert row["cost_per_captured_bug"] is None
    assert row["cost_per_captured_bug_status"] == "no_bug_captured"
    assert row["metric_status"] == "evaluable_zero_capture"


def test_replay_timeout_uses_runtime_snapshot():
    assert _replay_timeout_seconds({"timeout_seconds": 17}) == 17
    assert _replay_timeout_seconds({"budget_override": {"timeout_seconds": "23"}}) == 23
    assert _replay_timeout_seconds({"timeout_seconds": "bad"}, default=11) == 11


def _create_cache_replay_fixture(
    session: Session,
    *,
    experiment_id: str,
    plan_id: str,
    runtime_snapshot: dict | None = None,
) -> tuple[Experiment, ProjectSnapshot, TestRun, RunArtifact, ExperimentCleanRun, BugVariant]:
    experiment = Experiment(
        id=experiment_id,
        name=experiment_id,
        dataset_id=DEMO_DATASET_ID,
        repeat_count=1,
        llm_override={"provider": "mock", "model": "mock-1"},
        status="running",
    )
    session.add(experiment)
    snapshot = session.get(ProjectSnapshot, DEMO_SNAPSHOT_ID)
    assert snapshot is not None
    snapshot_runtime = runtime_snapshot or {
        "executor": "local_subprocess",
        "test_command": "python -m pytest tests -q",
        "network_policy": "default",
        "timeout_seconds": 120,
        "resource_limits": {"timeout_seconds": 120},
        "env_keys": [],
    }
    plan = TestPlan(
        id=plan_id,
        project_id=snapshot.project_id,
        name="unused",
        target_scope=[],
        goal="unused",
        budget={},
        output_options={},
        default_strategy_version_id="sv-direct-v1",
    )
    run = TestRun(
        id=new_id(),
        test_plan_id=plan.id,
        project_snapshot_id=snapshot.id,
        strategy_version_id="sv-direct-v1",
        runtime_snapshot=snapshot_runtime,
        strategy_snapshot={},
        status="completed",
        pytest_summary={},
    )
    session.add_all([plan, run])
    session.commit()
    clean_id = new_id()
    final_set = _empty_final_test_set()
    final_set.files = {"tests/generated/test_cache.py": "def test_cache_replay():\n    assert True\n"}
    artifact = _store_generated_test_set_artifact(
        session,
        run_id=run.id,
        experiment_id=experiment.id,
        clean_run_id=clean_id,
        final_set=final_set,
    )
    clean = ExperimentCleanRun(
        id=clean_id,
        experiment_id=experiment.id,
        eval_task_id="task-demo-shop-pricing-v2",
        strategy_version_id="sv-direct-v1",
        repeat_index=0,
        clean_run_id=run.id,
        generated_test_set_artifact_id=artifact.id,
        false_positive=False,
        clean_metrics={"validity_status": "evaluable"},
    )
    session.add(clean)
    session.commit()
    variant = session.get(BugVariant, "variant-wrong-status")
    assert variant is not None
    return experiment, snapshot, run, artifact, clean, variant


def test_variant_replay_reuses_completed_cache_hit(clean_db):
    _seed_v2_demo()
    with Session(get_engine()) as session:
        experiment, snapshot, _run, artifact, clean, variant = _create_cache_replay_fixture(
            session,
            experiment_id="exp-cache-hit",
            plan_id="unused-plan",
        )

        first, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert first.cache_status in {"miss", "stale"}
        second, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert second.status == "completed"
        assert second.cache_status == "hit"
        assert second.source_replay_id == first.id
        metrics = get_experiment_metrics(session, experiment.id)
        assert metrics["runtime_execution"]["reused_replay_count"] == 1
        assert any(event["event_type"] == "replay_cache_hit" for event in metrics["evaluation_events"])


def test_variant_replay_marks_stale_when_runtime_changes(clean_db):
    _seed_v2_demo()
    with Session(get_engine()) as session:
        experiment, snapshot, run, artifact, clean, variant = _create_cache_replay_fixture(
            session,
            experiment_id="exp-cache-stale",
            plan_id="unused-plan-stale",
        )

        first, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert first.status == "completed"
        run.runtime_snapshot = {**dict(run.runtime_snapshot), "test_command": "python -m pytest tests -q -k cache"}
        session.commit()
        second, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert second.status == "completed"
        assert second.cache_status == "stale"
        assert second.source_replay_id is None
        assert "runtime_profile_changed" in second.executor_metadata["cache_invalidation_reasons"]
        metrics = get_experiment_metrics(session, experiment.id)
        assert any(event["event_type"] == "replay_cache_stale" for event in metrics["evaluation_events"])


def test_variant_replay_marks_stale_when_generated_artifact_changes(clean_db):
    _seed_v2_demo()
    with Session(get_engine()) as session:
        _experiment, snapshot, _run, artifact, clean, variant = _create_cache_replay_fixture(
            session,
            experiment_id="exp-cache-stale-artifact",
            plan_id="unused-plan-stale-artifact",
        )
        first, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert first.status == "completed"
        payload = {"files": {"tests/generated/test_cache.py": "def test_cache_replay():\n    assert 2 + 2 == 4\n"}}
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        Path(artifact.uri).write_bytes(data)
        artifact.content_hash = hashlib.sha256(data).hexdigest()
        artifact.size_bytes = len(data)
        session.commit()

        second, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )

        assert second.status == "completed"
        assert second.cache_status == "stale"
        assert "generated_test_set_changed" in second.executor_metadata["cache_invalidation_reasons"]


def test_variant_replay_marks_stale_when_variant_ground_truth_changes(clean_db):
    _seed_v2_demo()
    with Session(get_engine()) as session:
        _experiment, snapshot, _run, artifact, clean, variant = _create_cache_replay_fixture(
            session,
            experiment_id="exp-cache-stale-variant",
            plan_id="unused-plan-stale-variant",
        )
        first, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )
        assert first.status == "completed"
        variant.ground_truth = {**dict(variant.ground_truth or {}), "mutation_version": "changed"}
        session.commit()

        second, _capturing, _failures = _run_replay(
            session,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variant=variant,
            clean_passed=set(),
            timeout_seconds=120,
        )

        assert second.status == "completed"
        assert second.cache_status == "stale"
        assert "bug_variant_changed" in second.executor_metadata["cache_invalidation_reasons"]


def test_variant_replay_scheduler_records_parallel_summary(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    monkeypatch.setenv("TRACE_REPLAY_CONCURRENCY", "2")

    def fast_replay_job(job):
        with experiment_module.get_session_factory()() as worker_session:
            replay = ReplayModel(
                id=f"replay-{job.variant_id}",
                experiment_clean_run_id=job.clean_run_id,
                generated_test_set_artifact_id=job.artifact_id,
                target_snapshot_id=job.target_snapshot_id,
                bug_variant_id=job.variant_id,
                status="completed",
                pytest_summary={
                    "collected": 1,
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "collection_errors": 0,
                    "duration_ms": 1,
                    "case_results": [],
                },
                runtime_snapshot={"executor": "local_subprocess", "timeout_seconds": 120},
                executor_metadata={},
                workspace_manifest={},
                cache_status="miss",
                replay_mode="frozen_test_set",
                llm_calls=0,
            )
            worker_session.add(replay)
            worker_session.commit()
        return experiment_module.VariantReplayJobOutput(
            replay_id=f"replay-{job.variant_id}",
            bug_variant_id=job.variant_id,
            captured_bug=False,
            replay_metrics={
                "capture_rule": "clean_passed_variant_assertion_failure_same_nodeid",
                "clean_passed_nodeids": sorted(job.clean_passed),
                "variant_assertion_failure_nodeids": [],
                "capturing_nodeids": [],
                "probe_check": {"status": "passed"},
            },
        )

    monkeypatch.setattr(experiment_module, "_run_variant_replay_job", fast_replay_job)

    with Session(get_engine()) as session:
        experiment = Experiment(
            id="exp-parallel-summary",
            name="parallel",
            dataset_id=DEMO_DATASET_ID,
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
            status="running",
        )
        session.add(experiment)
        snapshot = session.get(ProjectSnapshot, DEMO_SNAPSHOT_ID)
        assert snapshot is not None
        plan = TestPlan(
            id="unused-plan-parallel",
            project_id=snapshot.project_id,
            name="unused",
            target_scope=[],
            goal="unused",
            budget={},
            output_options={},
            default_strategy_version_id="sv-direct-v1",
        )
        run = TestRun(
            id=new_id(),
            test_plan_id=plan.id,
            project_snapshot_id=snapshot.id,
            strategy_version_id="sv-direct-v1",
            runtime_snapshot={
                "executor": "local_subprocess",
                "test_command": "python -m pytest tests -q",
                "network_policy": "default",
                "timeout_seconds": 120,
                "resource_limits": {"timeout_seconds": 120},
                "env_keys": [],
            },
            strategy_snapshot={},
            status="completed",
            pytest_summary={},
        )
        session.add_all([plan, run])
        session.commit()
        clean_id = new_id()
        final_set = _empty_final_test_set()
        final_set.files = {"tests/generated/test_cache.py": "def test_cache_replay():\n    assert True\n"}
        artifact = _store_generated_test_set_artifact(
            session,
            run_id=run.id,
            experiment_id=experiment.id,
            clean_run_id=clean_id,
            final_set=final_set,
        )
        clean = ExperimentCleanRun(
            id=clean_id,
            experiment_id=experiment.id,
            eval_task_id="task-demo-shop-pricing-v2",
            strategy_version_id="sv-direct-v1",
            repeat_index=0,
            clean_run_id=run.id,
            generated_test_set_artifact_id=artifact.id,
            false_positive=False,
            clean_metrics={"validity_status": "evaluable"},
        )
        session.add(clean)
        session.commit()
        variants = experiment_module._task_bugs_and_variants(session, "task-demo-shop-pricing-v2")[:2]

        experiment_module._run_variant_replays_for_clean(
            session,
            experiment_id=experiment.id,
            clean_run=clean,
            artifact=artifact,
            target_snapshot=snapshot,
            variants=variants,
            clean_replay_id="clean-replay",
            clean_replay_passed={"tests/generated/test_cache.py::test_cache_replay"},
            timeout_seconds=120,
            concurrency=2,
        )
        clean.clean_metrics = {
            **dict(clean.clean_metrics or {}),
            "replay_scheduler": {"variant_concurrency": 2, "variant_job_count": len(variants)},
        }
        session.commit()
        metrics = get_experiment_metrics(session, experiment.id)
        assert metrics["runtime_execution"]["replay_concurrency"]["mode"] == "parallel"
        assert metrics["runtime_execution"]["replay_concurrency"]["configured"] == 2
        assert session.scalar(select(func.count()).select_from(ExperimentReplayRun)) == 2


def test_clean_setup_failure_marks_invalid_test_set(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    def fail_setup(*args, **kwargs):
        return {"status": "failed", "exit_code": 7, "artifact_id": "setup-log"}

    monkeypatch.setattr(experiment_module, "_run_setup_stage", fail_setup)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-setup-fail",
            name="setup fail",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-setup-fail")
        assert result["status"] == "completed"
        [clean] = list(session.scalars(select(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-setup-fail")))
        assert clean.clean_metrics["invalid_reason"] == "setup_failed"
        assert session.scalar(select(func.count()).select_from(ReplayModel)) == 0
        metrics = get_experiment_metrics(session, "exp-setup-fail")
        assert any(event["event_type"] == "setup_failed" for event in metrics["evaluation_events"])


def test_experiment_marks_flaky_clean_replay_invalid_and_skips_variants(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    def unstable_flaky_gate(
        session,
        *,
        clean_run,
        artifact,
        target_snapshot,
        baseline_replay,
        baseline_statuses,
        timeout_seconds,
        required_runs=3,
    ):
        assert timeout_seconds == 120
        return {
            "enabled": True,
            "required_runs": required_runs,
            "actual_runs": required_runs,
            "stable": False,
            "baseline_replay_id": baseline_replay.id,
            "replay_ids": [baseline_replay.id, "flaky-run-2", "flaky-run-3"],
            "reasons": ["run 2 mismatch: status changed: tests/generated/test_generated.py::test_case passed->failed"],
            "runs": [
                {
                    "replay_id": baseline_replay.id,
                    "summary": {"collected": 1, "passed": 1, "failed": 0, "skipped": 0, "collection_errors": 0},
                    "nodeid_statuses": baseline_statuses,
                }
            ],
        }

    monkeypatch.setattr(experiment_module, "_run_clean_flaky_gate", unstable_flaky_gate)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-flaky-clean",
            name="flaky clean",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-plan-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-flaky-clean")
        assert result["status"] == "completed"

        [clean] = list(
            session.scalars(select(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-flaky-clean"))
        )
        assert clean.false_positive is False
        assert clean.clean_metrics["validity_status"] == "invalid_test_set"
        assert clean.clean_metrics["invalid_reason"] == "flaky_clean_replay"
        assert clean.clean_metrics["flaky_check"]["stable"] is False
        assert "status changed" in clean.clean_metrics["flaky_check"]["reasons"][0]
        metrics = get_experiment_metrics(session, "exp-flaky-clean")
        assert any(event["event_type"] == "flaky_clean_replay" for event in metrics["evaluation_events"])

        replays = list(
            session.scalars(
                select(ReplayModel)
                .where(ReplayModel.experiment_clean_run_id == clean.id)
                .order_by(ReplayModel.created_at.asc())
            )
        )
        assert len(replays) == 1
        assert replays[0].bug_variant_id is None
        assert session.scalar(select(func.count()).select_from(ExperimentReplayRun)) == 0

        [row] = metrics["rows"]
        assert row["metric_status"] == "invalid_test_set"
        assert row["invalid_test_set_count"] == 1
        assert row["repeats"] == 0
        assert row["captured_per_repeat"] == []
        assert metrics["capture_matrix_counts"]["variant-wrong-status"]["plan_execute"]["repeat_count"] == 0


def test_experiment_backfeeds_previous_evaluation_events_to_later_repeats(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    original_execute_run_sync = experiment_module.execute_run_sync
    seen_event_types: list[list[str]] = []

    def capture_execute_run_sync(
        session,
        *,
        run_id,
        budget_override=None,
        make_llm=None,
        evaluation_events=None,
    ):
        seen_event_types.append([event.event_type for event in evaluation_events or []])
        return original_execute_run_sync(
            session,
            run_id=run_id,
            budget_override=budget_override,
            make_llm=make_llm,
            evaluation_events=evaluation_events,
        )

    def unstable_flaky_gate(
        session,
        *,
        clean_run,
        artifact,
        target_snapshot,
        baseline_replay,
        baseline_statuses,
        timeout_seconds,
        required_runs=3,
    ):
        return {
            "enabled": True,
            "required_runs": required_runs,
            "actual_runs": required_runs,
            "stable": False,
            "baseline_replay_id": baseline_replay.id,
            "replay_ids": [baseline_replay.id, f"flaky-run-{clean_run.repeat_index}"],
            "reasons": [f"repeat {clean_run.repeat_index} clean replay changed status"],
            "runs": [
                {
                    "replay_id": baseline_replay.id,
                    "summary": {"collected": 1, "passed": 1, "failed": 0, "skipped": 0, "collection_errors": 0},
                    "nodeid_statuses": baseline_statuses,
                }
            ],
        }

    monkeypatch.setattr(experiment_module, "execute_run_sync", capture_execute_run_sync)
    monkeypatch.setattr(experiment_module, "_run_clean_flaky_gate", unstable_flaky_gate)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-event-backfeed",
            name="event backfeed",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-plan-v1"],
            repeat_count=2,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-event-backfeed")
        assert result["status"] == "completed"
        metrics = get_experiment_metrics(session, "exp-event-backfeed")

    assert len(seen_event_types) == 2
    assert seen_event_types[0] == []
    assert seen_event_types[1] == ["flaky_clean_replay"]
    second_repeat_audit = [
        audit for audit in metrics["reflection_event_backfeed"] if audit["repeat_index"] == 1
    ]
    assert len(second_repeat_audit) == 1
    [decision] = second_repeat_audit[0]["decisions"]
    assert decision["event_type"] == "flaky_clean_replay"
    assert decision["action"] == "included"
    assert decision["reason"] == "reflection-consumable failure event"
    assert second_repeat_audit[0]["included_event_ids"] == [decision["event_id"]]


def test_experiment_blocks_seeded_bug_run_when_target_source_context_is_missing(clean_db):
    _seed_v2_demo()

    with Session(get_engine()) as session:
        dataset = create_eval_dataset(
            session,
            dataset_id="dataset-missing-context",
            name="missing-context",
            version="v1",
            project_snapshot_ids=[DEMO_SNAPSHOT_ID],
        )
        task = create_eval_task(
            session,
            dataset_id=dataset.id,
            task_id="task-missing-context",
            project_snapshot_id=DEMO_SNAPSHOT_ID,
            target_scope={"targets": ["does_not_exist"], "source": "test"},
            goal="Generate tests for a target that cannot be mapped.",
            expected_capabilities=["context_gate"],
        )
        seeded = create_seeded_bug(
            session,
            eval_task_id=task.id,
            bug_id="bug-missing-context",
            bug_type=BUGS[0].bug_type,
            description=BUGS[0].description,
            expected_detection=BUGS[0].expected_detection,
        )
        create_bug_variant(
            session,
            seeded_bug_id=seeded.id,
            variant_id="variant-missing-context",
            variant_name="valid patch but missing target scope",
            patch={"file": BUGS[0].file, "old": BUGS[0].old, "new": BUGS[0].new},
            ground_truth={"target": BUGS[0].target, "target_kind": BUGS[0].kind},
        )
        import app.services.experiments as experiment_module

        experiment_module.create_experiment(
            session,
            experiment_id="exp-missing-context",
            name="missing context",
            dataset_id=dataset.id,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-missing-context")
        assert result["status"] == "completed"

        [clean] = list(
            session.scalars(
                select(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-missing-context")
            )
        )
        run = session.get(TestRun, clean.clean_run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "context_incomplete_blocking"
        assert clean.false_positive is False
        assert clean.clean_metrics["validity_status"] == "invalid_test_set"
        assert clean.clean_metrics["invalid_reason"] == "context_incomplete_blocking"
        assert clean.clean_metrics["context_completeness"]["missing_targets"] == ["does_not_exist"]
        assert session.scalar(select(func.count()).select_from(ReplayModel)) == 0
        assert session.scalar(select(func.count()).select_from(ExperimentReplayRun)) == 0

        metrics = get_experiment_metrics(session, "exp-missing-context")
        assert any(event["event_type"] == "context_incomplete_blocking" for event in metrics["evaluation_events"])
        [row] = metrics["rows"]
        assert row["repeats"] == 0
        assert row["captured_per_repeat"] == []
        assert row["metric_status"] == "invalid_test_set"
        assert row["invalid_test_set_count"] == 1
        assert metrics["capture_matrix"]["variant-missing-context"]["direct"] is False


def test_experiment_marks_pipeline_reject_as_invalid_test_set(monkeypatch, clean_db):
    _seed_v2_demo()

    import app.services.experiments as experiment_module

    def reject_generation(session, *, run_id, budget_override=None, make_llm=None, evaluation_events=None):
        run = session.get(TestRun, run_id)
        run.status = "failed"
        run.error_code = "PIPELINE_REJECT"
        run.error_message = "生成测试出现空洞断言"
        run.total_tokens = 123
        run.tool_call_count = 2
        session.commit()
        return run

    monkeypatch.setattr(experiment_module, "execute_run_sync", reject_generation)

    with Session(get_engine()) as session:
        experiment_module.create_experiment(
            session,
            experiment_id="exp-pipeline-reject",
            name="pipeline reject",
            dataset_id=DEMO_DATASET_ID,
            strategy_version_ids=["sv-direct-v1"],
            repeat_count=1,
            llm_override={"provider": "mock", "model": "mock-1"},
        )
        result = run_experiment(session, "exp-pipeline-reject")
        assert result["status"] == "completed"

        [clean] = list(
            session.scalars(select(ExperimentCleanRun).where(ExperimentCleanRun.experiment_id == "exp-pipeline-reject"))
        )
        assert clean.false_positive is False
        assert clean.clean_metrics["validity_status"] == "invalid_test_set"
        assert clean.clean_metrics["invalid_reason"] == "pipeline_reject"
        assert "空洞断言" in clean.clean_metrics["pipeline_reject_error"]
        assert clean.clean_metrics["total_tokens"] == 123
        assert session.scalar(select(func.count()).select_from(ReplayModel)) == 0
        assert session.scalar(select(func.count()).select_from(ExperimentReplayRun)) == 0

        metrics = get_experiment_metrics(session, "exp-pipeline-reject")
        [row] = metrics["rows"]
        assert row["metric_status"] == "invalid_test_set"
        assert row["invalid_test_set_count"] == 1


def test_eval_cli_service_path_reuses_experiment_service(clean_db):
    result = run_full_eval_via_service(repeats=1, llm_override={"provider": "mock", "model": "mock-1"})
    rows = {row["strategy_id"]: row for row in result["rows"]}

    assert rows["direct"]["captured_per_repeat"] == [5]
    assert rows["plan_execute"]["captured_per_repeat"] == [6]
    assert rows["react_reflection"]["captured_per_repeat"] == [6]
    assert "variant-wrong-status" in result["matrix_md"]
