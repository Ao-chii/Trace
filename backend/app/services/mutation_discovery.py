from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

from app.schemas.evaluation import (
    MutantMatcherContract,
    MutantPatchContract,
    MutantSelectionContract,
    MutationCandidateContract,
    MutationDiscoveryExclusionContract,
    MutationDiscoveryResultContract,
)


_IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
_COMPARE_MUTATIONS = {
    ast.LtE: ("<=", "<", "comparison_boundary"),
    ast.Lt: ("<", "<=", "comparison_boundary"),
    ast.GtE: (">=", ">", "comparison_boundary"),
    ast.Gt: (">", ">=", "comparison_boundary"),
    ast.Eq: ("==", "!=", "comparison_negation"),
    ast.NotEq: ("!=", "==", "comparison_negation"),
}
_ARITHMETIC_MUTATIONS = {
    ast.Add: ("+", "-"),
    ast.Sub: ("-", "+"),
}
_BOOLEAN_FUNCTION_PREFIXES = ("is_", "has_", "can_", "should_", "needs_", "supports_", "allows_")


def discover_mutation_candidates(
    *,
    root: Path | str,
    eval_task_id: str,
    source_snapshot_id: str,
    target_scope: dict[str, Any] | list[Any] | None = None,
    sample_seed: int = 0,
    max_selected: int = 20,
) -> MutationDiscoveryResultContract:
    """Dry-run auto mutation discovery. It never writes variants or runs tests."""
    project_root = Path(root).resolve()
    targets = _target_names(target_scope)
    seen_targets: set[str] = set()
    raw_candidates: list[MutationCandidateContract] = []
    exclusions: list[MutationDiscoveryExclusionContract] = []

    for path in _python_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            exclusions.append(
                MutationDiscoveryExclusionContract(
                    reason_code="syntax_error",
                    message=str(exc),
                    source_path=rel,
                    line=exc.lineno,
                )
            )
            continue

        for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if targets and function.name not in targets:
                continue
            seen_targets.add(function.name)
            raw_candidates.extend(
                _function_compare_candidates(
                    text=text,
                    rel_path=rel,
                    function=function,
                    eval_task_id=eval_task_id,
                    source_snapshot_id=source_snapshot_id,
                    exclusions=exclusions,
                )
            )
            raw_candidates.extend(
                _function_arithmetic_candidates(
                    text=text,
                    rel_path=rel,
                    function=function,
                    eval_task_id=eval_task_id,
                    source_snapshot_id=source_snapshot_id,
                    exclusions=exclusions,
                )
            )
            raw_candidates.extend(
                _function_boolean_candidates(
                    text=text,
                    rel_path=rel,
                    function=function,
                    eval_task_id=eval_task_id,
                    source_snapshot_id=source_snapshot_id,
                    exclusions=exclusions,
                )
            )
            raw_candidates.extend(
                _function_constant_candidates(
                    text=text,
                    rel_path=rel,
                    function=function,
                    eval_task_id=eval_task_id,
                    source_snapshot_id=source_snapshot_id,
                    exclusions=exclusions,
                )
            )
            raw_candidates.extend(
                _function_return_value_candidates(
                    text=text,
                    rel_path=rel,
                    function=function,
                    eval_task_id=eval_task_id,
                    source_snapshot_id=source_snapshot_id,
                    exclusions=exclusions,
                )
            )

    for target in sorted(targets - seen_targets):
        exclusions.append(
            MutationDiscoveryExclusionContract(
                reason_code="target_not_found",
                message=f"target function was not found: {target}",
                target_ref=target,
            )
        )

    candidates = _apply_deterministic_selection(raw_candidates, sample_seed=sample_seed, max_selected=max_selected)
    return MutationDiscoveryResultContract(
        eval_task_id=eval_task_id,
        source_snapshot_id=source_snapshot_id,
        sample_seed=sample_seed,
        max_selected=max_selected,
        candidates=candidates,
        exclusions=exclusions,
        selected_count=sum(1 for candidate in candidates if candidate.selection.status == "selected"),
        excluded_count=len(exclusions),
    )


