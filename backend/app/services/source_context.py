from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.schemas.evaluation import ContextCompletenessEvidence, SourceContextRetrievalTrace, SourceContextSnippet
from app.schemas.tools import AnalyzeProjectOutput, ReadFileInput, RgSearchInput, RgSearchMatch
from app.tools.base import ToolContext
from app.tools.fs_tools import read_file
from app.tools.search import rg_search


_SOURCE_KIND_PRIORITY = {
    "target_source": 0,
    "model_schema": 1,
    "fixture": 2,
    "existing_test": 3,
    "failure_context": 4,
    "fallback_file": 5,
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

    target_snippet_count = len(snippets)

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

    support_incomplete = _support_context_incomplete(retrieval_trace)
    if target_snippet_count == 0:
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

        rg_target, rg_trace = _resolve_one_with_rg(ctx, str(raw))
        retrieval_trace.extend(rg_trace)
        if rg_target is not None:
            targets.append(rg_target)
        else:
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


def _rg_query_for_target(value: str) -> str | None:
    _method, route_path = _route_target_parts(value)
    if route_path:
        return route_path
    symbol = _symbol_query_for_target(value)
    return symbol or None


def _symbol_query_for_target(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    if _looks_like_route_target(norm):
        return ""
    if "::" in norm and norm.split("::", 1)[0].endswith(".py"):
        norm = norm.split("::", 1)[1]
    elif ":" in norm and norm.split(":", 1)[0].endswith(".py"):
        norm = norm.split(":", 1)[1]
    return norm.rsplit(".", 1)[-1].strip()


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


def _looks_like_route_target(value: str) -> bool:
    _method, route_path = _route_target_parts(value)
    return route_path is not None


def _route_target_parts(value: str) -> tuple[str | None, str | None]:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    parts = norm.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
        path = parts[1].strip()
        return (parts[0].upper(), path) if path.startswith("/") else (None, None)
    return (None, norm) if norm.startswith("/") else (None, None)


def _route_handler_for_path(content: str, method: str | None, path: str) -> str | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _route_decorator_parts(decorator)
            if route is None:
                continue
            decorator_method, decorator_path = route
            if decorator_path == path and (method is None or decorator_method == method):
                return node.name
    return None


def _route_decorator_parts(decorator: ast.AST) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr.upper()
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
        return None
    if not decorator.args:
        return None
    first_arg = decorator.args[0]
    if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
        return None
    return method, first_arg.value


def _route_path_without_method(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    parts = norm.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
        return parts[1].strip()
    return norm


def _route_with_method(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    return norm


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
    support = _sort_targets(_support_targets(ctx, analysis))
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


def _support_targets(ctx: ToolContext, analysis: AnalyzeProjectOutput) -> list[_Target]:
    generated_tests_dir = ctx.relpath(ctx.test_write_dir)
    targets: list[_Target] = []
    for model in analysis.models:
        targets.append(
            _Target(
                raw=f"model:{model.name}",
                rel_path=model.file,
                symbol=model.name,
                source_kind="model_schema",
                retrieval_source="analysis_ast",
                confidence=0.95,
            )
        )
    for fixture in analysis.fixtures:
        if _is_generated_test_path(fixture.file, generated_tests_dir):
            continue
        targets.append(
            _Target(
                raw=f"fixture:{fixture.name}",
                rel_path=fixture.file,
                symbol=fixture.name,
                source_kind="fixture",
                retrieval_source="pytest_scanner",
                confidence=0.95,
            )
        )
    for test_file in analysis.existing_tests:
        if _is_generated_test_path(test_file.path, generated_tests_dir):
            continue
        for test in test_file.test_functions:
            targets.append(
                _Target(
                    raw=f"existing_test:{test.estimated_nodeid}",
                    rel_path=test_file.path,
                    symbol=test.name,
                    source_kind="existing_test",
                    retrieval_source="pytest_scanner",
                    confidence=0.9,
                )
            )
    return targets


def _sort_targets(targets: list[_Target]) -> list[_Target]:
    return sorted(
        targets,
        key=lambda target: (
            _SOURCE_KIND_PRIORITY.get(target.source_kind, 99),
            target.rel_path,
            target.symbol or "",
            target.raw,
        ),
    )


def _support_context_incomplete(retrieval_trace: list[SourceContextRetrievalTrace]) -> bool:
    support_kinds = {"model_schema", "fixture", "existing_test", "failure_context"}
    return any(trace.source_kind in support_kinds and trace.status != "resolved" for trace in retrieval_trace)


def _trace_id(*parts: object) -> str:
    raw = "\0".join("" if part is None else str(part) for part in parts)
    return "ctx-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _trace(
    *,
    target: str,
    source_kind: str,
    retrieval_source: str,
    status: str,
    confidence: float,
    trace_id: str | None = None,
    source_path: str | None = None,
    symbol: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    content_hash: str | None = None,
    risk_notes: list[str] | None = None,
) -> SourceContextRetrievalTrace:
    line_range = {"start": start_line, "end": end_line} if start_line is not None and end_line is not None else None
    return SourceContextRetrievalTrace(
        trace_id=trace_id
        or _trace_id(target, source_kind, retrieval_source, source_path or "", symbol or "", status, content_hash or ""),
        source_kind=source_kind,  # type: ignore[arg-type]
        retrieval_source=retrieval_source,  # type: ignore[arg-type]
        target=target,
        source_path=_safe_source_path(source_path),
        symbol=symbol,
        line_range=line_range,
        confidence=confidence,
        content_hash=content_hash,
        status=status,  # type: ignore[arg-type]
        risk_notes=risk_notes or [],
    )


def _safe_source_path(path: str | None) -> str | None:
    if not path:
        return None
    norm = path.replace("\\", "/").strip()
    if not norm or norm.startswith("/") or norm.startswith("//") or (len(norm) >= 2 and norm[1] == ":"):
        return None
    if any(part == ".." for part in norm.split("/")):
        return None
    return norm


def _is_generated_test_path(path: str, generated_tests_dir: str) -> bool:
    norm = path.replace("\\", "/").strip("/")
    generated = generated_tests_dir.replace("\\", "/").strip("/")
    return bool(generated) and (norm == generated or norm.startswith(generated + "/"))
