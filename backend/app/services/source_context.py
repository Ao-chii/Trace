from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.schemas.evaluation import (
    ContextCompletenessEvidence,
    EvaluationEventContract,
    SourceContextRetrievalTrace,
    SourceContextSnippet,
)
from app.schemas.tools import (
    AnalyzeProjectOutput,
    AstGrepMatch,
    AstGrepSearchInput,
    FailureDetail,
    LspDefinitionInput,
    LspDefinitionMatch,
    LspReferencesInput,
    ReadFileInput,
    RgSearchInput,
    RgSearchMatch,
)
from app.services.source_context_failures import (
    append_evaluation_event_context,
    append_failure_context,
)
from app.services.source_context_dependencies import dependency_slices
from app.services.source_context_trace import (
    make_source_context_trace as _trace,
    safe_source_path as _safe_source_path,
    source_context_trace_id as _trace_id,
)
from app.services.source_context_routes import (
    looks_like_route_target as _looks_like_route_target,
    route_handler_for_path as _route_handler_for_path,
    route_path_without_method as _route_path_without_method,
    route_target_parts as _route_target_parts,
    route_with_method as _route_with_method,
)
from app.services.source_context_targets import (
    ast_grep_query_for_target as _ast_grep_query_for_target,
    lsp_query_for_target as _lsp_query_for_target,
    rg_query_for_target as _rg_query_for_target,
    symbol_query_for_target as _symbol_query_for_target,
)
from app.services.source_context_support import (
    dedupe_support_targets as _dedupe_support_targets,
    is_generated_test_path as _is_generated_test_path,
    support_context_incomplete as _support_context_incomplete,
    support_dedup_note as _support_dedup_note,
    support_targets as _support_targets,
)
from app.tools.base import ToolContext
from app.tools.fs_tools import read_file
from app.tools.lsp import lsp_definition, lsp_references
from app.tools.search import ast_grep_search, rg_search


_SOURCE_KIND_PRIORITY = {
    "target_source": 0,
    "dependency": 1,
    "reference": 2,
    "model_schema": 3,
    "fixture": 4,
    "existing_test": 5,
    "failure_context": 6,
    "fallback_file": 7,
}

_RETRIEVAL_SOURCE_PRIORITY = {
    "direct_path": 0,
    "framework_scanner": 1,
    "analysis_ast": 2,
    "lsp": 3,
    "ast_grep": 4,
    "pytest_scanner": 5,
    "pytest_result": 6,
    "evaluation_event": 7,
    "rg": 8,
    "default_path": 9,
}


@dataclass(frozen=True)
class SourceContextBundle:
    snippets: list[SourceContextSnippet]
    context_completeness: ContextCompletenessEvidence
    source_context_text: str


@dataclass(frozen=True)
class _Target:
    # 一个解析后的读取目标：要么整文件，要么文件里的某个符号（函数/路由 handler/model）。
    raw: str
    rel_path: str
    symbol: str | None  # None = 整文件
    source_kind: str = "target_source"
    retrieval_source: str = "analysis_ast"
    confidence: float = 1.0


@dataclass(frozen=True)
class _AnalysisMatch:
    rel_path: str
    symbol: str
    source_kind: str
    retrieval_source: str
    confidence: float


