from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from app.schemas.evaluation import (
    EvaluationEventContract,
    SourceContextRetrievalTrace,
    SourceContextSnippet,
)
from app.schemas.tools import FailureDetail
from app.services.evaluation_event_policy import prompt_safe_event_type_reason
from app.services.source_context_trace import (
    make_source_context_trace as _trace,
    safe_source_path as _safe_source_path,
    source_context_trace_id as _trace_id,
)
from app.tools.base import ToolContext


def append_failure_context(
    ctx: ToolContext,
    failure_details: Sequence[FailureDetail],
    snippets: list[SourceContextSnippet],
    pieces: list[str],
    total_bytes: int,
    *,
    max_total_bytes: int,
    max_failure_contexts: int,
    max_failure_context_chars: int,
    risk_notes: list[str],
    retrieval_trace: list[SourceContextRetrievalTrace],
) -> int:
    details = list(failure_details)
    if len(details) > max_failure_contexts:
        risk_notes.append("failure context truncated by max_failure_contexts")
        for detail in details[max_failure_contexts:]:
            target = _failure_target(detail)
            source_path = _failure_source_path(ctx, detail)
            line = _failure_line(detail)
            retrieval_trace.append(
                _trace(
                    target=target,
                    source_kind="failure_context",
                    retrieval_source="pytest_result",
                    status="truncated",
                    source_path=source_path,
                    start_line=line,
                    end_line=line,
                    confidence=0.7,
                    risk_notes=["failure context truncated by max_failure_contexts"],
                )
            )

    for detail in details[:max_failure_contexts]:
        target = _failure_target(detail)
        source_path = _failure_source_path(ctx, detail)
        line = _failure_line(detail)
        if source_path is None:
            risk_notes.append(f"failure context missing project-relative path: {target}")
            retrieval_trace.append(
                _trace(
                    target=target,
                    source_kind="failure_context",
                    retrieval_source="pytest_result",
                    status="missing",
                    confidence=0.0,
                    risk_notes=["failure context lacks project-relative path"],
                )
            )
            continue

        segment = _failure_context_segment(detail, source_path, line, max_failure_context_chars)
        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("failure context truncated by max_total_bytes")
            retrieval_trace.append(
                _trace(
                    target=target,
                    source_kind="failure_context",
                    retrieval_source="pytest_result",
                    status="truncated",
                    source_path=source_path,
                    start_line=line,
                    end_line=line,
                    confidence=0.7,
                    risk_notes=["failure context truncated by max_total_bytes"],
                )
            )
            break

        total_bytes += segment_bytes
        content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
        trace_id = _trace_id(target, "failure_context", "pytest_result", source_path, line, content_hash)
        snippets.append(
            SourceContextSnippet(
                path=source_path,
                source_path=source_path,
                target_ref=target,
                target=target,
                symbol=None,
                source_kind="failure_context",
                retrieval_source="pytest_result",
                confidence=0.9,
                start_line=line,
                end_line=line,
                content_hash=content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=target,
                source_kind="failure_context",
                retrieval_source="pytest_result",
                status="resolved",
                source_path=source_path,
                start_line=line,
                end_line=line,
                confidence=0.9,
                content_hash=content_hash,
            )
        )
        header = f"## {source_path}:{line}-{line} (failure_context:{target})"
        pieces.append(f"{header}\n```text\n{segment}\n```")
    return total_bytes


