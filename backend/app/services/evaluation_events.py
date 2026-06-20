from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from app.models import Experiment, ExperimentCleanRun, ExperimentReplayRun, TestReplay
from app.schemas.evaluation import EvaluationEventContract
from app.services.evaluation_event_policy import filter_reason_for_reflection_event


@dataclass(frozen=True)
class ReflectionEventBackfeed:
    events: list[EvaluationEventContract]
    decisions: list[dict]


def build_reflection_event_backfeed(
    events: Sequence[EvaluationEventContract | dict],
    *,
    eval_task_id: str | None = None,
    strategy_version_id: str | None = None,
    before_repeat_index: int | None = None,
    max_events: int = 8,
) -> ReflectionEventBackfeed:
    consumable: list[EvaluationEventContract] = []
    decisions: list[dict] = []
    for raw_event in events:
        event = EvaluationEventContract.model_validate(raw_event)
        reason = filter_reason_for_reflection_event(
            event,
            eval_task_id=eval_task_id,
            strategy_version_id=strategy_version_id,
            before_repeat_index=before_repeat_index,
        )
        if reason is None and len(consumable) >= max_events:
            reason = "reflection event backfeed truncated by max_events"
        action = "included" if reason is None else "filtered"
        decisions.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "action": action,
                "reason": "reflection-consumable failure event" if reason is None else reason,
            }
        )
        if reason is None:
            consumable.append(event)
    return ReflectionEventBackfeed(events=consumable, decisions=decisions)


def build_reflection_event_backfeed_audit(
    clean_runs: Sequence[ExperimentCleanRun],
    events: Sequence[EvaluationEventContract | dict],
    *,
    max_events: int = 8,
) -> list[dict]:
    validated_events = [EvaluationEventContract.model_validate(event) for event in events]
    audits = []
    for clean in clean_runs:
        candidate_events = [
            event
            for event in validated_events
            if event.repeat_index is None or event.repeat_index < clean.repeat_index
        ]
        backfeed = build_reflection_event_backfeed(
            candidate_events,
            eval_task_id=clean.eval_task_id,
            strategy_version_id=clean.strategy_version_id,
            before_repeat_index=clean.repeat_index,
            max_events=max_events,
        )
        audits.append(
            {
                "clean_run_id": clean.id,
                "eval_task_id": clean.eval_task_id,
                "strategy_version_id": clean.strategy_version_id,
                "repeat_index": clean.repeat_index,
                "max_events": max_events,
                "included_event_ids": [event.event_id for event in backfeed.events],
                "decisions": backfeed.decisions,
                "created_from": "evaluation_event_projection",
            }
        )
    return audits


