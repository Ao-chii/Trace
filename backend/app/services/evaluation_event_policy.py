from __future__ import annotations

from app.schemas.evaluation import EvaluationEventContract


REFLECTION_CONSUMABLE_EVENT_TYPES = frozenset(
    {
        "flaky_clean_replay",
        "probe_check_failed",
        "replay_failure",
    }
)


def filter_reason_for_reflection_event(
    event: EvaluationEventContract,
    *,
    eval_task_id: str | None,
    strategy_version_id: str | None,
    before_repeat_index: int | None,
) -> str | None:
    if event.event_type in {"provider_failure", "artifact_hash_mismatch"}:
        return "event is audit-only and must not enter LLM prompt"
    if eval_task_id is not None and event.eval_task_id != eval_task_id:
        return "event belongs to another eval_task_id"
    if strategy_version_id is not None and event.strategy_version_id != strategy_version_id:
        return "event belongs to another strategy_version_id"
    if (
        before_repeat_index is not None
        and event.repeat_index is not None
        and event.repeat_index >= before_repeat_index
    ):
        return "event belongs to current or future repeat"
    if event.event_type not in REFLECTION_CONSUMABLE_EVENT_TYPES:
        return "event type is not reflection-consumable"
    return None


def prompt_safe_event_type_reason(event: EvaluationEventContract) -> str | None:
    if event.event_type in REFLECTION_CONSUMABLE_EVENT_TYPES:
        return None
    return filter_reason_for_reflection_event(
        event,
        eval_task_id=None,
        strategy_version_id=None,
        before_repeat_index=None,
    )
