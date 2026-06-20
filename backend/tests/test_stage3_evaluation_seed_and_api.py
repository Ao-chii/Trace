from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.main import create_app
from app.models.evaluation import BugVariant, EvalDataset, SeededBug
from app.services.evaluation import build_task_mutation_discovery_audit_report
from app.services.evaluation_seed import DEMO_DATASET_ID, seed_demo_dataset


def _patch_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _create_project_and_snapshot(client: TestClient, project_root) -> dict:
    project = client.post("/api/v1/projects", json={"name": "eval demo", "local_path": str(project_root)}).json()
    response = client.post(f"/api/v1/projects/{project['id']}/snapshots", json={"root_path": str(project_root)})
    assert response.status_code == 200
    return response.json()


def test_eval_dataset_task_bug_variant_api_enforces_patch_unique_hit(tmp_path, clean_db):
    app = create_app()
    project_root = tmp_path / "proj"
    package = project_root / "shop"
    package.mkdir(parents=True)
    (package / "pricing.py").write_text(
        "def shipping_fee(weight_kg):\n"
        "    if weight_kg <= 5:\n"
        "        return 10\n"
        "    return 20\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        snapshot = _create_project_and_snapshot(client, project_root)

        dataset_response = client.post(
            "/api/v1/eval-datasets",
            json={
                "id": "dataset-api-v2",
                "name": "api dataset",
                "version": "v2",
                "project_snapshot_ids": [snapshot["id"]],
            },
        )
        assert dataset_response.status_code == 200
        dataset = dataset_response.json()

        duplicate = client.post(
            "/api/v1/eval-datasets",
            json={"name": "api dataset", "version": "v2", "project_snapshot_ids": [snapshot["id"]]},
        )
        assert duplicate.status_code == 409

        duplicate_id = client.post(
            "/api/v1/eval-datasets",
            json={
                "id": dataset["id"],
                "name": "api dataset other name",
                "version": "v3",
                "project_snapshot_ids": [snapshot["id"]],
            },
        )
        assert duplicate_id.status_code == 409

        task = client.post(
            f"/api/v1/eval-datasets/{dataset['id']}/tasks",
            json={
                "id": "task-api-v2",
                "project_snapshot_id": snapshot["id"],
                "target_scope": {"targets": ["shipping_fee"]},
                "goal": "cover shipping boundary",
                "expected_capabilities": ["boundary_value"],
            },
        ).json()
        duplicate_task_id = client.post(
            f"/api/v1/eval-datasets/{dataset['id']}/tasks",
            json={
                "id": task["id"],
                "project_snapshot_id": snapshot["id"],
                "goal": "duplicate task id",
            },
        )
        assert duplicate_task_id.status_code == 409

        bug = client.post(
            f"/api/v1/eval-tasks/{task['id']}/seeded-bugs",
            json={
                "id": "bug-shipping-boundary",
                "bug_type": "boundary",
                "description": "5kg boundary is wrong",
                "expected_detection": "assert shipping_fee(5) == 10",
            },
        ).json()
        duplicate_bug_id = client.post(
            f"/api/v1/eval-tasks/{task['id']}/seeded-bugs",
            json={
                "id": bug["id"],
                "bug_type": "boundary",
                "description": "duplicate bug id",
                "expected_detection": "duplicate",
            },
        )
        assert duplicate_bug_id.status_code == 409

        bypass_auto_mutation_response = client.post(
            f"/api/v1/seeded-bugs/{bug['id']}/variants",
            json={
                "id": "variant-auto-bypass",
                "variant_name": "auto mutation bypass",
                "patch": {
                    "file": "shop/pricing.py",
                    "old": "if weight_kg <= 5:",
                    "new": "if weight_kg < 5:",
                },
                "ground_truth": {"source": "auto_mutation"},
            },
        )
        assert bypass_auto_mutation_response.status_code == 400
        assert "mutation confirmation" in bypass_auto_mutation_response.text
        variant_response = client.post(
            f"/api/v1/seeded-bugs/{bug['id']}/variants",
            json={
                "id": "variant-shipping-boundary",
                "variant_name": "canonical patch",
                "patch": {
                    "file": "shop/pricing.py",
                    "old": "if weight_kg <= 5:",
                    "new": "if weight_kg < 5:",
                },
                "ground_truth": {"target": "shipping_fee"},
            },
        )
        assert variant_response.status_code == 200
        variant = variant_response.json()
        patch_payload = {
            "file": "shop/pricing.py",
            "old": "if weight_kg <= 5:",
            "new": "if weight_kg < 5:",
        }
        assert variant["patch_artifact_id"] is None
        assert variant["ground_truth"]["patch_unique_hit"]["hit_count"] == 1
        patch_artifact = variant["ground_truth"]["patch_artifact"]
        assert "uri" not in patch_artifact
        assert patch_artifact["storage"] == "inline"
        assert patch_artifact["content_id"].startswith("patch-")
        assert patch_artifact["content_hash"] == _patch_hash(patch_payload)
        assert patch_artifact["patch"] == patch_payload
        assert patch_artifact["metadata"]["relative_paths"] == ["shop/pricing.py"]

        duplicate_variant_id = client.post(
            f"/api/v1/seeded-bugs/{bug['id']}/variants",
            json={
                "id": variant["id"],
                "variant_name": "duplicate variant id",
                "patch": patch_payload,
            },
        )
        assert duplicate_variant_id.status_code == 409

        bad_mutated_root = project_root / "bad_mutated"
        bad_mutated_package = bad_mutated_root / "shop"
        bad_mutated_package.mkdir(parents=True)
        (bad_mutated_package / "pricing.py").write_text(
            "def shipping_fee(weight_kg):\n"
            "    if weight_kg <= 5:\n"
            "        return 10\n"
            "    return 20\n",
            encoding="utf-8",
        )
        bad_mutated_snapshot = client.post(
            f"/api/v1/projects/{snapshot['project_id']}/snapshots",
            json={"root_path": str(bad_mutated_root)},
        ).json()
        bad_mutated_variant = client.post(
            f"/api/v1/seeded-bugs/{bug['id']}/variants",
            json={
                "variant_name": "bad mutated snapshot",
                "mutated_snapshot_id": bad_mutated_snapshot["id"],
                "patch": patch_payload,
            },
        )
        assert bad_mutated_variant.status_code == 400
        assert "does not match canonical patch" in bad_mutated_variant.text

        bad_variant = client.post(
            f"/api/v1/seeded-bugs/{bug['id']}/variants",
            json={
                "variant_name": "bad patch",
                "patch": {
                    "file": "shop/pricing.py",
                    "old": "not present",
                    "new": "still absent",
                },
            },
        )
        assert bad_variant.status_code == 400
        assert "exactly once" in bad_variant.text

        detail = client.get(f"/api/v1/eval-datasets/{dataset['id']}").json()
        assert detail["tasks"][0]["seeded_bugs"][0]["variants"][0]["id"] == variant["id"]


def test_mutation_discovery_dry_run_api_returns_audit_without_writing_variants(tmp_path, clean_db):
    app = create_app()
    project_root = tmp_path / "proj"
    package = project_root / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def shipping_fee(weight_kg):\n"
        "    if weight_kg <= 5:\n"
        "        return 10\n"
        "    if weight_kg >= 10:\n"
        "        return 30\n"
        "    return 20\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        snapshot = _create_project_and_snapshot(client, project_root)
        dataset = client.post(
            "/api/v1/eval-datasets",
            json={
                "id": "dataset-mutation-dry-run",
                "name": "mutation dry-run",
                "version": "v1",
                "project_snapshot_ids": [snapshot["id"]],
            },
        ).json()
        task = client.post(
            f"/api/v1/eval-datasets/{dataset['id']}/tasks",
            json={
                "id": "task-mutation-dry-run",
                "project_snapshot_id": snapshot["id"],
                "target_scope": {"targets": ["shipping_fee"]},
                "goal": "discover boundary mutants",
            },
        ).json()

        response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/dry-run",
            json={"sample_seed": 11, "max_selected": 1},
        )
        assert response.status_code == 200
        payload = response.json()
        repeat = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/dry-run",
            json={"sample_seed": 11, "max_selected": 1},
        ).json()
        missing = client.post("/api/v1/eval-tasks/missing-task/mutation-discovery/dry-run", json={})

    assert payload == repeat
    assert payload["eval_task_id"] == task["id"]
    assert payload["source_snapshot_id"] == snapshot["id"]
    assert payload["selected_count"] == 1
    assert payload["excluded_count"] == 0
    assert len(payload["candidates"]) == 2
    assert {candidate["patch"]["old"] for candidate in payload["candidates"]} == {"weight_kg <= 5", "weight_kg >= 10"}
    assert [candidate for candidate in payload["candidates"] if candidate["selection"]["status"] == "selected"][0]["selection"]["sample_seed"] == 11
    assert missing.status_code == 404

    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(BugVariant)) == 0


