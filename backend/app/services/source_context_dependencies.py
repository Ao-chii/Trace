from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from app.schemas.tools import ReadFileInput
from app.services.source_context_trace import safe_source_path as _safe_source_path
from app.tools.base import ToolContext
from app.tools.fs_tools import read_file


@dataclass(frozen=True)
class DependencySlice:
    source_path: str
    name: str
    start_line: int
    end_line: int
    segment: str
    retrieval_source: str
    confidence: float


@dataclass(frozen=True)
class DependencyMiss:
    source_path: str | None
    name: str
    retrieval_source: str
    status: str
    confidence: float
    risk_note: str


@dataclass(frozen=True)
class _ImportedDependencyTarget:
    rel_path: str
    symbol: str


def dependency_slices(
    ctx: ToolContext,
    source_path: str,
    content: str,
    target_symbol: str,
    *,
    max_file_bytes: int,
    max_dependency_depth: int,
) -> tuple[list[DependencySlice], list[DependencyMiss]]:
    if max_dependency_depth <= 0:
        return [], []
    short_symbol = target_symbol.rsplit(".", 1)[-1]
    dependencies: list[DependencySlice] = []
    misses: list[DependencyMiss] = []
    content_cache: dict[str, str] = {source_path: content}
    visited: set[tuple[str, str]] = {(source_path, target_symbol), (source_path, short_symbol)}
    queue: list[tuple[str, str, str, int]] = [(source_path, content, target_symbol, 0)]

    while queue:
        current_path, current_content, current_symbol, depth = queue.pop(0)
        if depth >= max_dependency_depth:
            continue
        direct_dependencies, direct_misses = _direct_dependencies(
            ctx,
            current_path,
            current_content,
            current_symbol,
            max_file_bytes=max_file_bytes,
        )
        misses.extend(direct_misses)
        for dep in direct_dependencies:
            key = (dep.source_path, dep.name)
            if key in visited:
                continue
            visited.add(key)
            dependencies.append(dep)
            if depth + 1 >= max_dependency_depth:
                continue
            dep_content = content_cache.get(dep.source_path)
            if dep_content is None:
                try:
                    out = read_file(ctx, ReadFileInput(path=dep.source_path, max_bytes=max_file_bytes))
                except Exception:
                    misses.append(
                        DependencyMiss(
                            source_path=dep.source_path,
                            name=dep.name,
                            retrieval_source=dep.retrieval_source,
                            status="error",
                            confidence=0.0,
                            risk_note="cross-file direct dependency source could not be read",
                        )
                    )
                    continue
                dep_content = out.content
                content_cache[out.path] = out.content
            queue.append((dep.source_path, dep_content, dep.name, depth + 1))
    return dependencies, misses