def build_source_context_bundle(
    ctx: ToolContext,
    target_scope: list[str],
    analysis: AnalyzeProjectOutput | None = None,
    *,
    max_file_bytes: int = 24000,
    max_total_bytes: int = 48000,
    max_snippets: int = 12,
    max_support_snippets: int = 4,
    max_dependency_snippets: int = 3,
    max_dependency_depth: int = 2,
    max_reference_snippets: int = 2,
    failure_details: Sequence[FailureDetail] | None = None,
    evaluation_events: Sequence[EvaluationEventContract | dict] | None = None,
    max_failure_contexts: int = 4,
    max_failure_context_chars: int = 1600,
    max_evaluation_events: int = 4,
    max_evaluation_event_chars: int = 1600,
) -> SourceContextBundle:
    """按目标函数/路由读取源码片段。

    V2 §8.2：不再整库整文件盲读。target_scope 里的每一项先解析成「文件 + 可选符号」，
    命中符号就用 AST 切出该函数/路由 handler/Pydantic model 的定义片段；只给文件名才整文件读。
    解析不到的目标记进 missing_targets，并把 context 标成 incomplete，绝不假装上下文完整。
    路径白名单与大小上限沿用 read_file 的 root 子树校验 + 字节预算。
    """
    snippets: list[SourceContextSnippet] = []
    pieces: list[str] = []
    total_bytes = 0
    missing_targets: list[str] = []
    risk_notes: list[str] = []
    retrieval_trace: list[SourceContextRetrievalTrace] = []
    seen: set[tuple[str, str | None]] = set()
    resolved_target_sources: list[tuple[_Target, str, str]] = []

    targets, unresolved, pre_read_trace = _resolve_targets(ctx, target_scope, analysis)
    retrieval_trace.extend(pre_read_trace)
    # 连「目标 -> 文件」都映射不到的，先记进缺失，别让它们在记录里凭空消失。
    missing_targets.extend(unresolved)
    for raw in unresolved:
        retrieval_trace.append(
            _trace(
                target=raw,
                source_kind="target_source",
                retrieval_source="analysis_ast",
                status="missing",
                confidence=0.0,
                risk_notes=["target could not be resolved to project source"],
            )
        )
    if len(targets) > max_snippets:
        truncated = targets[max_snippets:]
        missing_targets.extend(target.raw for target in truncated)
        risk_notes.append("source context truncated by max_snippets")
        for target in truncated:
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="truncated",
                    source_path=_safe_source_path(target.rel_path),
                    symbol=target.symbol,
                    confidence=target.confidence,
                    risk_notes=["source context truncated by max_snippets"],
                )
            )
    for target in targets[:max_snippets]:
        key = (target.rel_path, target.symbol)
        if key in seen:
            continue
        seen.add(key)
        try:
            out = read_file(ctx, ReadFileInput(path=target.rel_path, max_bytes=max_file_bytes))
        except Exception:
            missing_targets.append(target.raw)
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="error",
                    source_path=_safe_source_path(target.rel_path),
                    symbol=target.symbol,
                    confidence=0.0,
                    risk_notes=["source file could not be read"],
                )
            )
            continue

        if target.symbol:
            sliced = _slice_symbol(out.content, target.symbol)
            if sliced is None:
                # 文件读到了，但定位不到目标符号：算缺失，不要退化成整文件冒充命中。
                missing_targets.append(target.raw)
                retrieval_trace.append(
                    _trace(
                        target=target.raw,
                        source_kind=target.source_kind,
                        retrieval_source=target.retrieval_source,
                        status="missing",
                        source_path=out.path,
                        symbol=target.symbol,
                        confidence=0.0,
                        risk_notes=["symbol could not be sliced from source file"],
                    )
                )
                continue
            start_line, end_line, segment = sliced
            target_ref = target.symbol
        else:
            start_line, end_line, segment = 1, max(1, out.content.count("\n") + 1), out.content
            target_ref = Path(target.rel_path).stem

        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("source context truncated by max_total_bytes")
            missing_targets.append(target.raw)
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="truncated",
                    source_path=out.path,
                    symbol=target.symbol,
                    start_line=start_line,
                    end_line=end_line,
                    confidence=target.confidence,
                    risk_notes=["source context truncated by max_total_bytes"],
                )
            )
            break
        total_bytes += segment_bytes
        content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
        trace_id = _trace_id(
            target.raw,
            target.source_kind,
            target.retrieval_source,
            out.path,
            target.symbol or "",
            start_line,
            end_line,
            content_hash,
        )
        snippets.append(
            SourceContextSnippet(
                path=out.path,
                source_path=out.path,
                target_ref=target_ref,
                target=target.raw,
                symbol=target.symbol or target_ref,
                source_kind=target.source_kind,
                retrieval_source=target.retrieval_source,
                confidence=target.confidence,
                start_line=start_line,
                end_line=end_line,
                content_hash=content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=target.raw,
                source_kind=target.source_kind,
                retrieval_source=target.retrieval_source,
                status="resolved",
                source_path=out.path,
                symbol=target.symbol or target_ref,
                start_line=start_line,
                end_line=end_line,
                confidence=target.confidence,
                content_hash=content_hash,
            )
        )
        header = f"## {out.path}:{start_line}-{end_line} ({target_ref})"
        pieces.append(f"{header}\n```python\n{segment}\n```")
        if target.source_kind == "target_source" and target.symbol:
            resolved_target_sources.append((target, out.path, out.content))

    for target, source_path, content in resolved_target_sources:
        if max_dependency_snippets > 0:
            total_bytes = _append_direct_dependencies(
                ctx,
                source_path,
                content,
                target,
                snippets,
                pieces,
                seen,
                total_bytes,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
                max_dependency_snippets=max_dependency_snippets,
                max_dependency_depth=max_dependency_depth,
                risk_notes=risk_notes,
                retrieval_trace=retrieval_trace,
            )
    for target, _source_path, _content in resolved_target_sources:
        if max_reference_snippets > 0:
            total_bytes = _append_lsp_references(
                ctx,
                target,
                snippets,
                pieces,
                seen,
                total_bytes,
                max_total_bytes=max_total_bytes,
                max_reference_snippets=max_reference_snippets,
                risk_notes=risk_notes,
                retrieval_trace=retrieval_trace,
            )

    primary_snippet_count = sum(
        1 for snippet in snippets if snippet.source_kind in {"target_source", "fallback_file"}
    )

    if analysis is not None and max_support_snippets > 0:
        total_bytes = _append_support_context(
            ctx,
            analysis,
            snippets,
            pieces,
            seen,
            total_bytes,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            max_support_snippets=max_support_snippets,
            risk_notes=risk_notes,
            retrieval_trace=retrieval_trace,
        )

    if failure_details:
        total_bytes = append_failure_context(
            ctx,
            failure_details,
            snippets,
            pieces,
            total_bytes,
            max_total_bytes=max_total_bytes,
            max_failure_contexts=max_failure_contexts,
            max_failure_context_chars=max_failure_context_chars,
            risk_notes=risk_notes,
            retrieval_trace=retrieval_trace,
        )
    if evaluation_events:
        total_bytes = append_evaluation_event_context(
            ctx,
            evaluation_events,
            snippets,
            pieces,
            total_bytes,
            max_total_bytes=max_total_bytes,
            max_evaluation_events=max_evaluation_events,
            max_evaluation_event_chars=max_evaluation_event_chars,
            risk_notes=risk_notes,
            retrieval_trace=retrieval_trace,
        )

    support_incomplete = _support_context_incomplete(retrieval_trace)
    risk_notes.extend(_lsp_fallback_risk_notes(retrieval_trace))
    if primary_snippet_count == 0:
        status = "incomplete"
        risk_notes.append("no source snippet could be read")
    elif missing_targets:
        status = "partial"
        risk_notes.append("some source targets were unavailable")
    elif support_incomplete:
        status = "partial"
        risk_notes.append("some support context was unavailable")
    else:
        status = "complete"

    context = ContextCompletenessEvidence(
        status=status,
        context_incomplete=status != "complete",
        snippets=snippets,
        retrieval_trace=retrieval_trace,
        missing_targets=missing_targets,
        risk_notes=risk_notes,
    )
    return SourceContextBundle(
        snippets=snippets,
        context_completeness=context,
        source_context_text="\n\n".join(pieces),
    )