def test_mutation_discovery_audit_report_cli_exports_json_without_writing_variants(tmp_path, clean_db):
    app = create_app()
    project_root = tmp_path / "proj"
    package = project_root / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def shipping_fee(weight_kg):\n"
        "    if weight_kg <= 5:\n"
        "        return 10\n"
        "    return 20\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        snapshot = _create_project_and_snapshot(client, project_root)
        dataset = client.post(
            "/api/v1/eval-datasets",
            json={
                "id": "dataset-mutation-audit",
                "name": "mutation audit",
                "version": "v1",
                "project_snapshot_ids": [snapshot["id"]],
            },
        ).json()
        task = client.post(
            f"/api/v1/eval-datasets/{dataset['id']}/tasks",
            json={
                "id": "task-mutation-audit",
                "project_snapshot_id": snapshot["id"],
                "target_scope": {"targets": ["shipping_fee"]},
                "goal": "audit boundary mutants",
            },
        ).json()

    with Session(get_engine()) as session:
        report = build_task_mutation_discovery_audit_report(
            session,
            task["id"],
            sample_seed=5,
            max_selected=1,
        )
        assert report.schema_version == "v2.mutation_discovery_audit"
        assert report.dry_run is True
        assert report.writes_database is False
        assert report.runs_replay is False
        assert report.selected_candidate_ids == [report.discovery.candidates[0].candidate_id]

    output_path = tmp_path / "audit" / "mutation-discovery.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "mutation_discovery_audit.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            task["id"],
            "--sample-seed",
            "5",
            "--max-selected",
            "1",
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert str(output_path) in completed.stdout
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert exported["schema_version"] == "v2.mutation_discovery_audit"
    assert exported["eval_task_id"] == task["id"]
    assert exported["selected_candidate_ids"]
    assert exported["writes_database"] is False
    assert exported["runs_replay"] is False

    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(BugVariant)) == 0