def append_evaluation_event_context(
    ctx: ToolContext,
    evaluation_events: Sequence[EvaluationEventContract | dict],
    snippets: list[SourceContextSnippet],
    pieces: list[str],
    total_bytes: int,
    *,
    max_total_bytes: int,
    max_evaluation_events: int,
    max_evaluation_event_chars: int,
    risk_notes: list[str],
    retrieval_trace: list[SourceContextRetrievalTrace],
) -> int:
    events = []
    for raw_event in evaluation_events:
        event = EvaluationEventContract.model_validate(raw_event)
        filter_reason = prompt_safe_event_type_reason(event)
        if filter_reason is not None:
            risk_notes.append(f"evaluation event filtered from prompt: {event.event_id} ({filter_reason})")
            continue
        events.append(event)
    source_path = _safe_source_path(f"{ctx.relpath(ctx.test_write_dir)}/evaluation_events.txt")
    if source_path is None:
        source_path = "tests/generated/evaluation_events.txt"

    if len(events) > max_evaluation_events:
        risk_notes.append("evaluation event context truncated by max_evaluation_events")
        for event in events[max_evaluation_events:]:
            retrieval_trace.append(
                _trace(
                    target=event.event_id,
                    source_kind="failure_context",
                    retrieval_source="evaluation_event",
                    status="truncated",
                    source_path=source_path,
                    start_line=1,
                    end_line=1,
                    confidence=0.8,
                    risk_notes=["evaluation event context truncated by max_evaluation_events"],
                )
            )

    for event in events[:max_evaluation_events]:
        segment = _evaluation_event_segment(event, max_evaluation_event_chars)
        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("evaluation event context truncated by max_total_bytes")
            retrieval_trace.append(
                _trace(
                    target=event.event_id,
                    source_kind="failure_context",
                    retrieval_source="evaluation_event",
                    status="truncated",
                    source_path=source_path,
                    start_line=1,
                    end_line=1,
                    confidence=0.8,
                    risk_notes=["evaluation event context truncated by max_total_bytes"],
                )
            )
            break

        total_bytes += segment_bytes
        content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
        trace_id = _trace_id(event.event_id, "failure_context", "evaluation_event", source_path, 1, content_hash)
        snippets.append(
            SourceContextSnippet(
                path=source_path,
                source_path=source_path,
                target_ref=event.event_id,
                target=event.event_id,
                symbol=None,
                source_kind="failure_context",
                retrieval_source="evaluation_event",
                confidence=0.85,
                start_line=1,
                end_line=1,
                content_hash=content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=event.event_id,
                source_kind="failure_context",
                retrieval_source="evaluation_event",
                status="resolved",
                source_path=source_path,
                start_line=1,
                end_line=1,
                confidence=0.85,
                content_hash=content_hash,
            )
        )
        header = f"## {source_path}:1-1 (failure_context:{event.event_id})"
        pieces.append(f"{header}\n```text\n{segment}\n```")
    return total_bytes


def _failure_target(detail: FailureDetail) -> str:
    return str(detail.nodeid or detail.file or "<collect>")


def _evaluation_event_segment(event: EvaluationEventContract, max_chars: int) -> str:
    rows = [
        f"event_id: {event.event_id}",
        f"event_type: {event.event_type}",
        f"severity: {event.severity}",
        f"scope: {event.scope}",
        f"stable_code: {event.stable_code}",
        f"reason: {_clip_failure_text(event.reason, 400)}",
    ]
    for key, value in sorted(event.source_ids.items()):
        rows.append(f"source_id.{key}: {value}")
    for artifact_id in event.artifact_ids:
        rows.append(f"artifact_id: {artifact_id}")
    for nodeid in event.nodeids:
        rows.append(f"nodeid: {nodeid}")
    for key, value in sorted(event.payload.items()):
        rows.append(f"payload.{key}: {_clip_failure_text(str(value), 400)}")
    return _clip_failure_text("\n".join(rows), max_chars)


def _failure_source_path(ctx: ToolContext, detail: FailureDetail) -> str | None:
    candidates = [detail.file, _nodeid_path(detail.nodeid)]
    for candidate in candidates:
        safe = _safe_source_path(candidate)
        if safe:
            return safe
        if candidate:
            absolute = Path(candidate)
            if absolute.is_absolute():
                try:
                    relative = absolute.resolve().relative_to(ctx.root.resolve())
                except ValueError:
                    continue
                safe = _safe_source_path(relative.as_posix())
                if safe:
                    return safe
    return None


def _nodeid_path(nodeid: str | None) -> str | None:
    if not nodeid or nodeid.startswith("<"):
        return None
    return nodeid.split("::", 1)[0]


def _failure_line(detail: FailureDetail) -> int:
    return detail.line if detail.line and detail.line > 0 else 1


def _failure_context_segment(
    detail: FailureDetail,
    source_path: str,
    line: int,
    max_chars: int,
) -> str:
    rows = [
        f"nodeid: {_failure_target(detail)}",
        f"location: {source_path}:{line}",
    ]
    if detail.failure_type:
        rows.append(f"failure_type: {detail.failure_type}")
    if detail.exc_type:
        rows.append(f"exc_type: {detail.exc_type}")
    if detail.message:
        rows.append(f"message: {_clip_failure_text(detail.message, 400)}")
    if detail.traceback:
        rows.append("traceback:")
        rows.append(_indent_failure_text(_clip_failure_text(detail.traceback, max_chars), "  "))
    return "\n".join(rows)


def _clip_failure_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def _indent_failure_text(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())