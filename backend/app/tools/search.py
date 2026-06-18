from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from app.schemas.tools import (
    AstGrepMatch,
    AstGrepSearchInput,
    AstGrepSearchOutput,
    RgSearchInput,
    RgSearchMatch,
    RgSearchOutput,
)
from app.tools.base import ToolContext

_IGNORE = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
_HTTP_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}


def rg_search(ctx: ToolContext, inp: RgSearchInput) -> RgSearchOutput:
    base = ctx.resolve_read(inp.path)
    warnings: list[str] = []
    if shutil.which("rg"):
        try:
            return _search_with_rg(ctx, base, inp)
        except Exception as exc:
            warnings.append(f"rg search failed, used python fallback: {type(exc).__name__}: {exc}")
    out = _search_with_python(ctx, base, inp)
    out.warnings.extend(warnings)
    return out


def ast_grep_search(ctx: ToolContext, inp: AstGrepSearchInput) -> AstGrepSearchOutput:
    base = ctx.resolve_read(inp.path)
    matches: list[AstGrepMatch] = []
    warnings: list[str] = []
    truncated = False
    for path in _iter_files(base):
        rel = ctx.relpath(path)
        if inp.glob and not (fnmatch.fnmatch(path.name, inp.glob) or fnmatch.fnmatch(rel, inp.glob)):
            continue
        text = path.read_bytes()[: inp.max_file_bytes].decode("utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            warnings.append(f"{rel}: SyntaxError: {exc.msg}")
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            match = _structured_match(ctx, path, lines, node, inp)
            if match is None:
                continue
            matches.append(match)
            if len(matches) >= inp.max_matches:
                truncated = True
                return AstGrepSearchOutput(
                    query=inp.query,
                    kind=inp.kind,
                    matches=matches,
                    truncated=truncated,
                    engine="python_ast_fallback",
                    warnings=warnings,
                )
    return AstGrepSearchOutput(
        query=inp.query,
        kind=inp.kind,
        matches=matches,
        truncated=truncated,
        engine="python_ast_fallback",
        warnings=warnings,
    )


def _search_with_rg(ctx: ToolContext, base: Path, inp: RgSearchInput) -> RgSearchOutput:
    cmd = ["rg", "--json", "--no-messages", "--color", "never", "-F"]
    if not inp.case_sensitive:
        cmd.append("-i")
    if inp.glob:
        cmd.extend(["-g", inp.glob])
    cmd.extend(["--", inp.query, str(base)])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError((proc.stderr or proc.stdout or "rg failed").strip())

    matches: list[RgSearchMatch] = []
    truncated = False
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = ((data.get("path") or {}).get("text") or "").strip()
        line_number = int(data.get("line_number") or 0)
        line_text = str(((data.get("lines") or {}).get("text") or "")).rstrip("\r\n")
        if not path_text or line_number < 1:
            continue
        matches.append(_match(ctx, Path(path_text), line_number, line_text, inp.query, engine="rg"))
        if len(matches) >= inp.max_matches:
            truncated = True
            break
    return RgSearchOutput(query=inp.query, matches=matches, truncated=truncated, engine="rg")


def _search_with_python(ctx: ToolContext, base: Path, inp: RgSearchInput) -> RgSearchOutput:
    matches: list[RgSearchMatch] = []
    truncated = False
    needle = inp.query if inp.case_sensitive else inp.query.lower()
    for path in _iter_files(base):
        rel = ctx.relpath(path)
        if inp.glob and not (fnmatch.fnmatch(path.name, inp.glob) or fnmatch.fnmatch(rel, inp.glob)):
            continue
        text = path.read_bytes()[: inp.max_file_bytes].decode("utf-8", errors="replace")
        for index, line in enumerate(text.splitlines(), start=1):
            haystack = line if inp.case_sensitive else line.lower()
            if needle not in haystack:
                continue
            matches.append(_match(ctx, path, index, line, inp.query, engine="python_fallback"))
            if len(matches) >= inp.max_matches:
                truncated = True
                return RgSearchOutput(
                    query=inp.query,
                    matches=matches,
                    truncated=truncated,
                    engine="python_fallback",
                )
    return RgSearchOutput(query=inp.query, matches=matches, truncated=truncated, engine="python_fallback")


def _structured_match(
    ctx: ToolContext,
    path: Path,
    lines: list[str],
    node: ast.AST,
    inp: AstGrepSearchInput,
) -> AstGrepMatch | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        route = _route_match(node, inp)
        if route is not None:
            method, route_path = route
            return _ast_match(
                ctx,
                path,
                lines,
                node,
                inp.query,
                node_kind="route",
                symbol=node.name,
                confidence=0.86,
                metadata={"method": method, "path": route_path},
            )
        if inp.kind in {None, "function"} and node.name == inp.query:
            return _ast_match(
                ctx,
                path,
                lines,
                node,
                inp.query,
                node_kind="function",
                symbol=node.name,
                confidence=0.82,
            )
    if isinstance(node, ast.ClassDef) and inp.kind in {None, "class"} and node.name == inp.query:
        return _ast_match(
            ctx,
            path,
            lines,
            node,
            inp.query,
            node_kind="class",
            symbol=node.name,
            confidence=0.82,
        )
    return None


def _route_match(node: ast.FunctionDef | ast.AsyncFunctionDef, inp: AstGrepSearchInput) -> tuple[str, str] | None:
    if inp.kind not in {None, "route"}:
        return None
    query_method, query_path = _route_query(inp.query, inp.method)
    if not query_path:
        return None
    for decorator in node.decorator_list:
        route = _route_decorator_parts(decorator)
        if route is None:
            continue
        method, path = route
        if path == query_path and (query_method is None or method == query_method):
            return method, path
    return None


def _route_query(query: str, method: str | None) -> tuple[str | None, str | None]:
    norm = query.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    parts = norm.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in _HTTP_VERBS:
        return parts[0].upper(), parts[1].strip() if parts[1].startswith("/") else None
    if method and norm.startswith("/"):
        return method.upper(), norm
    return None, norm if norm.startswith("/") else None


def _route_decorator_parts(decorator: ast.AST) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    method = decorator.func.attr.upper()
    if method not in _HTTP_VERBS or not decorator.args:
        return None
    first = decorator.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return method, first.value


def _ast_match(
    ctx: ToolContext,
    path: Path,
    lines: list[str],
    node: ast.AST,
    query: str,
    *,
    node_kind: str,
    symbol: str,
    confidence: float,
    metadata: dict[str, str] | None = None,
) -> AstGrepMatch:
    start, end, segment = _node_segment(lines, node)
    source_path = ctx.relpath(path)
    content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
    trace_id = _trace_id("ast", query, source_path, node_kind, symbol, start, end, content_hash)
    return AstGrepMatch(
        source_path=source_path,
        line_range={"start": start, "end": end},
        matched_text=segment,
        symbol=symbol,
        node_kind=node_kind,  # type: ignore[arg-type]
        content_hash=content_hash,
        trace_id=trace_id,
        confidence=confidence,
        engine="python_ast_fallback",
        metadata=metadata or {},
    )


def _node_segment(lines: list[str], node: ast.AST) -> tuple[int, int, str]:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min(start, min(decorator.lineno for decorator in decorators))
    segment = "\n".join(lines[start - 1 : end])
    return start, end, segment


def _iter_files(base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _IGNORE for part in path.parts):
            continue
        files.append(path)
    return files


def _match(ctx: ToolContext, path: Path, line_number: int, line_text: str, query: str, *, engine: str) -> RgSearchMatch:
    source_path = ctx.relpath(path)
    content_hash = hashlib.sha256(line_text.encode("utf-8")).hexdigest()
    trace_id = _trace_id(query, source_path, line_number, content_hash)
    return RgSearchMatch(
        source_path=source_path,
        line_number=line_number,
        line_range={"start": line_number, "end": line_number},
        line_text=line_text,
        content_hash=content_hash,
        trace_id=trace_id,
        confidence=0.55 if engine == "rg" else 0.45,
        engine=engine,  # type: ignore[arg-type]
    )


def _trace_id(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts)
    prefix = "ast-" if parts and parts[0] == "ast" else "rg-"
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