def _event_payload(**kwargs) -> dict:
    source_ids = kwargs.get("source_ids")
    if isinstance(source_ids, dict):
        kwargs["source_ids"] = {
            str(key): str(value)
            for key, value in source_ids.items()
            if str(key).strip() and str(value).strip()
        }
    if not kwargs.get("event_id"):
        identity = {
            "event_type": kwargs.get("event_type"),
            "scope": kwargs.get("scope"),
            "experiment_id": kwargs.get("experiment_id"),
            "clean_run_id": kwargs.get("clean_run_id"),
            "replay_id": kwargs.get("replay_id"),
            "bug_variant_id": kwargs.get("bug_variant_id"),
            "stable_code": kwargs.get("stable_code"),
            "source_ids": kwargs.get("source_ids") or {},
            "artifact_ids": kwargs.get("artifact_ids") or [],
            "nodeids": kwargs.get("nodeids") or [],
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        kwargs["event_id"] = f"evt-{digest}"
    return EvaluationEventContract(**kwargs).model_dump(exclude_none=True)


def _clean_run_events(clean: ExperimentCleanRun) -> list[dict]:
    metrics = clean.clean_metrics or {}
    if metrics.get("validity_status") != "invalid_test_set":
        return []

    invalid_reason = str(metrics.get("invalid_reason") or "invalid_test_set")
    event_type = "invalid_test_set"
    severity = "blocking"
    source_ids = {"test_run_id": clean.clean_run_id}
    payload: dict = {}
    nodeids: list[str] = []
    reason = invalid_reason

    if invalid_reason == "context_incomplete_blocking":
        event_type = "context_incomplete_blocking"
        context = metrics.get("context_completeness") if isinstance(metrics.get("context_completeness"), dict) else {}
        payload = {
            "missing_targets": list(context.get("missing_targets") or []),
            "risk_notes": list(context.get("risk_notes") or []),
        }
        reason = "target source context is incomplete"
    elif invalid_reason == "flaky_clean_replay":
        event_type = "flaky_clean_replay"
        flaky = metrics.get("flaky_check") if isinstance(metrics.get("flaky_check"), dict) else {}
        source_ids.update(
            {
                "baseline_replay_id": str(flaky.get("baseline_replay_id") or metrics.get("clean_replay_id") or ""),
            }
        )
        source_ids = {key: value for key, value in source_ids.items() if value}
        payload = {
            "required_runs": flaky.get("required_runs"),
            "actual_runs": flaky.get("actual_runs"),
            "replay_ids": list(flaky.get("replay_ids") or []),
            "reasons": list(flaky.get("reasons") or []),
        }
        reason = "; ".join(payload["reasons"]) or "clean replay was unstable"
    elif invalid_reason == "pipeline_reject":
        payload = {"pipeline_reject_error": metrics.get("pipeline_reject_error")}
        reason = str(metrics.get("pipeline_reject_error") or "contract guard rejected generated tests")
    elif invalid_reason == "setup_failed":
        event_type = "setup_failed"
        payload = {"setup": metrics.get("setup") if isinstance(metrics.get("setup"), dict) else {}}
        reason = "setup command failed"
    elif invalid_reason == "clean_replay_collection_error":
        payload = {"clean_replay_id": metrics.get("clean_replay_id")}
        reason = "clean replay had collection errors"
    elif invalid_reason == "clean_replay_failed":
        payload = {
            "clean_replay_id": metrics.get("clean_replay_id"),
            "clean_replay_error": metrics.get("clean_replay_error"),
        }
        reason = str(metrics.get("clean_replay_error") or "clean replay failed")

    return [
        _event_payload(
            event_type=event_type,
            severity=severity,
            scope="clean_run",
            experiment_id=clean.experiment_id,
            clean_run_id=clean.id,
            eval_task_id=clean.eval_task_id,
            strategy_version_id=clean.strategy_version_id,
            repeat_index=clean.repeat_index,
            stable_code=invalid_reason,
            reason=reason,
            source_ids=source_ids,
            nodeids=nodeids,
            artifact_ids=[clean.generated_test_set_artifact_id],
            payload=payload,
        )
    ]


def _replay_failure_events(clean_by_id: dict[str, ExperimentCleanRun], replay: TestReplay) -> list[dict]:
    if replay.status == "completed" and not replay.error_code:
        return []
    clean = clean_by_id.get(replay.experiment_clean_run_id)
    if clean is None:
        return []

    message = replay.error_message or replay.error_code or "replay failed"
    events = [
        _event_payload(
            event_type="replay_failure",
            severity="error",
            scope="replay",
            experiment_id=clean.experiment_id,
            clean_run_id=clean.id,
            replay_id=replay.id,
            bug_variant_id=replay.bug_variant_id,
            eval_task_id=clean.eval_task_id,
            strategy_version_id=clean.strategy_version_id,
            repeat_index=clean.repeat_index,
            stable_code=str(replay.error_code or "replay_failure"),
            reason=str(message),
            source_ids={"test_replay_id": replay.id},
            artifact_ids=[replay.generated_test_set_artifact_id],
            payload={
                "target_snapshot_id": replay.target_snapshot_id,
                "pytest_error_code": replay.error_code,
            },
        )
    ]
    if replay.error_code == "EXECUTOR_UNAVAILABLE":
        events.append(
            _event_payload(
                event_type="executor_unavailable",
                severity="error",
                scope="replay",
                experiment_id=clean.experiment_id,
                clean_run_id=clean.id,
                replay_id=replay.id,
                bug_variant_id=replay.bug_variant_id,
                eval_task_id=clean.eval_task_id,
                strategy_version_id=clean.strategy_version_id,
                repeat_index=clean.repeat_index,
                stable_code="executor_unavailable",
                reason=str(message),
                source_ids={"test_replay_id": replay.id},
                artifact_ids=[replay.generated_test_set_artifact_id],
                payload={
                    "executor": (replay.runtime_snapshot or {}).get("executor"),
                    "runtime_profile_id": (replay.runtime_snapshot or {}).get("runtime_profile_id"),
                },
            )
        )
    if replay.error_code == "SETUP_FAILED":
        events.append(
            _event_payload(
                event_type="setup_failed",
                severity="error",
                scope="replay",
                experiment_id=clean.experiment_id,
                clean_run_id=clean.id,
                replay_id=replay.id,
                bug_variant_id=replay.bug_variant_id,
                eval_task_id=clean.eval_task_id,
                strategy_version_id=clean.strategy_version_id,
                repeat_index=clean.repeat_index,
                stable_code="setup_failed",
                reason=str(message),
                source_ids={"test_replay_id": replay.id},
                artifact_ids=[replay.generated_test_set_artifact_id],
                payload={"setup": (replay.workspace_manifest or {}).get("setup")},
            )
        )
    if replay.bug_variant_id and "probe" in str(message).lower():
        events.append(
            _event_payload(
                event_type="probe_check_failed",
                severity="blocking",
                scope="variant",
                experiment_id=clean.experiment_id,
                clean_run_id=clean.id,
                replay_id=replay.id,
                bug_variant_id=replay.bug_variant_id,
                eval_task_id=clean.eval_task_id,
                strategy_version_id=clean.strategy_version_id,
                repeat_index=clean.repeat_index,
                stable_code="probe_check_failed",
                reason=str(message),
                source_ids={"test_replay_id": replay.id},
                artifact_ids=[replay.generated_test_set_artifact_id],
                payload={"pytest_error_code": replay.error_code},
            )
        )
    return events


def _replay_cache_events(clean_by_id: dict[str, ExperimentCleanRun], replay: TestReplay) -> list[dict]:
    clean = clean_by_id.get(replay.experiment_clean_run_id)
    if clean is None:
        return []
    if replay.cache_status not in {"hit", "stale"}:
        return []
    event_type = "replay_cache_hit" if replay.cache_status == "hit" else "replay_cache_stale"
    return [
        _event_payload(
            event_type=event_type,
            severity="info",
            scope="replay",
            experiment_id=clean.experiment_id,
            clean_run_id=clean.id,
            replay_id=replay.id,
            bug_variant_id=replay.bug_variant_id,
            eval_task_id=clean.eval_task_id,
            strategy_version_id=clean.strategy_version_id,
            repeat_index=clean.repeat_index,
            stable_code=event_type,
            reason="replay cache hit" if replay.cache_status == "hit" else "replay cache was stale",
            source_ids={"test_replay_id": replay.id},
            artifact_ids=[replay.generated_test_set_artifact_id],
            payload={"cache_key": replay.cache_key, "source_replay_id": replay.source_replay_id},
        )
    ]


def _experiment_replay_events(clean_by_id: dict[str, ExperimentCleanRun], replay_result: ExperimentReplayRun) -> list[dict]:
    clean = clean_by_id.get(replay_result.experiment_clean_run_id)
    if clean is None:
        return []
    metrics = replay_result.replay_metrics or {}
    probe = metrics.get("probe_check") if isinstance(metrics.get("probe_check"), dict) else None
    events = []
    if probe is not None:
        status = str(probe.get("status") or "")
        if status == "passed":
            events.append(
                _event_payload(
                    event_type="probe_check_passed",
                    severity="info",
                    scope="variant",
                    experiment_id=clean.experiment_id,
                    clean_run_id=clean.id,
                    replay_id=replay_result.replay_id,
                    bug_variant_id=replay_result.bug_variant_id,
                    eval_task_id=clean.eval_task_id,
                    strategy_version_id=clean.strategy_version_id,
                    repeat_index=clean.repeat_index,
                    stable_code="probe_check_passed",
                    reason="variant probe matched clean and buggy ground truth",
                    source_ids={"experiment_replay_run_id": replay_result.id},
                    payload={
                        "clean_actual": probe.get("clean_actual"),
                        "clean_expected": probe.get("clean_expected"),
                        "buggy_actual": probe.get("buggy_actual"),
                        "buggy_expected": probe.get("buggy_expected"),
                    },
                )
            )

    capturing_nodeids = list(metrics.get("capturing_nodeids") or [])
    event_type = "replay_captured" if replay_result.captured_bug else "replay_uncaptured"
    events.append(
        _event_payload(
            event_type=event_type,
            severity="info",
            scope="replay",
            experiment_id=clean.experiment_id,
            clean_run_id=clean.id,
            replay_id=replay_result.replay_id,
            bug_variant_id=replay_result.bug_variant_id,
            eval_task_id=clean.eval_task_id,
            strategy_version_id=clean.strategy_version_id,
            repeat_index=clean.repeat_index,
            stable_code=event_type,
            reason="variant replay captured bug" if replay_result.captured_bug else "variant replay did not capture bug",
            source_ids={
                "experiment_replay_run_id": replay_result.id,
                "clean_replay_id": str(metrics.get("clean_replay_id") or ""),
            },
            nodeids=capturing_nodeids,
            payload={
                "capture_rule": metrics.get("capture_rule"),
                "clean_passed_count": len(metrics.get("clean_passed_nodeids") or []),
                "variant_assertion_failure_count": len(metrics.get("variant_assertion_failure_nodeids") or []),
                "capturing_count": len(capturing_nodeids),
            },
        )
    )
    return events


def _experiment_failure_events(experiment: Experiment) -> list[dict]:
    if experiment.status != "failed":
        return []
    message = experiment.error_message or experiment.error_code or "experiment failed"
    lowered = str(message).lower()
    if "generated test set artifact hash mismatch" in lowered:
        event_type = "artifact_hash_mismatch"
        scope = "artifact"
        stable_code = "artifact_hash_mismatch"
    elif "llm_provider_error" in lowered or "provider" in lowered:
        event_type = "provider_failure"
        scope = "experiment"
        stable_code = "provider_failure"
    else:
        return []
    return [
        _event_payload(
            event_type=event_type,
            severity="error",
            scope=scope,
            experiment_id=experiment.id,
            stable_code=stable_code,
            reason=str(message),
            source_ids={"experiment_id": experiment.id},
            payload={"experiment_error_code": experiment.error_code},
        )
    ]


def project_evaluation_events(
    experiment: Experiment,
    clean_runs: list[ExperimentCleanRun],
    replays: list[TestReplay],
    replay_results: list[ExperimentReplayRun],
) -> list[dict]:
    events = []
    clean_by_id = {clean.id: clean for clean in clean_runs}
    for clean in clean_runs:
        events.extend(_clean_run_events(clean))
    for replay in replays:
        events.extend(_replay_failure_events(clean_by_id, replay))
        events.extend(_replay_cache_events(clean_by_id, replay))
    for replay_result in replay_results:
        events.extend(_experiment_replay_events(clean_by_id, replay_result))
    events.extend(_experiment_failure_events(experiment))
    return events