def _function_compare_candidates(
    *,
    text: str,
    rel_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    eval_task_id: str,
    source_snapshot_id: str,
    exclusions: list[MutationDiscoveryExclusionContract],
) -> list[MutationCandidateContract]:
    candidates: list[MutationCandidateContract] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare):
            continue
        if len(node.ops) != 1 or len(node.comparators) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="unsupported_compare",
                    message="only single-operator comparisons are supported",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        op_type = type(node.ops[0])
        if op_type not in _COMPARE_MUTATIONS:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="unsupported_compare",
                    message=f"unsupported comparison operator: {op_type.__name__}",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        source_segment = ast.get_source_segment(text, node)
        if not source_segment:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="source_segment_unavailable",
                    message="could not recover exact source segment for comparison",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        if text.count(source_segment) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="non_unique_patch",
                    message="comparison source segment is not unique in file",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue

        _, replacement_op, operator = _COMPARE_MUTATIONS[op_type]
        replacement = f"{ast.unparse(node.left)} {replacement_op} {ast.unparse(node.comparators[0])}"
        candidate_id = _candidate_id(
            source_snapshot_id=source_snapshot_id,
            rel_path=rel_path,
            line=node.lineno,
            old=source_segment,
            new=replacement,
        )
        candidates.append(
            MutationCandidateContract(
                candidate_id=candidate_id,
                eval_task_id=eval_task_id,
                source_snapshot_id=source_snapshot_id,
                operator=operator,
                patch=MutantPatchContract(file=rel_path, old=source_segment, new=replacement),
                matcher=MutantMatcherContract(
                    matcher_kind="source_location_hash",
                    source_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    original_content_hash=_sha256(source_segment),
                    operator=operator,
                    target_symbol=function.name,
                    context_hash=_sha256(_line_range_text(text, node.lineno, getattr(node, "end_lineno", node.lineno))),
                ),
                selection=MutantSelectionContract(
                    status="not_selected",
                    selected_by="auto_sampler",
                    reason="candidate discovered but not selected yet",
                ),
            )
        )
    return candidates


def _function_arithmetic_candidates(
    *,
    text: str,
    rel_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    eval_task_id: str,
    source_snapshot_id: str,
    exclusions: list[MutationDiscoveryExclusionContract],
) -> list[MutationCandidateContract]:
    candidates: list[MutationCandidateContract] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.BinOp):
            continue
        op_type = type(node.op)
        if op_type not in _ARITHMETIC_MUTATIONS:
            continue
        source_segment = ast.get_source_segment(text, node)
        if not source_segment:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="source_segment_unavailable",
                    message="could not recover exact source segment for arithmetic expression",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        if text.count(source_segment) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="non_unique_patch",
                    message="arithmetic source segment is not unique in file",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue

        _, replacement_op = _ARITHMETIC_MUTATIONS[op_type]
        replacement = f"{ast.unparse(node.left)} {replacement_op} {ast.unparse(node.right)}"
        operator = "arithmetic_operator"
        candidate_id = _candidate_id(
            source_snapshot_id=source_snapshot_id,
            rel_path=rel_path,
            line=node.lineno,
            old=source_segment,
            new=replacement,
        )
        candidates.append(
            MutationCandidateContract(
                candidate_id=candidate_id,
                eval_task_id=eval_task_id,
                source_snapshot_id=source_snapshot_id,
                operator=operator,
                patch=MutantPatchContract(file=rel_path, old=source_segment, new=replacement),
                matcher=MutantMatcherContract(
                    matcher_kind="source_location_hash",
                    source_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    original_content_hash=_sha256(source_segment),
                    operator=operator,
                    target_symbol=function.name,
                    context_hash=_sha256(_line_range_text(text, node.lineno, getattr(node, "end_lineno", node.lineno))),
                ),
                selection=MutantSelectionContract(
                    status="not_selected",
                    selected_by="auto_sampler",
                    reason="candidate discovered but not selected yet",
                ),
            )
        )
    return candidates