def _resolve_targets(
    ctx: ToolContext, target_scope: list[str], analysis: AnalyzeProjectOutput | None
) -> tuple[list[_Target], list[str], list[SourceContextRetrievalTrace]]:
    if not target_scope:
        # 没给目标：退回少量默认入口文件整文件读，保持 demo 可跑。
        defaults = ["calc.py", "app.py", "main.py"]
        return [
            _Target(
                raw=name,
                rel_path=name,
                symbol=None,
                source_kind="fallback_file",
                retrieval_source="default_path",
                confidence=0.4,
            )
            for name in defaults
            if (ctx.root / name).exists()
        ], [], []

    targets: list[_Target] = []
    unresolved: list[str] = []
    retrieval_trace: list[SourceContextRetrievalTrace] = []
    for raw in target_scope:
        target = _resolve_one(ctx, str(raw), analysis)
        if target is not None:
            targets.append(target)
            continue

        candidates: list[_Target] = []
        fallback_trace: list[SourceContextRetrievalTrace] = []

        lsp_target, lsp_trace = _resolve_one_with_lsp(ctx, str(raw))
        if lsp_target is not None:
            candidates.append(lsp_target)
        else:
            fallback_trace.extend(lsp_trace)

        ast_target, ast_trace = _resolve_one_with_ast_grep(ctx, str(raw))
        if ast_target is not None:
            candidates.append(ast_target)
        else:
            fallback_trace.extend(ast_trace)

        rg_target, rg_trace = _resolve_one_with_rg(ctx, str(raw))
        if rg_target is not None:
            candidates.append(rg_target)
        else:
            fallback_trace.extend(rg_trace)

        if candidates:
            selected = _sort_targets(candidates)[0]
            targets.append(selected)
            selected_priority = _RETRIEVAL_SOURCE_PRIORITY.get(selected.retrieval_source, 99)
            retrieval_trace.extend(
                trace
                for trace in fallback_trace
                if _RETRIEVAL_SOURCE_PRIORITY.get(trace.retrieval_source, 99) < selected_priority
            )
        else:
            retrieval_trace.extend(fallback_trace)
            unresolved.append(str(raw))
    return targets, unresolved, retrieval_trace


