from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.schemas.evaluation import ContextCompletenessEvidence, SourceContextSnippet
from app.schemas.tools import AnalyzeProjectOutput, ReadFileInput
from app.tools.base import ToolContext
from app.tools.fs_tools import read_file


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
    seen: set[tuple[str, str | None]] = set()

    targets, unresolved = _resolve_targets(ctx, target_scope, analysis)
    # 连「目标 -> 文件」都映射不到的，先记进缺失，别让它们在记录里凭空消失。
    missing_targets.extend(unresolved)
    if len(targets) > max_snippets:
        truncated = targets[max_snippets:]
        missing_targets.extend(target.raw for target in truncated)
        risk_notes.append("source context truncated by max_snippets")
    for target in targets[:max_snippets]:
        key = (target.rel_path, target.symbol)
        if key in seen:
            continue
        seen.add(key)
        try:
            out = read_file(ctx, ReadFileInput(path=target.rel_path, max_bytes=max_file_bytes))
        except Exception:
            missing_targets.append(target.raw)
            continue

        if target.symbol:
            sliced = _slice_symbol(out.content, target.symbol)
            if sliced is None:
                # 文件读到了，但定位不到目标符号：算缺失，不要退化成整文件冒充命中。
                missing_targets.append(target.raw)
                continue
            start_line, end_line, segment = sliced
            target_ref = target.symbol
        else:
            start_line, end_line, segment = 1, max(1, out.content.count("\n") + 1), out.content
            target_ref = Path(target.rel_path).stem

        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("source context truncated by max_total_bytes")
            break
        total_bytes += segment_bytes
        snippets.append(
            SourceContextSnippet(
                path=out.path,
                target_ref=target_ref,
                start_line=start_line,
                end_line=end_line,
                content_hash=hashlib.sha256(segment.encode("utf-8")).hexdigest(),
                bytes=segment_bytes,
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
        )

    if target_snippet_count == 0:
        status = "incomplete"
        risk_notes.append("no source snippet could be read")
    elif missing_targets:
        status = "partial"
        risk_notes.append("some source targets were unavailable")
    else:
        status = "complete"

    context = ContextCompletenessEvidence(
        status=status,
        context_incomplete=status != "complete",
        snippets=snippets,
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
) -> tuple[list[_Target], list[str]]:
    if not target_scope:
        # 没给目标：退回少量默认入口文件整文件读，保持 demo 可跑。
        defaults = ["calc.py", "app.py", "main.py"]
        return [_Target(raw=name, rel_path=name, symbol=None) for name in defaults if (ctx.root / name).exists()], []

    targets: list[_Target] = []
    unresolved: list[str] = []
    for raw in target_scope:
        target = _resolve_one(ctx, str(raw), analysis)
        if target is not None:
            targets.append(target)
        else:
            unresolved.append(str(raw))
    return targets, unresolved


def _resolve_one(ctx: ToolContext, raw: str, analysis: AnalyzeProjectOutput | None) -> _Target | None:
    norm = raw.replace("\\", "/").strip()
    if not norm:
        return None

    # 形如 path.py::symbol 或 path.py:symbol —— 显式文件 + 符号。
    for sep in ("::", ":"):
        if sep in norm and norm.split(sep, 1)[0].endswith(".py"):
            path_part, symbol = norm.split(sep, 1)
            return _Target(raw=raw, rel_path=path_part, symbol=symbol or None)

    # 先按符号 / 路由 path 查 analyze 结果。路由目标常写成 "/items" 或 "/api/users"，
    # 不能因为包含 "/" 就误判成文件路径。
    if analysis is not None:
        file = _file_for_symbol(analysis, norm)
        if file is not None:
            return _Target(raw=raw, rel_path=file, symbol=_symbol_for_target(analysis, norm))

    # 直接是一个文件路径：整文件。
    if norm.endswith(".py") or "/" in norm:
        return _Target(raw=raw, rel_path=norm, symbol=None)

    return None


def _file_for_symbol(analysis: AnalyzeProjectOutput, symbol: str) -> str | None:
    # 支持传 "module.func" 或裸 "func"；路由可传 handler 名或 path。
    short = symbol.rsplit(".", 1)[-1]
    for fn in analysis.functions:
        if fn.name == short:
            return fn.file
    for model in analysis.models:
        if model.name == short:
            return model.file
    for route in analysis.routes:
        if route.handler == short or route.path == symbol:
            return route.file
    return None


def _symbol_for_target(analysis: AnalyzeProjectOutput, target: str) -> str:
    short = target.rsplit(".", 1)[-1]
    for route in analysis.routes:
        if route.path == target:
            return route.handler
    return short


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
) -> int:
    support = _support_targets(ctx, analysis)
    if len(support) > max_support_snippets:
        risk_notes.append("support context truncated by max_support_snippets")
    for target in support[:max_support_snippets]:
        key = (target.rel_path, target.raw)
        if key in seen:
            continue
        seen.add(key)
        try:
            out = read_file(ctx, ReadFileInput(path=target.rel_path, max_bytes=max_file_bytes))
        except Exception:
            risk_notes.append(f"support context unavailable: {target.raw}")
            continue
        if not target.symbol:
            continue
        sliced = _slice_symbol(out.content, target.symbol)
        if sliced is None:
            risk_notes.append(f"support context symbol unavailable: {target.raw}")
            continue
        start_line, end_line, segment = sliced
        segment_bytes = len(segment.encode("utf-8"))
        if total_bytes + segment_bytes > max_total_bytes:
            risk_notes.append("support context truncated by max_total_bytes")
            break
        total_bytes += segment_bytes
        snippets.append(
            SourceContextSnippet(
                path=out.path,
                target_ref=target.raw,
                start_line=start_line,
                end_line=end_line,
                content_hash=hashlib.sha256(segment.encode("utf-8")).hexdigest(),
                bytes=segment_bytes,
            )
        )
        header = f"## {out.path}:{start_line}-{end_line} ({target.raw})"
        pieces.append(f"{header}\n```python\n{segment}\n```")
    return total_bytes


def _support_targets(ctx: ToolContext, analysis: AnalyzeProjectOutput) -> list[_Target]:
    generated_tests_dir = ctx.relpath(ctx.test_write_dir)
    targets: list[_Target] = []
    for fixture in analysis.fixtures:
        if _is_generated_test_path(fixture.file, generated_tests_dir):
            continue
        targets.append(_Target(raw=f"fixture:{fixture.name}", rel_path=fixture.file, symbol=fixture.name))
    for test_file in analysis.existing_tests:
        if _is_generated_test_path(test_file.path, generated_tests_dir):
            continue
        for test in test_file.test_functions:
            targets.append(
                _Target(
                    raw=f"existing_test:{test.estimated_nodeid}",
                    rel_path=test_file.path,
                    symbol=test.name,
                )
            )
    return targets


def _is_generated_test_path(path: str, generated_tests_dir: str) -> bool:
    norm = path.replace("\\", "/").strip("/")
    generated = generated_tests_dir.replace("\\", "/").strip("/")
    return bool(generated) and (norm == generated or norm.startswith(generated + "/"))