def test_confirm_selected_mutation_candidate_creates_seeded_bug_and_variant_only_for_selected(tmp_path, clean_db):
    app = create_app()
    project_root = tmp_path / "proj"
    package = project_root / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def shipping_fee(weight_kg):\n"
        "    if weight_kg <= 5:\n"
        "        return 10\n"
        "    if weight_kg >= 10:\n"
        "        return 30\n"
        "    return 20\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        snapshot = _create_project_and_snapshot(client, project_root)
        dataset = client.post(
            "/api/v1/eval-datasets",
            json={
                "id": "dataset-mutation-confirm",
                "name": "mutation confirm",
                "version": "v1",
                "project_snapshot_ids": [snapshot["id"]],
            },
        ).json()
        task = client.post(
            f"/api/v1/eval-datasets/{dataset['id']}/tasks",
            json={
                "id": "task-mutation-confirm",
                "project_snapshot_id": snapshot["id"],
                "target_scope": {"targets": ["shipping_fee"]},
                "goal": "confirm boundary mutants",
            },
        ).json()

        dry_run = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/dry-run",
            json={"sample_seed": 13, "max_selected": 1},
        ).json()
        selected_candidate_id = next(
            candidate["candidate_id"]
            for candidate in dry_run["candidates"]
            if candidate["selection"]["status"] == "selected"
        )
        selected_candidate = next(
            candidate
            for candidate in dry_run["candidates"]
            if candidate["candidate_id"] == selected_candidate_id
        )
        not_selected_candidate = next(
            candidate
            for candidate in dry_run["candidates"]
            if candidate["selection"]["status"] != "selected"
        )
        not_selected_candidate_id = not_selected_candidate["candidate_id"]

        def probe_for(candidate: dict) -> dict:
            if candidate["patch"]["old"] == "weight_kg <= 5":
                return {"target_kind": "function", "probe": "shipping_fee(5)", "clean_value": 10, "buggy_value": 20}
            return {"target_kind": "function", "probe": "shipping_fee(10)", "clean_value": 30, "buggy_value": 20}

        probe = probe_for(selected_candidate)
        not_selected_probe = probe_for(not_selected_candidate)
        with Session(get_engine()) as session:
            audit_report = build_task_mutation_discovery_audit_report(
                session,
                task["id"],
                sample_seed=13,
                max_selected=1,
            )
        tampered_audit = audit_report.model_dump(mode="json")
        tampered_audit["selected_candidate_ids"] = [not_selected_candidate_id]
        for candidate in tampered_audit["discovery"]["candidates"]:
            if candidate["candidate_id"] == selected_candidate_id:
                candidate["selection"] = {
                    "status": "not_selected",
                    "selected_by": "auto_sampler",
                    "reason": "tampered out of selected set",
                }
            elif candidate["candidate_id"] == not_selected_candidate_id:
                candidate["selection"] = {
                    "status": "selected",
                    "selected_by": "auto_sampler",
                    "reason": "tampered into selected set",
                    "sample_seed": 13,
                    "sample_index": 0,
                }
        tampered_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": tampered_audit,
                "candidate_id": not_selected_candidate_id,
                "probe": not_selected_probe,
                "seeded_bug_id": "bug-tampered-audit",
                "variant_id": "variant-tampered-audit",
            },
        )
        wrong_bug_type_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": audit_report.model_dump(mode="json"),
                "candidate_id": selected_candidate_id,
                "probe": probe,
                "bug_type": "seeded_bug",
                "seeded_bug_id": "bug-wrong-type",
                "variant_id": "variant-wrong-type",
            },
        )
        bad_probe_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": audit_report.model_dump(mode="json"),
                "candidate_id": selected_candidate_id,
                "probe": probe | {"buggy_value": probe["clean_value"]},
                "seeded_bug_id": "bug-bad-probe",
                "variant_id": "variant-bad-probe",
            },
        )

        confirm_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": audit_report.model_dump(mode="json"),
                "candidate_id": selected_candidate_id,
                "probe": probe,
                "description": "confirmed selected auto mutation",
                "expected_detection": "tests should catch selected mutation",
                "variant_name": "confirmed selected mutation",
            },
        )
        duplicate_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": audit_report.model_dump(mode="json"),
                "candidate_id": selected_candidate_id,
                "probe": probe,
            },
        )
        not_selected_response = client.post(
            f"/api/v1/eval-tasks/{task['id']}/mutation-discovery/confirm-selected",
            json={
                "audit_report": audit_report.model_dump(mode="json"),
                "candidate_id": not_selected_candidate_id,
                "probe": probe,
                "seeded_bug_id": "bug-not-selected",
                "variant_id": "variant-not-selected",
            },
        )

    assert tampered_response.status_code == 400
    assert "current server discovery" in tampered_response.text
    assert wrong_bug_type_response.status_code == 400
    assert "bug_type=auto_mutation" in wrong_bug_type_response.text
    assert bad_probe_response.status_code == 400
    assert "probe clean_value and buggy_value must differ" in bad_probe_response.text
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["id"].startswith("bug-mut-")
    assert confirmed["bug_type"] == "auto_mutation"
    assert confirmed["description"] == "confirmed selected auto mutation"
    [variant] = confirmed["variants"]
    assert variant["id"].startswith("variant-mut-")
    assert variant["variant_name"] == "confirmed selected mutation"
    assert variant["ground_truth"]["source"] == "auto_mutation"
    assert variant["ground_truth"]["mutation"]["candidate_id"] == selected_candidate_id
    assert variant["ground_truth"]["mutation"]["selection"]["status"] == "selected"
    assert variant["ground_truth"]["probe"]["probe_check"]["status"] == "passed"
    assert variant["ground_truth"]["probe"]["clean_value"] != variant["ground_truth"]["probe"]["buggy_value"]
    assert variant["ground_truth"]["patch_unique_hit"]["hit_count"] == 1
    assert duplicate_response.status_code == 409
    assert not_selected_response.status_code == 400
    assert "not selected" in not_selected_response.text

    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(SeededBug)) == 1
        assert session.scalar(select(func.count()).select_from(BugVariant)) == 1


