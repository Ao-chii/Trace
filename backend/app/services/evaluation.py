from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.models.evaluation import BugVariant, EvalDataset, EvalTask, SeededBug
from app.models.project import ProjectSnapshot
from app.repositories.evaluation import (
    add_bug_variant,
    add_dataset,
    add_eval_task,
    add_seeded_bug,
    get_dataset,
    get_dataset_by_name_version,
    get_bug_variant,
    get_eval_task,
    get_seeded_bug,
    list_datasets as repo_list_datasets,
    list_seeded_bugs_for_task,
    list_seeded_bugs_for_tasks,
    list_tasks_for_dataset,
    list_variants_for_bug,
    list_variants_for_bugs,
)
from app.schemas.api_evaluation import BugVariantOut, EvalDatasetDetailOut, EvalTaskDetailOut, SeededBugDetailOut
from app.schemas.evaluation import (
    BugVariantGroundTruthContract,
    MutationCandidateContract,
    MutationDiscoveryAuditReportContract,
    MutationDiscoveryResultContract,
)
from app.services.mutation_discovery import discover_mutation_candidates
from eval.harness.probes import ProbeCheckError, check_variant_probe


class EvaluationError(ValueError):
    status_code = 400


class EvaluationNotFoundError(EvaluationError):
    status_code = 404


class EvaluationConflictError(EvaluationError):
    status_code = 409