def _resolve_one(ctx: ToolContext, raw: str, analysis: AnalyzeProjectOutput | None) -> _Target | None:
    norm = raw.replace("\\", "/").strip()
    if not norm:
        return None

    # 形如 path.py::symbol 或 path.py:symbol —— 显式文件 + 符号。
    for sep in ("::", ":"):
        if sep in norm and norm.split(sep, 1)[0].endswith(".py"):
            path_part, symbol = norm.split(sep, 1)
            return _Target(
                raw=raw,
                rel_path=path_part,
                symbol=symbol or None,
                source_kind="target_source",
                retrieval_source="direct_path",
                confidence=0.8,
            )

    # 先按符号 / 路由 path 查 analyze 结果。路由目标常写成 "/items" 或 "/api/users"，
    # 不能因为包含 "/" 就误判成文件路径。
    if analysis is not None:
        match = _analysis_match(analysis, norm)
        if match is not None:
            return _Target(
                raw=raw,
                rel_path=match.rel_path,
                symbol=match.symbol,
                source_kind=match.source_kind,
                retrieval_source=match.retrieval_source,
                confidence=match.confidence,
            )

    # 直接是一个文件路径：整文件。
    if _looks_like_route_target(norm):
        return None
    if norm.endswith(".py") or "/" in norm:
        return _Target(
            raw=raw,
            rel_path=norm,
            symbol=None,
            source_kind="target_source",
            retrieval_source="direct_path",
            confidence=0.7,
        )

    return None


def _resolve_one_with_lsp(ctx: ToolContext, raw: str) -> tuple[_Target | None, list[SourceContextRetrievalTrace]]:
    query = _lsp_query_for_target(raw)
    if not query:
        return None, []
    try:
        out = lsp_definition(ctx, LspDefinitionInput(query=query, path=".", glob="*.py", max_matches=8))
    except Exception as exc:
        return None, [
            _trace(
                target=raw,
                source_kind="target_source",
                retrieval_source="lsp",
                status="error",
                confidence=0.0,
                risk_notes=[f"lsp definition failed: {type(exc).__name__}"],
            )
        ]

    if out.definitions:
        match = _rank_lsp_matches(ctx, out.definitions)[0]
        if not match.symbol:
            return None, [_lsp_candidate_trace(raw, match, "lsp definition did not include a symbol")]
        return (
            _Target(
                raw=raw,
                rel_path=match.source_path,
                symbol=match.symbol,
                source_kind="target_source",
                retrieval_source="lsp",
                confidence=match.confidence,
            ),
            [],
        )

    status = "missing" if out.status == "missing" else "error"
    note = "; ".join(out.warnings) if out.warnings else f"lsp definition status: {out.status}"
    return None, [
        _trace(
            target=raw,
            source_kind="target_source",
            retrieval_source="lsp",
            status=status,
            confidence=0.0,
            risk_notes=[note],
        )
    ]


def _resolve_one_with_ast_grep(ctx: ToolContext, raw: str) -> tuple[_Target | None, list[SourceContextRetrievalTrace]]:
    query, kind, method = _ast_grep_query_for_target(raw)
    if not query:
        return None, []

    try:
        out = ast_grep_search(
            ctx,
            AstGrepSearchInput(query=query, kind=kind, method=method, path=".", glob="*.py", max_matches=8),
        )
    except Exception as exc:
        return None, [
            _trace(
                target=raw,
                source_kind="target_source",
                retrieval_source="ast_grep",
                status="error",
                confidence=0.0,
                risk_notes=[f"ast-grep fallback failed: {type(exc).__name__}"],
            )
        ]

    if not out.matches:
        return None, [
            _trace(
                target=raw,
                source_kind="target_source",
                retrieval_source="ast_grep",
                status="missing",
                confidence=0.0,
                risk_notes=["ast-grep found no structural match"],
            )
        ]

    match = _rank_ast_grep_matches(ctx, out.matches)[0]
    if not match.symbol:
        return None, [_ast_grep_candidate_trace(raw, match, "ast-grep match did not include a symbol")]
    return (
        _Target(
            raw=raw,
            rel_path=match.source_path,
            symbol=match.symbol,
            source_kind="target_source",
            retrieval_source="ast_grep",
            confidence=match.confidence,
        ),
        [],
    )