def _function_boolean_candidates(
    *,
    text: str,
    rel_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    eval_task_id: str,
    source_snapshot_id: str,
    exclusions: list[MutationDiscoveryExclusionContract],
) -> list[MutationCandidateContract]:
    candidates: list[MutationCandidateContract] = []
    if not _function_has_boolean_evidence(function):
        return candidates
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Name):
            continue
        source_segment = ast.get_source_segment(text, node)
        if not source_segment:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="source_segment_unavailable",
                    message="could not recover exact source segment for boolean return",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        if "\n" in source_segment or text.count(source_segment) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="non_unique_patch",
                    message="boolean return source segment is not unique in file",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue

        replacement = f"return not {node.value.id}"
        operator = "boolean_negation"
        candidate_id = _candidate_id(
            source_snapshot_id=source_snapshot_id,
            rel_path=rel_path,
            line=node.lineno,
            old=source_segment,
            new=replacement,
        )
        candidates.append(
            MutationCandidateContract(
                candidate_id=candidate_id,
                eval_task_id=eval_task_id,
                source_snapshot_id=source_snapshot_id,
                operator=operator,
                patch=MutantPatchContract(file=rel_path, old=source_segment, new=replacement),
                matcher=MutantMatcherContract(
                    matcher_kind="source_location_hash",
                    source_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    original_content_hash=_sha256(source_segment),
                    operator=operator,
                    target_symbol=function.name,
                    context_hash=_sha256(_line_range_text(text, node.lineno, getattr(node, "end_lineno", node.lineno))),
                ),
                selection=MutantSelectionContract(
                    status="not_selected",
                    selected_by="auto_sampler",
                    reason="candidate discovered but not selected yet",
                ),
            )
        )
    return candidates


def _function_constant_candidates(
    *,
    text: str,
    rel_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    eval_task_id: str,
    source_snapshot_id: str,
    exclusions: list[MutationDiscoveryExclusionContract],
) -> list[MutationCandidateContract]:
    candidates: list[MutationCandidateContract] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Return):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, bool):
            continue
        source_segment = ast.get_source_segment(text, node)
        if not source_segment:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="source_segment_unavailable",
                    message="could not recover exact source segment for boolean constant return",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        if "\n" in source_segment or text.count(source_segment) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="non_unique_patch",
                    message="boolean constant return source segment is not unique in file",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue

        replacement = f"return {not node.value.value}"
        operator = "constant_replacement"
        candidate_id = _candidate_id(
            source_snapshot_id=source_snapshot_id,
            rel_path=rel_path,
            line=node.lineno,
            old=source_segment,
            new=replacement,
        )
        candidates.append(
            MutationCandidateContract(
                candidate_id=candidate_id,
                eval_task_id=eval_task_id,
                source_snapshot_id=source_snapshot_id,
                operator=operator,
                patch=MutantPatchContract(file=rel_path, old=source_segment, new=replacement),
                matcher=MutantMatcherContract(
                    matcher_kind="source_location_hash",
                    source_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    original_content_hash=_sha256(source_segment),
                    operator=operator,
                    target_symbol=function.name,
                    context_hash=_sha256(_line_range_text(text, node.lineno, getattr(node, "end_lineno", node.lineno))),
                ),
                selection=MutantSelectionContract(
                    status="not_selected",
                    selected_by="auto_sampler",
                    reason="candidate discovered but not selected yet",
                ),
            )
        )
    return candidates