def _direct_dependencies(
    ctx: ToolContext,
    source_path: str,
    content: str,
    target_symbol: str,
    *,
    max_file_bytes: int,
) -> tuple[list[DependencySlice], list[DependencyMiss]]:
    if not target_symbol:
        return [], []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], []
    lines = content.splitlines()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    class_methods = _class_method_nodes(tree)
    target_node, target_class = _find_callable_node(target_symbol, functions, class_methods)
    if target_node is None:
        return [], []
    module_assignments = _module_level_assignments(tree, source_path, lines)
    imported = _direct_from_import_targets(tree, source_path)
    imported_modules = _module_import_targets(ctx, tree, source_path)
    called: list[str] = []
    attribute_called: list[tuple[str, str]] = []
    method_called: list[tuple[str, str]] = []
    seen: set[str] = set()
    seen_attributes: set[tuple[str, str]] = set()
    for node in ast.walk(target_node):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name == target_node.name or name in seen:
                continue
            seen.add(name)
            called.append(name)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            receiver = node.func.value.id
            attr_name = node.func.attr
            if receiver in {"self", "cls"} and target_class and attr_name in class_methods.get(target_class, {}):
                key = (target_class, attr_name)
                if key in seen_attributes:
                    continue
                seen_attributes.add(key)
                method_called.append(key)
                continue
            if receiver in class_methods and attr_name in class_methods[receiver]:
                key = (receiver, attr_name)
                if key in seen_attributes:
                    continue
                seen_attributes.add(key)
                method_called.append(key)
                continue
            module_alias = receiver
            key = (module_alias, attr_name)
            if key in seen_attributes:
                continue
            seen_attributes.add(key)
            attribute_called.append(key)

    dependencies: list[DependencySlice] = []
    misses: list[DependencyMiss] = []
    for name in _module_names_referenced_by_body(target_node, module_assignments):
        dependency = module_assignments.get(name)
        if dependency is not None:
            dependencies.append(dependency)
    for name in called:
        if name in functions:
            dependency = _dependency_slice_from_node(
                source_path,
                functions[name],
                lines,
                retrieval_source="ast_grep",
                confidence=0.85,
            )
            if dependency is not None:
                dependencies.append(dependency)
            continue
        import_target = imported.get(name)
        if import_target is None:
            continue
        if not _import_target_is_project_local(ctx, import_target.rel_path):
            continue
        dependency, miss = _slice_imported_dependency(ctx, import_target, max_file_bytes=max_file_bytes)
        if miss is not None:
            misses.append(miss)
            continue
        if dependency is not None:
            dependencies.append(dependency)
    for module_alias, attr_name in attribute_called:
        rel_path = imported_modules.get(module_alias)
        if rel_path is None:
            continue
        if not _import_target_is_project_local(ctx, rel_path):
            continue
        import_target = _ImportedDependencyTarget(rel_path=rel_path, symbol=attr_name)
        dependency, miss = _slice_imported_dependency(ctx, import_target, max_file_bytes=max_file_bytes)
        if miss is not None:
            misses.append(miss)
            continue
        if dependency is not None:
            dependencies.append(dependency)
    for class_name, method_name in method_called:
        if class_name == target_class and method_name == target_node.name:
            continue
        method_node = class_methods.get(class_name, {}).get(method_name)
        if method_node is None:
            continue
        dependency = _dependency_slice_from_node(
            source_path,
            method_node,
            lines,
            name=f"{class_name}.{method_name}",
            retrieval_source="ast_grep",
            confidence=0.85,
        )
        if dependency is not None:
            dependencies.append(dependency)
    return dependencies, misses


def _module_level_assignments(tree: ast.AST, source_path: str, lines: list[str]) -> dict[str, DependencySlice]:
    assignments: dict[str, DependencySlice] = {}
    for node in getattr(tree, "body", []):
        names: list[str] = []
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if not names:
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if not start or not end:
            continue
        segment = "\n".join(lines[start - 1 : end])
        for name in names:
            assignments[name] = DependencySlice(
                source_path=source_path,
                name=name,
                start_line=start,
                end_line=end,
                segment=segment,
                retrieval_source="analysis_ast",
                confidence=0.8,
            )
    return assignments


def _module_names_referenced_by_body(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    assignments: dict[str, DependencySlice],
) -> list[str]:
    if not assignments:
        return []
    local_names = _callable_local_names(node)
    referenced: list[str] = []
    seen: set[str] = set()
    for stmt in node.body:
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            name = child.id
            if name in seen or name in local_names or name not in assignments:
                continue
            seen.add(name)
            referenced.append(name)
    return referenced


def _callable_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in node.args.args}
    names.update(arg.arg for arg in node.args.kwonlyargs)
    if node.args.vararg is not None:
        names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.add(node.args.kwarg.arg)
    for stmt in node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
    return names


def _class_method_nodes(tree: ast.AST) -> dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    methods: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.ClassDef):
            continue
        class_methods = {
            item.name: item
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if class_methods:
            methods[node.name] = class_methods
    return methods


def _find_callable_node(
    target_symbol: str,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    class_methods: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]],
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, str | None]:
    if "." in target_symbol:
        owner, method_name = target_symbol.rsplit(".", 1)
        method = class_methods.get(owner, {}).get(method_name)
        if method is not None:
            return method, owner

    short = target_symbol.rsplit(".", 1)[-1]
    if short in functions:
        return functions[short], None
    matches = [
        (class_name, methods[short])
        for class_name, methods in class_methods.items()
        if short in methods
    ]
    if len(matches) == 1:
        class_name, method = matches[0]
        return method, class_name
    return None, None