def _resolve_one_with_rg(ctx: ToolContext, raw: str) -> tuple[_Target | None, list[SourceContextRetrievalTrace]]:
    query = _rg_query_for_target(raw)
    if not query:
        return None, []

    try:
        out = rg_search(ctx, RgSearchInput(query=query, path=".", glob="*.py", max_matches=8))
    except Exception as exc:
        return None, [
            _trace(
                target=raw,
                source_kind="target_source",
                retrieval_source="rg",
                status="error",
                confidence=0.0,
                risk_notes=[f"rg fallback failed: {type(exc).__name__}"],
            )
        ]

    traces: list[SourceContextRetrievalTrace] = []
    route_method, route_path = _route_target_parts(raw)
    symbol = _symbol_query_for_target(raw)
    for match in _rank_rg_matches(ctx, out.matches):
        try:
            source = read_file(ctx, ReadFileInput(path=match.source_path, max_bytes=24000))
        except Exception:
            traces.append(_rg_candidate_trace(raw, match, "rg candidate file could not be read"))
            continue

        if route_path:
            handler = _route_handler_for_path(source.content, route_method, route_path)
            if handler is not None:
                return (
                    _Target(
                        raw=raw,
                        rel_path=match.source_path,
                        symbol=handler,
                        source_kind="target_source",
                        retrieval_source="rg",
                        confidence=_confirmed_rg_confidence(match),
                    ),
                    traces,
                )
        elif symbol and _slice_symbol(source.content, symbol) is not None:
            return (
                _Target(
                    raw=raw,
                    rel_path=match.source_path,
                    symbol=symbol,
                    source_kind="target_source",
                    retrieval_source="rg",
                    confidence=_confirmed_rg_confidence(match),
                ),
                traces,
            )

        traces.append(_rg_candidate_trace(raw, match, "rg lexical match was not accepted as target source"))

    return None, traces


def _analysis_match(analysis: AnalyzeProjectOutput, target: str) -> _AnalysisMatch | None:
    # 支持传 "module.func" 或裸 "func"；路由可传 handler 名、path 或 "METHOD path"。
    short = target.rsplit(".", 1)[-1]
    route_path = _route_path_without_method(target)
    route_with_method = _route_with_method(target)
    for route in analysis.routes:
        method_path = f"{route.method} {route.path}"
        if route.handler == short or route.path == route_path or method_path == route_with_method:
            return _AnalysisMatch(
                rel_path=route.file,
                symbol=route.handler,
                source_kind="target_source",
                retrieval_source="framework_scanner",
                confidence=1.0,
            )
    for fn in analysis.functions:
        if fn.name == short:
            return _AnalysisMatch(
                rel_path=fn.file,
                symbol=fn.name,
                source_kind="target_source",
                retrieval_source="analysis_ast",
                confidence=1.0,
            )
    for model in analysis.models:
        if model.name == short:
            return _AnalysisMatch(
                rel_path=model.file,
                symbol=model.name,
                source_kind="model_schema",
                retrieval_source="analysis_ast",
                confidence=1.0,
            )
    return None



def _rank_ast_grep_matches(ctx: ToolContext, matches: list[AstGrepMatch]) -> list[AstGrepMatch]:
    generated_tests_dir = ctx.relpath(ctx.test_write_dir)
    return sorted(
        matches,
        key=lambda match: (
            _is_generated_test_path(match.source_path, generated_tests_dir),
            _is_probable_test_path(match.source_path),
            match.source_path,
            match.line_range.get("start", 1),
        ),
    )


def _rank_lsp_matches(ctx: ToolContext, matches: list[LspDefinitionMatch]) -> list[LspDefinitionMatch]:
    generated_tests_dir = ctx.relpath(ctx.test_write_dir)
    return sorted(
        matches,
        key=lambda match: (
            _is_generated_test_path(match.source_path, generated_tests_dir),
            _is_probable_test_path(match.source_path),
            match.source_path,
            match.line_range.get("start", 1),
        ),
    )


def _lsp_candidate_trace(raw: str, match: LspDefinitionMatch, note: str) -> SourceContextRetrievalTrace:
    return _trace(
        trace_id=match.trace_id,
        target=raw,
        source_kind="target_source",
        retrieval_source="lsp",
        status="missing",
        source_path=match.source_path,
        symbol=match.symbol,
        start_line=match.line_range["start"],
        end_line=match.line_range["end"],
        confidence=match.confidence,
        content_hash=match.content_hash,
        risk_notes=[note],
    )


def _ast_grep_candidate_trace(raw: str, match: AstGrepMatch, note: str) -> SourceContextRetrievalTrace:
    return _trace(
        trace_id=match.trace_id,
        target=raw,
        source_kind="target_source",
        retrieval_source="ast_grep",
        status="missing",
        source_path=match.source_path,
        symbol=match.symbol,
        start_line=match.line_range["start"],
        end_line=match.line_range["end"],
        confidence=match.confidence,
        content_hash=match.content_hash,
        risk_notes=[note],
    )



def _rank_rg_matches(ctx: ToolContext, matches: list[RgSearchMatch]) -> list[RgSearchMatch]:
    generated_tests_dir = ctx.relpath(ctx.test_write_dir)
    return sorted(
        matches,
        key=lambda match: (
            _is_generated_test_path(match.source_path, generated_tests_dir),
            _is_probable_test_path(match.source_path),
            match.source_path,
            match.line_number,
        ),
    )