def _function_return_value_candidates(
    *,
    text: str,
    rel_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    eval_task_id: str,
    source_snapshot_id: str,
    exclusions: list[MutationDiscoveryExclusionContract],
) -> list[MutationCandidateContract]:
    candidates: list[MutationCandidateContract] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Return):
            continue
        if not isinstance(node.value, ast.Constant) or node.value.value is not None:
            continue
        source_segment = ast.get_source_segment(text, node)
        if not source_segment:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="source_segment_unavailable",
                    message="could not recover exact source segment for None return",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue
        if "\n" in source_segment or text.count(source_segment) != 1:
            exclusions.append(
                _mutation_exclusion(
                    reason_code="non_unique_patch",
                    message="None return source segment is not unique in file",
                    rel_path=rel_path,
                    node=node,
                    target_ref=function.name,
                )
            )
            continue

        replacement = "return False"
        operator = "return_value"
        candidate_id = _candidate_id(
            source_snapshot_id=source_snapshot_id,
            rel_path=rel_path,
            line=node.lineno,
            old=source_segment,
            new=replacement,
        )
        candidates.append(
            MutationCandidateContract(
                candidate_id=candidate_id,
                eval_task_id=eval_task_id,
                source_snapshot_id=source_snapshot_id,
                operator=operator,
                patch=MutantPatchContract(file=rel_path, old=source_segment, new=replacement),
                matcher=MutantMatcherContract(
                    matcher_kind="source_location_hash",
                    source_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    original_content_hash=_sha256(source_segment),
                    operator=operator,
                    target_symbol=function.name,
                    context_hash=_sha256(_line_range_text(text, node.lineno, getattr(node, "end_lineno", node.lineno))),
                ),
                selection=MutantSelectionContract(
                    status="not_selected",
                    selected_by="auto_sampler",
                    reason="candidate discovered but not selected yet",
                ),
            )
        )
    return candidates


def _function_has_boolean_evidence(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = function.returns
    if isinstance(annotation, ast.Name) and annotation.id == "bool":
        return True
    if isinstance(annotation, ast.Attribute) and annotation.attr == "bool":
        return True
    return function.name.lower().startswith(_BOOLEAN_FUNCTION_PREFIXES)


def _apply_deterministic_selection(
    candidates: list[MutationCandidateContract],
    *,
    sample_seed: int,
    max_selected: int,
) -> list[MutationCandidateContract]:
    ranked = sorted(candidates, key=lambda candidate: _sample_key(candidate.candidate_id, sample_seed))
    selected_ids = {candidate.candidate_id for candidate in ranked[:max_selected]}
    selected_index = 0
    result: list[MutationCandidateContract] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        if candidate.candidate_id in selected_ids:
            selection = MutantSelectionContract(
                status="selected",
                selected_by="auto_sampler",
                reason="selected by deterministic dry-run sample",
                sample_seed=sample_seed,
                sample_index=selected_index,
            )
            selected_index += 1
        else:
            selection = MutantSelectionContract(
                status="not_selected",
                selected_by="auto_sampler",
                reason="outside deterministic dry-run sample budget",
            )
        result.append(candidate.model_copy(update={"selection": selection}))
    return result


def _target_names(target_scope: dict[str, Any] | list[Any] | None) -> set[str]:
    raw_targets: list[Any] = []
    if isinstance(target_scope, dict):
        values = target_scope.get("targets")
        raw_targets = values if isinstance(values, list) else []
    elif isinstance(target_scope, list):
        raw_targets = target_scope
    names: set[str] = set()
    for raw in raw_targets:
        value = str(raw).strip()
        if not value or value.startswith("/") or value.lower().startswith("route:"):
            continue
        if " " in value and value.split(maxsplit=1)[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
            continue
        names.add(value.rsplit(".", 1)[-1])
    return names


def _python_source_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(part in _IGNORE_DIRS for part in path.parts):
            continue
        if rel.startswith("tests/") or "/tests/" in rel or path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        files.append(path)
    return sorted(files)


def _mutation_exclusion(
    *,
    reason_code: str,
    message: str,
    rel_path: str,
    node: ast.AST,
    target_ref: str,
) -> MutationDiscoveryExclusionContract:
    return MutationDiscoveryExclusionContract(
        reason_code=reason_code,
        message=message,
        target_ref=target_ref,
        source_path=rel_path,
        line=getattr(node, "lineno", None),
    )


def _candidate_id(*, source_snapshot_id: str, rel_path: str, line: int, old: str, new: str) -> str:
    digest = _sha256(f"{source_snapshot_id}\0{rel_path}\0{line}\0{old}\0{new}")
    return f"mut-{digest[:24]}"


def _sample_key(candidate_id: str, sample_seed: int) -> str:
    return _sha256(f"{sample_seed}\0{candidate_id}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _line_range_text(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])