def test_seed_demo_dataset_is_repeatable_and_keeps_patch_metadata(clean_db):
    with Session(get_engine()) as session:
        first = seed_demo_dataset(session)
        second = seed_demo_dataset(session)

        assert first["dataset_id"] == DEMO_DATASET_ID
        assert second["created_bugs"] == 0
        assert second["created_variants"] == 0

        dataset_count = session.scalar(select(func.count()).select_from(EvalDataset))
        bug_count = session.scalar(select(func.count()).select_from(SeededBug))
        variant_count = session.scalar(select(func.count()).select_from(BugVariant))

        assert dataset_count == 1
        assert bug_count == first["seeded_bug_count"]
        assert variant_count == first["seeded_bug_count"]

        variant = session.scalar(select(BugVariant).where(BugVariant.id == "variant-cmp-flip-discount"))
        assert variant is not None
        assert variant.patch_artifact_id is None
        assert variant.ground_truth["patch_unique_hit"]["hit_count"] == 1
        assert variant.ground_truth["patch_artifact"]["artifact_type"] == "bug_patch"
        assert variant.ground_truth["patch_artifact"]["storage"] == "inline"
        assert variant.ground_truth["patch_artifact"]["content_id"].startswith("patch-")
        assert "uri" not in variant.ground_truth["patch_artifact"]


def test_seed_demo_dataset_prevalidates_patches_before_writing(monkeypatch, clean_db):
    from eval.demo import bugs as demo_bugs

    bad_bug = replace(demo_bugs.BUGS[0], old="definitely not in clean source")
    monkeypatch.setattr(demo_bugs, "BUGS", [bad_bug])

    with Session(get_engine()) as session:
        with pytest.raises(ValueError, match="canonical patch must hit exactly once"):
            seed_demo_dataset(session)

        assert session.scalar(select(func.count()).select_from(EvalDataset)) == 0
        assert session.scalar(select(func.count()).select_from(SeededBug)) == 0
        assert session.scalar(select(func.count()).select_from(BugVariant)) == 0