def _is_probable_test_path(path: str) -> bool:
    norm = path.replace("\\", "/")
    name = Path(norm).name
    return norm.startswith("tests/") or "/tests/" in norm or name.startswith("test_") or name.endswith("_test.py")


def _confirmed_rg_confidence(match: RgSearchMatch) -> float:
    return min(0.75, match.confidence + 0.2)


def _rg_candidate_trace(raw: str, match: RgSearchMatch, note: str) -> SourceContextRetrievalTrace:
    return _trace(
        trace_id=match.trace_id,
        target=raw,
        source_kind="target_source",
        retrieval_source="rg",
        status="missing",
        source_path=match.source_path,
        start_line=match.line_number,
        end_line=match.line_number,
        confidence=match.confidence,
        content_hash=match.content_hash,
        risk_notes=[note],
    )


def _slice_symbol(content: str, symbol: str) -> tuple[int, int, str] | None:
    # 用 AST 在文件里定位顶层（或类内）的 def/async def/class，按行切出定义片段。
    short = symbol.rsplit(".", 1)[-1]
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    lines = content.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == short:
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if not start or not end:
                return None
            # 把紧贴定义上方的装饰器也带上（路由 handler 的 @router.get 很关键）。
            decorators = getattr(node, "decorator_list", [])
            if decorators:
                start = min(start, min(d.lineno for d in decorators))
            segment = "\n".join(lines[start - 1 : end])
            return start, end, segment
    return None


