from __future__ import annotations

import ast
from pathlib import Path

from app.schemas.tools import (
    AnalyzeProjectInput,
    AnalyzeProjectOutput,
    ExistingTestInfo,
    FileInfo,
    FixtureInfo,
    FunctionInfo,
    ModelInfo,
    RouteInfo,
    TestFunctionInfo,
)
from app.tools.base import ToolContext

# FastAPI 路由装饰器动词
_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "options", "head"}
_IGNORE = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
_DEP_FILES = ["pyproject.toml", "requirements.txt", "setup.cfg", "setup.py", "Pipfile"]


def analyze_project(ctx: ToolContext, inp: AnalyzeProjectInput) -> AnalyzeProjectOutput:
    """静态分析：AST 抽函数/模型 + FastAPI 路由内省。绝不 import 业务代码、不把整仓喂给 LLM。"""
    out = AnalyzeProjectOutput()
    scopes, warnings = _analysis_scopes(ctx, inp.target_scope)
    out.warnings.extend(warnings)

    for f in _collect_py_files(scopes):
        rel = ctx.relpath(f)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            out.warnings.append(f"解析失败 {rel}: {e}")
            continue

        if _is_test_file(rel):
            out.files.append(FileInfo(path=rel, kind="test", size=f.stat().st_size))
            tinfo = _extract_tests(rel, tree)
            if tinfo.test_functions:
                out.existing_tests.append(tinfo)
            out.fixtures.extend(_extract_fixtures(rel, tree))
        else:
            out.files.append(FileInfo(path=rel, kind="source", size=f.stat().st_size))
            _extract_source(rel, tree, out)

    for name in _DEP_FILES:
        if (ctx.root / name).exists():
            out.dependency_files.append(name)
    return out


def _analysis_scopes(ctx: ToolContext, target_scope: list[str]) -> tuple[list[Path], list[str]]:
    if not target_scope:
        return [ctx.root], []

    scopes: list[Path] = []
    needs_project_scan = False
    warnings: list[str] = []
    for raw in target_scope:
        norm = str(raw).replace("\\", "/").strip()
        if not norm:
            continue
        path_candidate = _path_scope_from_target(norm)
        if path_candidate is None:
            needs_project_scan = True
            continue
        try:
            scopes.append(ctx.resolve_read(path_candidate))
        except Exception as exc:
            warnings.append(f"target_scope {norm!r} could not be resolved as a project path: {exc}")

    if needs_project_scan:
        scopes.insert(0, ctx.root)
    deduped = list(dict.fromkeys(scopes))
    return (deduped or [ctx.root]), warnings


def _path_scope_from_target(norm: str) -> str | None:
    for sep in ("::", ":"):
        if sep in norm and norm.split(sep, 1)[0].endswith(".py"):
            return norm.split(sep, 1)[0]
    if norm.endswith(".py"):
        return norm
    if norm in {".", "./"}:
        return "."
    if "/" in norm and not norm.startswith("/"):
        return norm
    return None


def _collect_py_files(scopes: list[Path]) -> list[Path]:
    seen: dict[Path, bool] = {}
    for base in scopes:
        if base.is_file() and base.suffix == ".py":
            seen[base] = True
        elif base.is_dir():
            for p in base.rglob("*.py"):
                if any(part in _IGNORE for part in p.parts):
                    continue
                seen[p] = True
    return sorted(seen)


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (
        rel.startswith("tests/")
        or "/tests/" in rel
        or name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _signature(node) -> str:
    try:
        return f"{node.name}({ast.unparse(node.args)})"
    except Exception:
        return f"{node.name}(...)"


def _extract_source(rel: str, tree: ast.AST, out: AnalyzeProjectOutput) -> None:
    prefixes = _router_prefixes(tree)
    seen_fn: set[str] = set()

    # 模块级函数 + pydantic 模型
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add_fn(out, rel, node, seen_fn)
        elif isinstance(node, ast.ClassDef) and _is_pydantic_model(node):
            out.models.append(ModelInfo(name=node.name, file=rel))

    # 全树扫路由：handler 可能在任意 router 上
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for route in _routes_from_decorators(node, rel, prefixes):
                out.routes.append(route)
                _add_fn(out, rel, node, seen_fn)

    # include_router 的 prefix 跨文件合并 V1 不做，诚实记 warning，别让 Agent 以为路由前缀齐了
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee_name(node) == "include_router":
            pfx = _kw_str(node, "prefix")
            if pfx:
                out.warnings.append(
                    f"{rel}: include_router(prefix={pfx!r}) 前缀未跨文件合并，相关路由 path 可能缺前缀"
                )


def _router_prefixes(tree: ast.AST) -> dict[str, str]:
    # 单文件内追踪 `x = APIRouter(prefix="/api")`，给该 router 的路由补前缀
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _callee_name(node.value) == "APIRouter":
                pfx = _kw_str(node.value, "prefix") or ""
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        prefixes[t.id] = pfx
    return prefixes


def _add_fn(out: AnalyzeProjectOutput, rel: str, node, seen_fn: set[str]) -> None:
    key = f"{rel}::{node.name}"
    if key in seen_fn:
        return
    seen_fn.add(key)
    out.functions.append(FunctionInfo(name=node.name, signature=_signature(node), file=rel))


def _routes_from_decorators(node, rel: str, prefixes: dict[str, str]):
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        verb = dec.func.attr
        recv = dec.func.value
        prefix = prefixes.get(recv.id, "") if isinstance(recv, ast.Name) else ""
        path = _route_path(dec)
        if path is None:
            continue  # 路径不是字面量（变量/常量引用），静态分析放弃，不瞎报
        full = _join_path(prefix, path)
        if verb in _HTTP_VERBS:
            yield RouteInfo(method=verb.upper(), path=full, handler=node.name, file=rel)
        elif verb == "api_route":
            for m in _methods_kwarg(dec):
                yield RouteInfo(method=m.upper(), path=full, handler=node.name, file=rel)


def _route_path(call: ast.Call):
    # 位置参数优先，其次 path= keyword
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return _kw_str(call, "path")


def _join_path(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    return prefix.rstrip("/") + "/" + path.lstrip("/")


def _callee_name(call: ast.Call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _kw_str(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _methods_kwarg(call: ast.Call) -> list[str]:
    for kw in call.keywords:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            return [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _extract_tests(rel: str, tree: ast.AST) -> ExistingTestInfo:
    info = ExistingTestInfo(path=rel)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            info.test_functions.append(
                TestFunctionInfo(name=node.name, line=node.lineno, estimated_nodeid=f"{rel}::{node.name}")
            )
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test"):
                    info.test_functions.append(
                        TestFunctionInfo(
                            name=m.name,
                            line=m.lineno,
                            estimated_nodeid=f"{rel}::{node.name}::{m.name}",
                        )
                    )
    return info


def _extract_fixtures(rel: str, tree: ast.AST) -> list[FixtureInfo]:
    fixtures: list[FixtureInfo] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_pytest_fixture(node):
            continue
        fixtures.append(
            FixtureInfo(
                name=node.name,
                file=rel,
                line=node.lineno,
                dependencies=[arg.arg for arg in node.args.args],
            )
        )
    return fixtures


def _is_pytest_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
    return False