def _slice_imported_dependency(
    ctx: ToolContext,
    import_target: _ImportedDependencyTarget,
    *,
    max_file_bytes: int,
) -> tuple[DependencySlice | None, DependencyMiss | None]:
    try:
        out = read_file(ctx, ReadFileInput(path=import_target.rel_path, max_bytes=max_file_bytes))
    except Exception:
        return None, DependencyMiss(
            source_path=import_target.rel_path,
            name=import_target.symbol,
            retrieval_source="analysis_ast",
            status="error",
            confidence=0.0,
            risk_note="cross-file direct dependency source could not be read",
        )
    try:
        imported_tree = ast.parse(out.content)
    except SyntaxError:
        return None, DependencyMiss(
            source_path=out.path,
            name=import_target.symbol,
            retrieval_source="analysis_ast",
            status="error",
            confidence=0.0,
            risk_note="cross-file direct dependency source could not be parsed",
        )
    imported_functions = {
        node.name: node
        for node in imported_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_node = imported_functions.get(import_target.symbol)
    if imported_node is None:
        return None, DependencyMiss(
            source_path=out.path,
            name=import_target.symbol,
            retrieval_source="analysis_ast",
            status="missing",
            confidence=0.0,
            risk_note="cross-file direct dependency symbol could not be sliced",
        )
    dependency = _dependency_slice_from_node(
        out.path,
        imported_node,
        out.content.splitlines(),
        retrieval_source="analysis_ast",
        confidence=0.8,
    )
    return dependency, None


def _dependency_slice_from_node(
    source_path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    *,
    name: str | None = None,
    retrieval_source: str,
    confidence: float,
) -> DependencySlice | None:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if not start or not end:
        return None
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min(start, min(decorator.lineno for decorator in decorators))
    segment = "\n".join(lines[start - 1 : end])
    return DependencySlice(
        source_path=source_path,
        name=name or node.name,
        start_line=start,
        end_line=end,
        segment=segment,
        retrieval_source=retrieval_source,
        confidence=confidence,
    )


def _direct_from_import_targets(tree: ast.AST, source_path: str) -> dict[str, _ImportedDependencyTarget]:
    targets: dict[str, _ImportedDependencyTarget] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.ImportFrom):
            continue
        rel_path = _resolve_import_module_path(source_path, node.module, node.level)
        if rel_path is None:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            targets[local_name] = _ImportedDependencyTarget(rel_path=rel_path, symbol=alias.name)
    return targets


def _module_import_targets(ctx: ToolContext, tree: ast.AST, source_path: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                rel_path = _resolve_import_module_path(source_path, alias.name, 0)
                if rel_path is None:
                    continue
                if alias.asname:
                    targets[alias.asname] = rel_path
                elif "." not in alias.name:
                    targets[alias.name] = rel_path
        elif isinstance(node, ast.ImportFrom):
            base_parts = _resolve_import_module_parts(source_path, node.module, node.level)
            if not base_parts:
                continue
            base_path = _safe_source_path(Path(*base_parts).as_posix())
            if base_path is None or not (ctx.root / base_path).is_dir():
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                rel_path = _safe_source_path(Path(*base_parts, *alias.name.split(".")).with_suffix(".py").as_posix())
                if rel_path is None:
                    continue
                targets[alias.asname or alias.name] = rel_path
    return targets


def _resolve_import_module_path(source_path: str, module: str | None, level: int) -> str | None:
    parts = _resolve_import_module_parts(source_path, module, level)
    if not parts:
        return None
    return _safe_source_path(Path(*parts).with_suffix(".py").as_posix())


def _resolve_import_module_parts(source_path: str, module: str | None, level: int) -> list[str] | None:
    module_parts = [part for part in (module or "").split(".") if part]
    if level <= 0:
        parts = module_parts
    else:
        parent_parts = list(Path(source_path).parent.parts)
        ascend = level - 1
        if ascend > len(parent_parts):
            return None
        parts = parent_parts[: len(parent_parts) - ascend] + module_parts
    if not parts:
        return None
    return parts


def _import_target_is_project_local(ctx: ToolContext, rel_path: str) -> bool:
    safe = _safe_source_path(rel_path)
    if safe is None:
        return False
    parts = Path(safe).parts
    if not parts:
        return False
    top = (ctx.root / parts[0]).resolve()
    root = ctx.root.resolve()
    if root not in top.parents and top != root:
        return False
    return top.exists()