def _append_direct_dependencies(
    ctx: ToolContext,
    source_path: str,
    content: str,
    target: _Target,
    snippets: list[SourceContextSnippet],
    pieces: list[str],
    seen: set[tuple[str, str | None]],
    total_bytes: int,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_dependency_snippets: int,
    max_dependency_depth: int,
    risk_notes: list[str],
    retrieval_trace: list[SourceContextRetrievalTrace],
) -> int:
    dependencies, misses = dependency_slices(
        ctx,
        source_path,
        content,
        target.symbol or "",
        max_file_bytes=max_file_bytes,
        max_dependency_depth=max_dependency_depth,
    )
    for miss in misses:
        risk_notes.append(miss.risk_note)
        retrieval_trace.append(
            _trace(
                target=f"dependency:{target.raw}->{miss.name}",
                source_kind="dependency",
                retrieval_source=miss.retrieval_source,
                status=miss.status,
                source_path=miss.source_path,
                symbol=miss.name,
                confidence=miss.confidence,
                risk_notes=[miss.risk_note],
            )
        )
    if not dependencies:
        return total_bytes
    if len(dependencies) > max_dependency_snippets:
        risk_notes.append("direct dependency context truncated by max_dependency_snippets")
        for dep in dependencies[max_dependency_snippets:]:
            retrieval_trace.append(
                _trace(
                    target=f"dependency:{target.raw}->{dep.name}",
                    source_kind="dependency",
                    retrieval_source=dep.retrieval_source,
                    status="truncated",
                    source_path=dep.source_path,
                    symbol=dep.name,
                    start_line=dep.start_line,
                    end_line=dep.end_line,
                    confidence=dep.confidence,
                    risk_notes=["direct dependency context truncated by max_dependency_snippets"],
                )
            )

    for dep in dependencies[:max_dependency_snippets]:
        key = (dep.source_path, dep.name)
        if key in seen:
            continue
        seen.add(key)
        segment_bytes = len(dep.segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("direct dependency context truncated by max_total_bytes")
            retrieval_trace.append(
                _trace(
                    target=f"dependency:{target.raw}->{dep.name}",
                    source_kind="dependency",
                    retrieval_source=dep.retrieval_source,
                    status="truncated",
                    source_path=dep.source_path,
                    symbol=dep.name,
                    start_line=dep.start_line,
                    end_line=dep.end_line,
                    confidence=dep.confidence,
                    risk_notes=["direct dependency context truncated by max_total_bytes"],
                )
            )
            break
        total_bytes += segment_bytes
        content_hash = hashlib.sha256(dep.segment.encode("utf-8")).hexdigest()
        dep_target = f"dependency:{target.raw}->{dep.name}"
        trace_id = _trace_id(
            dep_target,
            "dependency",
            dep.retrieval_source,
            dep.source_path,
            dep.name,
            dep.start_line,
            dep.end_line,
            content_hash,
        )
        snippets.append(
            SourceContextSnippet(
                path=dep.source_path,
                source_path=dep.source_path,
                target_ref=f"dependency:{dep.name}",
                target=dep_target,
                symbol=dep.name,
                source_kind="dependency",
                retrieval_source=dep.retrieval_source,
                confidence=dep.confidence,
                start_line=dep.start_line,
                end_line=dep.end_line,
                content_hash=content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=dep_target,
                source_kind="dependency",
                retrieval_source=dep.retrieval_source,
                status="resolved",
                source_path=dep.source_path,
                symbol=dep.name,
                start_line=dep.start_line,
                end_line=dep.end_line,
                confidence=dep.confidence,
                content_hash=content_hash,
            )
        )
        header = f"## {dep.source_path}:{dep.start_line}-{dep.end_line} (dependency:{dep.name})"
        pieces.append(f"{header}\n```python\n{dep.segment}\n```")
    return total_bytes


def _append_lsp_references(
    ctx: ToolContext,
    target: _Target,
    snippets: list[SourceContextSnippet],
    pieces: list[str],
    seen: set[tuple[str, str | None]],
    total_bytes: int,
    *,
    max_total_bytes: int,
    max_reference_snippets: int,
    risk_notes: list[str],
    retrieval_trace: list[SourceContextRetrievalTrace],
) -> int:
    if not target.symbol:
        return total_bytes
    try:
        out = lsp_references(
            ctx,
            LspReferencesInput(
                query=target.symbol,
                path=".",
                glob="*.py",
                max_matches=max_reference_snippets + 1,
            ),
        )
    except Exception as exc:
        risk_notes.append(f"reference context unavailable for {target.raw}: {type(exc).__name__}")
        return total_bytes

    references = [
        reference
        for reference in out.references
        if reference.source_path != target.rel_path
        and not _is_generated_test_path(reference.source_path, ctx.relpath(ctx.test_write_dir))
    ]
    if not references:
        return total_bytes

    if len(references) > max_reference_snippets:
        risk_notes.append("reference context truncated by max_reference_snippets")
        for reference in references[max_reference_snippets:]:
            retrieval_trace.append(
                _trace(
                    target=f"reference:{target.raw}->{reference.source_path}:{reference.line_range.get('start', 1)}",
                    source_kind="reference",
                    retrieval_source="lsp",
                    status="truncated",
                    source_path=reference.source_path,
                    symbol=reference.symbol,
                    start_line=reference.line_range.get("start", 1),
                    end_line=reference.line_range.get("end", reference.line_range.get("start", 1)),
                    confidence=reference.confidence,
                    risk_notes=["reference context truncated by max_reference_snippets"],
                )
            )

    for reference in references[:max_reference_snippets]:
        start_line = reference.line_range.get("start", 1)
        end_line = reference.line_range.get("end", start_line)
        key = (reference.source_path, f"reference:{reference.symbol}:{start_line}:{end_line}")
        if key in seen:
            continue
        seen.add(key)
        segment = reference.matched_text
        segment_bytes = len(segment.encode("utf-8"))
        reference_target = f"reference:{target.raw}->{reference.source_path}:{start_line}"
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("reference context truncated by max_total_bytes")
            retrieval_trace.append(
                _trace(
                    target=reference_target,
                    source_kind="reference",
                    retrieval_source="lsp",
                    status="truncated",
                    source_path=reference.source_path,
                    symbol=reference.symbol,
                    start_line=start_line,
                    end_line=end_line,
                    confidence=reference.confidence,
                    risk_notes=["reference context truncated by max_total_bytes"],
                )
            )
            break
        total_bytes += segment_bytes
        trace_id = _trace_id(
            reference_target,
            "reference",
            "lsp",
            reference.source_path,
            reference.symbol or "",
            start_line,
            end_line,
            reference.content_hash,
        )
        snippets.append(
            SourceContextSnippet(
                path=reference.source_path,
                source_path=reference.source_path,
                target_ref=f"reference:{target.symbol}",
                target=reference_target,
                symbol=reference.symbol,
                source_kind="reference",
                retrieval_source="lsp",
                confidence=reference.confidence,
                start_line=start_line,
                end_line=end_line,
                content_hash=reference.content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=reference_target,
                source_kind="reference",
                retrieval_source="lsp",
                status="resolved",
                source_path=reference.source_path,
                symbol=reference.symbol,
                start_line=start_line,
                end_line=end_line,
                confidence=reference.confidence,
                content_hash=reference.content_hash,
            )
        )
        header = f"## {reference.source_path}:{start_line}-{end_line} (reference:{target.symbol})"
        pieces.append(f"{header}\n```python\n{segment}\n```")
    return total_bytes


def _append_support_context(
    ctx: ToolContext,
    analysis: AnalyzeProjectOutput,
    snippets: list[SourceContextSnippet],
    pieces: list[str],
    seen: set[tuple[str, str | None]],
    total_bytes: int,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_support_snippets: int,
    risk_notes: list[str],
    retrieval_trace: list[SourceContextRetrievalTrace],
) -> int:
    support, duplicate_support = _dedupe_support_targets(_sort_targets(_support_targets(ctx, analysis)), seen)
    for target, kept in duplicate_support:
        retrieval_trace.append(
            _trace(
                target=target.raw,
                source_kind=target.source_kind,
                retrieval_source=target.retrieval_source,
                status="resolved",
                source_path=_safe_source_path(target.rel_path),
                symbol=target.symbol,
                confidence=target.confidence,
                risk_notes=[_support_dedup_note(kept)],
            )
        )

    if len(support) > max_support_snippets:
        risk_notes.append("support context truncated by max_support_snippets")
        for target in support[max_support_snippets:]:
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="truncated",
                    source_path=_safe_source_path(target.rel_path),
                    symbol=target.symbol,
                    confidence=target.confidence,
                    risk_notes=["support context truncated by max_support_snippets"],
                )
            )
    for target in support[:max_support_snippets]:
        key = (target.rel_path, target.symbol)
        if key in seen:
            continue
        seen.add(key)
        try:
            out = read_file(ctx, ReadFileInput(path=target.rel_path, max_bytes=max_file_bytes))
        except Exception:
            risk_notes.append(f"support context unavailable: {target.raw}")
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="error",
                    source_path=_safe_source_path(target.rel_path),
                    symbol=target.symbol,
                    confidence=0.0,
                    risk_notes=["support source file could not be read"],
                )
            )
            continue
        if not target.symbol:
            continue
        sliced = _slice_symbol(out.content, target.symbol)
        if sliced is None:
            risk_notes.append(f"support context symbol unavailable: {target.raw}")
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="missing",
                    source_path=out.path,
                    symbol=target.symbol,
                    confidence=0.0,
                    risk_notes=["support symbol could not be sliced from source file"],
                )
            )
            continue
        start_line, end_line, segment = sliced
        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("support context truncated by max_total_bytes")
            retrieval_trace.append(
                _trace(
                    target=target.raw,
                    source_kind=target.source_kind,
                    retrieval_source=target.retrieval_source,
                    status="truncated",
                    source_path=out.path,
                    symbol=target.symbol,
                    start_line=start_line,
                    end_line=end_line,
                    confidence=target.confidence,
                    risk_notes=["support context truncated by max_total_bytes"],
                )
            )
            break
        total_bytes += segment_bytes
        content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
        trace_id = _trace_id(
            target.raw,
            target.source_kind,
            target.retrieval_source,
            out.path,
            target.symbol,
            start_line,
            end_line,
            content_hash,
        )
        snippets.append(
            SourceContextSnippet(
                path=out.path,
                source_path=out.path,
                target_ref=target.raw,
                target=target.raw,
                symbol=target.symbol,
                source_kind=target.source_kind,
                retrieval_source=target.retrieval_source,
                confidence=target.confidence,
                start_line=start_line,
                end_line=end_line,
                content_hash=content_hash,
                bytes=segment_bytes,
                retrieval_trace_id=trace_id,
            )
        )
        retrieval_trace.append(
            _trace(
                trace_id=trace_id,
                target=target.raw,
                source_kind=target.source_kind,
                retrieval_source=target.retrieval_source,
                status="resolved",
                source_path=out.path,
                symbol=target.symbol,
                start_line=start_line,
                end_line=end_line,
                confidence=target.confidence,
                content_hash=content_hash,
            )
        )
        header = f"## {out.path}:{start_line}-{end_line} ({target.raw})"
        pieces.append(f"{header}\n```python\n{segment}\n```")
    return total_bytes


def _sort_targets(targets: list[_Target]) -> list[_Target]:
    return sorted(
        targets,
        key=lambda target: (
            _SOURCE_KIND_PRIORITY.get(target.source_kind, 99),
            _confidence_band(target.confidence),
            _RETRIEVAL_SOURCE_PRIORITY.get(target.retrieval_source, 99),
            -target.confidence,
            target.rel_path,
            target.symbol or "",
            target.raw,
        ),
    )


def _confidence_band(confidence: float) -> int:
    if confidence >= 0.7:
        return 0
    if confidence >= 0.4:
        return 1
    return 2


def _lsp_fallback_risk_notes(retrieval_trace: list[SourceContextRetrievalTrace]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for trace in retrieval_trace:
        if trace.source_kind != "target_source" or trace.retrieval_source != "lsp" or trace.status == "resolved":
            continue
        reason = "; ".join(trace.risk_notes) if trace.risk_notes else f"status={trace.status}"
        note = f"lsp definition {trace.status} for {trace.target}; fell back to ast_grep/rg ({reason})"
        if note in seen:
            continue
        seen.add(note)
        notes.append(note)
    return notes