def _sha256_json(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _snapshot_or_error(session: Session, snapshot_id: str) -> ProjectSnapshot:
    snapshot = session.get(ProjectSnapshot, snapshot_id)
    if snapshot is None:
        raise EvaluationNotFoundError("project snapshot not found")
    return snapshot


def _project_file_path(root: str | Path, file: str) -> Path:
    root = Path(root).resolve()
    target = (root / file).resolve()
    if root not in target.parents and target != root:
        raise EvaluationError("patch file escapes project snapshot root")
    if not target.is_file():
        raise EvaluationError("patch target file not found")
    return target


def count_canonical_hits(root: str | Path, file: str, old: str) -> tuple[str, int]:
    text = _project_file_path(root, file).read_text(encoding="utf-8")
    return text, text.count(old)


def _validate_patch_unique_hit(snapshot: ProjectSnapshot, *, file: str, old: str) -> str:
    text, count = count_canonical_hits(snapshot.root_path, file, old)
    if count != 1:
        raise EvaluationError(f"canonical patch must hit exactly once, got {count}")
    return text


def _canonical_patch_payload(*, file: str, old: str, new: str) -> dict:
    return {"file": file, "old": old, "new": new}


def _patched_text(clean_text: str, *, old: str, new: str) -> str:
    return clean_text.replace(old, new, 1)


def _validate_mutated_snapshot_matches_patch(
    mutated_snapshot: ProjectSnapshot,
    *,
    file: str,
    clean_text: str,
    old: str,
    new: str,
) -> None:
    mutated_text = _project_file_path(mutated_snapshot.root_path, file).read_text(encoding="utf-8")
    expected_text = _patched_text(clean_text, old=old, new=new)
    if mutated_text != expected_text:
        raise EvaluationError("mutated snapshot does not match canonical patch")


def inline_patch_payload(*, file: str, old: str, new: str) -> dict:
    patch = _canonical_patch_payload(file=file, old=old, new=new)
    content_hash = _sha256_json(patch)
    content_id = f"patch-{content_hash[:32]}"
    size_bytes = len(json.dumps(patch, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "content_id": content_id,
        "artifact_type": "bug_patch",
        "storage": "inline",
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "metadata": {
            "kind": "bug_patch",
            "created_by": "seed_script",
            "content_role": "inline_canonical_patch",
            "relative_paths": [file],
            "hash_algorithm": "sha256",
        },
        "patch": patch,
    }


def create_eval_dataset(
    session: Session,
    *,
    name: str,
    version: str,
    description: str | None = None,
    project_snapshot_ids: list[str] | None = None,
    dataset_id: str | None = None,
) -> EvalDataset:
    if get_dataset_by_name_version(session, name=name, version=version) is not None:
        raise EvaluationConflictError("dataset name/version already exists")
    if dataset_id is not None and get_dataset(session, dataset_id) is not None:
        raise EvaluationConflictError("dataset id already exists")
    snapshot_ids = project_snapshot_ids or []
    for snapshot_id in snapshot_ids:
        _snapshot_or_error(session, snapshot_id)
    record = EvalDataset(
        id=dataset_id or f"dataset-{new_id()}",
        name=name,
        version=version,
        description=description,
        project_snapshot_ids=snapshot_ids,
    )
    return add_dataset(session, record)


def list_eval_datasets(session: Session) -> list[EvalDataset]:
    return repo_list_datasets(session)


def get_dataset_or_404(session: Session, dataset_id: str) -> EvalDataset:
    dataset = get_dataset(session, dataset_id)
    if dataset is None:
        raise EvaluationNotFoundError("dataset not found")
    return dataset


def create_eval_task(
    session: Session,
    *,
    dataset_id: str,
    project_snapshot_id: str,
    target_scope: dict | None = None,
    goal: str,
    expected_capabilities: list | None = None,
    task_id: str | None = None,
) -> EvalTask:
    dataset = get_dataset_or_404(session, dataset_id)
    snapshot = _snapshot_or_error(session, project_snapshot_id)
    if task_id is not None and get_eval_task(session, task_id) is not None:
        raise EvaluationConflictError("eval task id already exists")
    if dataset.project_snapshot_ids and snapshot.id not in dataset.project_snapshot_ids:
        raise EvaluationError("project snapshot is not part of dataset")
    record = EvalTask(
        id=task_id or f"task-{new_id()}",
        dataset_id=dataset.id,
        project_snapshot_id=snapshot.id,
        target_scope=target_scope or {},
        goal=goal,
        expected_capabilities=expected_capabilities or [],
    )
    return add_eval_task(session, record)


def list_dataset_tasks(session: Session, dataset_id: str) -> list[EvalTask]:
    get_dataset_or_404(session, dataset_id)
    return list_tasks_for_dataset(session, dataset_id)


def get_eval_task_or_404(session: Session, task_id: str) -> EvalTask:
    task = get_eval_task(session, task_id)
    if task is None:
        raise EvaluationNotFoundError("eval task not found")
    return task


def create_seeded_bug(
    session: Session,
    *,
    eval_task_id: str,
    bug_type: str,
    description: str,
    expected_detection: str,
    bug_id: str | None = None,
) -> SeededBug:
    task = get_eval_task_or_404(session, eval_task_id)
    if bug_id is not None and get_seeded_bug(session, bug_id) is not None:
        raise EvaluationConflictError("seeded bug id already exists")
    record = SeededBug(
        id=bug_id or f"bug-{new_id()}",
        eval_task_id=task.id,
        bug_type=bug_type,
        description=description,
        expected_detection=expected_detection,
    )
    return add_seeded_bug(session, record)


def list_task_seeded_bugs(session: Session, task_id: str) -> list[SeededBug]:
    get_eval_task_or_404(session, task_id)
    return list_seeded_bugs_for_task(session, task_id)


def dry_run_task_mutation_discovery(
    session: Session,
    task_id: str,
    *,
    sample_seed: int = 0,
    max_selected: int = 20,
    target_scope_override: dict | list | None = None,
) -> MutationDiscoveryResultContract:
    task = get_eval_task_or_404(session, task_id)
    snapshot = _snapshot_or_error(session, task.project_snapshot_id)
    return discover_mutation_candidates(
        root=Path(snapshot.root_path),
        eval_task_id=task.id,
        source_snapshot_id=snapshot.id,
        target_scope=target_scope_override if target_scope_override is not None else task.target_scope,
        sample_seed=sample_seed,
        max_selected=max_selected,
    )


def build_task_mutation_discovery_audit_report(
    session: Session,
    task_id: str,
    *,
    sample_seed: int = 0,
    max_selected: int = 20,
    target_scope_override: dict | list | None = None,
) -> MutationDiscoveryAuditReportContract:
    task = get_eval_task_or_404(session, task_id)
    discovery = dry_run_task_mutation_discovery(
        session,
        task_id,
        sample_seed=sample_seed,
        max_selected=max_selected,
        target_scope_override=target_scope_override,
    )
    selected_candidate_ids = [
        candidate.candidate_id
        for candidate in discovery.candidates
        if candidate.selection.status == "selected"
    ]
    exclusion_summary: dict[str, int] = {}
    for exclusion in discovery.exclusions:
        exclusion_summary[exclusion.reason_code] = exclusion_summary.get(exclusion.reason_code, 0) + 1
    return MutationDiscoveryAuditReportContract(
        generated_at=datetime.now(timezone.utc).isoformat(),
        eval_task_id=task.id,
        dataset_id=task.dataset_id,
        source_snapshot_id=task.project_snapshot_id,
        target_scope=target_scope_override if target_scope_override is not None else task.target_scope,
        sample_seed=sample_seed,
        max_selected=max_selected,
        selected_candidate_ids=selected_candidate_ids,
        exclusion_summary=exclusion_summary,
        discovery=discovery,
    )


def confirm_selected_mutation_candidate(
    session: Session,
    task_id: str,
    *,
    audit_report: MutationDiscoveryAuditReportContract,
    candidate_id: str,
    probe: dict[str, Any],
    seeded_bug_id: str | None = None,
    variant_id: str | None = None,
    bug_type: str = "auto_mutation",
    description: str | None = None,
    expected_detection: str | None = None,
    variant_name: str | None = None,
) -> SeededBugDetailOut:
    task = get_eval_task_or_404(session, task_id)
    snapshot = _snapshot_or_error(session, task.project_snapshot_id)
    if audit_report.eval_task_id != task.id:
        raise EvaluationError("audit report eval_task_id does not match task")
    if audit_report.dataset_id != task.dataset_id:
        raise EvaluationError("audit report dataset_id does not match task")
    if audit_report.source_snapshot_id != snapshot.id:
        raise EvaluationError("audit report source_snapshot_id does not match task snapshot")
    if bug_type != "auto_mutation":
        raise EvaluationError("auto mutation confirmation requires bug_type=auto_mutation")

    reported_candidate = _selected_candidate_from_audit(audit_report, candidate_id)
    if reported_candidate.eval_task_id != task.id or reported_candidate.source_snapshot_id != snapshot.id:
        raise EvaluationError("mutation candidate does not match task or snapshot")
    candidate = _selected_candidate_from_current_discovery(
        session,
        task,
        audit_report=audit_report,
        candidate_id=candidate_id,
    )
    if reported_candidate.model_dump(mode="json") != candidate.model_dump(mode="json"):
        raise EvaluationError("audit report candidate does not match current server discovery")

    bug_id = seeded_bug_id or f"bug-{candidate.candidate_id}"
    var_id = variant_id or f"variant-{candidate.candidate_id}"
    if get_seeded_bug(session, bug_id) is not None:
        raise EvaluationConflictError("seeded bug id already exists")
    if get_bug_variant(session, var_id) is not None:
        raise EvaluationConflictError("bug variant id already exists")

    _validate_patch_unique_hit(snapshot, file=candidate.patch.file, old=candidate.patch.old)
    target = candidate.matcher.target_symbol or candidate.patch.file
    probe_payload = {
        "target_kind": probe["target_kind"],
        "probe": probe["probe"],
        "clean_value": probe["clean_value"],
        "buggy_value": probe["buggy_value"],
    }
    probe_ground_truth = {
        **probe_payload,
        "patch_artifact": {"patch": candidate.patch.model_dump(mode="json")},
    }
    probe_check = _check_auto_mutation_probe(
        snapshot=snapshot,
        candidate=candidate,
        probe_ground_truth=probe_ground_truth,
        variant_id=var_id,
    )
    bug = create_seeded_bug(
        session,
        eval_task_id=task.id,
        bug_id=bug_id,
        bug_type=bug_type,
        description=description or f"Auto mutation {candidate.operator} in {candidate.patch.file}",
        expected_detection=expected_detection or f"Generated tests should detect selected mutation {candidate.candidate_id}",
    )
    ground_truth = BugVariantGroundTruthContract(
        source="auto_mutation",
        target=target,
        mutation=candidate,
        probe=probe_payload | {"probe_check": probe_check},
    ).model_dump(mode="json", exclude_none=True)
    variant = create_bug_variant(
        session,
        seeded_bug_id=bug.id,
        variant_id=var_id,
        variant_name=variant_name or f"auto mutation {candidate.candidate_id}",
        patch=candidate.patch.model_dump(mode="json"),
        ground_truth=ground_truth,
        allow_auto_mutation=True,
    )
    return SeededBugDetailOut.model_validate(bug).model_copy(
        update={"variants": [BugVariantOut.model_validate(variant)]}
    )


def _selected_candidate_from_audit(
    audit_report: MutationDiscoveryAuditReportContract,
    candidate_id: str,
) -> MutationCandidateContract:
    if candidate_id not in audit_report.selected_candidate_ids:
        raise EvaluationError("candidate is not selected in audit report")
    for candidate in audit_report.discovery.candidates:
        if candidate.candidate_id == candidate_id:
            if candidate.selection.status != "selected":
                raise EvaluationError("candidate selection status is not selected")
            return candidate
    raise EvaluationError("candidate not found in audit report")


def _selected_candidate_from_current_discovery(
    session: Session,
    task: EvalTask,
    *,
    audit_report: MutationDiscoveryAuditReportContract,
    candidate_id: str,
) -> MutationCandidateContract:
    discovery = dry_run_task_mutation_discovery(
        session,
        task.id,
        sample_seed=audit_report.sample_seed,
        max_selected=audit_report.max_selected,
        target_scope_override=audit_report.target_scope,
    )
    for candidate in discovery.candidates:
        if candidate.candidate_id != candidate_id:
            continue
        if candidate.selection.status != "selected":
            raise EvaluationError("audit report candidate is not selected by current server discovery")
        return candidate
    raise EvaluationError("audit report candidate is not present in current server discovery")


def _check_auto_mutation_probe(
    *,
    snapshot: ProjectSnapshot,
    candidate: MutationCandidateContract,
    probe_ground_truth: dict,
    variant_id: str,
) -> dict:
    clean_root = Path(snapshot.root_path).resolve()
    with tempfile.TemporaryDirectory(prefix="trace-mut-probe-") as tmpdir:
        variant_root = Path(tmpdir) / "variant"
        shutil.copytree(
            clean_root,
            variant_root,
            ignore=shutil.ignore_patterns(
                ".git",
                ".trace_artifacts",
                ".pytest_cache",
                "__pycache__",
            ),
        )
        _apply_candidate_patch(variant_root, candidate)
        try:
            result = check_variant_probe(
                clean_root=clean_root,
                variant_root=variant_root,
                ground_truth=probe_ground_truth,
                variant_id=variant_id,
            )
        except ProbeCheckError as exc:
            raise EvaluationError(str(exc)) from exc
    if result is None:
        raise EvaluationError("auto mutation confirmation requires probe metadata")
    return result


def _apply_candidate_patch(root: Path, candidate: MutationCandidateContract) -> None:
    target = (root / candidate.patch.file).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise EvaluationError("mutation patch target escapes variant root")
    text = target.read_text(encoding="utf-8")
    if text.count(candidate.patch.old) != 1:
        raise EvaluationError("mutation patch must hit exactly once during probe check")
    target.write_text(text.replace(candidate.patch.old, candidate.patch.new, 1), encoding="utf-8")


def get_seeded_bug_or_404(session: Session, bug_id: str) -> SeededBug:
    bug = get_seeded_bug(session, bug_id)
    if bug is None:
        raise EvaluationNotFoundError("seeded bug not found")
    return bug


def create_bug_variant(
    session: Session,
    *,
    seeded_bug_id: str,
    variant_name: str,
    patch: dict,
    canonical_kind: str = "patch",
    mutated_snapshot_id: str | None = None,
    ground_truth: dict | None = None,
    variant_id: str | None = None,
    allow_auto_mutation: bool = False,
) -> BugVariant:
    if canonical_kind != "patch":
        raise EvaluationError("V2.1 only supports canonical_kind=patch")
    if (ground_truth or {}).get("source") == "auto_mutation" and not allow_auto_mutation:
        raise EvaluationError("auto mutation variants must be created through mutation confirmation")
    if variant_id is not None and get_bug_variant(session, variant_id) is not None:
        raise EvaluationConflictError("bug variant id already exists")
    bug = get_seeded_bug_or_404(session, seeded_bug_id)
    task = get_eval_task_or_404(session, bug.eval_task_id)
    clean_snapshot = _snapshot_or_error(session, task.project_snapshot_id)
    mutated_snapshot = None
    if mutated_snapshot_id is not None:
        mutated_snapshot = _snapshot_or_error(session, mutated_snapshot_id)

    file = patch["file"]
    old = patch["old"]
    new = patch["new"]
    clean_text = _validate_patch_unique_hit(clean_snapshot, file=file, old=old)
    if mutated_snapshot is not None:
        _validate_mutated_snapshot_matches_patch(
            mutated_snapshot,
            file=file,
            clean_text=clean_text,
            old=old,
            new=new,
        )

    patch_artifact = inline_patch_payload(file=file, old=old, new=new)
    merged_ground_truth = dict(ground_truth or {})
    merged_ground_truth["patch_artifact"] = patch_artifact
    merged_ground_truth["patch_unique_hit"] = {
        "project_snapshot_id": clean_snapshot.id,
        "file": file,
        "hit_count": 1,
    }
    if mutated_snapshot is not None:
        merged_ground_truth["mutated_snapshot_verification"] = {
            "mutated_snapshot_id": mutated_snapshot.id,
            "matches_canonical_patch": True,
        }

    record = BugVariant(
        id=variant_id or f"variant-{new_id()}",
        seeded_bug_id=bug.id,
        variant_name=variant_name,
        canonical_kind=canonical_kind,
        patch_artifact_id=None,
        mutated_snapshot_id=mutated_snapshot_id,
        ground_truth=merged_ground_truth,
    )
    return add_bug_variant(session, record)


def list_bug_variants(session: Session, seeded_bug_id: str) -> list[BugVariant]:
    get_seeded_bug_or_404(session, seeded_bug_id)
    return list_variants_for_bug(session, seeded_bug_id)


def get_dataset_detail(session: Session, dataset_id: str) -> EvalDatasetDetailOut:
    dataset = get_dataset_or_404(session, dataset_id)
    tasks = list_tasks_for_dataset(session, dataset.id)
    bugs = list_seeded_bugs_for_tasks(session, [task.id for task in tasks])
    variants = list_variants_for_bugs(session, [bug.id for bug in bugs])

    variants_by_bug_id = defaultdict(list)
    for variant in variants:
        variants_by_bug_id[variant.seeded_bug_id].append(BugVariantOut.model_validate(variant))

    bugs_by_task_id = defaultdict(list)
    for bug in bugs:
        bug_out = SeededBugDetailOut.model_validate(bug).model_copy(
            update={"variants": variants_by_bug_id[bug.id]}
        )
        bugs_by_task_id[bug.eval_task_id].append(bug_out)

    task_outs = [
        EvalTaskDetailOut.model_validate(task).model_copy(update={"seeded_bugs": bugs_by_task_id[task.id]})
        for task in tasks
    ]
    return EvalDatasetDetailOut.model_validate(dataset).model_copy(update={"tasks": task_outs})
