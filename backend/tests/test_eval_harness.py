# 评测 harness 端到端 + 指标聚合自测。
# 端到端跑真 pytest 子进程：干净生成 → 6 变体重放，验证三策略的捕获/假阳性/反思故事。
# 聚合用合成数据快测，证明 3 次重复的均值/标准差/假阳性/成本口径，无需起子进程。
from __future__ import annotations

import pytest

from eval.demo.bugs import BUGS
from eval.harness import dataset
from eval.harness import metrics
from eval.harness.probes import ProbeCheckError, check_variant_probe
from eval.harness.run_eval import _failed_smoke_runs, _format_run_error, run_full_eval
from eval.harness.runner import CleanRun


def test_full_eval_story(tmp_path):
    result = run_full_eval(tmp_path, repeats=1)
    rows = {r["strategy_id"]: r for r in result["rows"]}

    # Direct：单发把路由测试状态码写反 → 干净上失败=假阳性；且漏掉 wrong-status（5/6）
    d = rows["sv-direct-v1"]
    assert d["false_positive_rate"] == 1.0
    assert d["reflection_used"] is False
    assert abs(d["capture_rate_mean"] - 5 / 6) < 1e-9

    # Plan-and-Execute：逐目标，路由写对，全捕获、无假阳性
    p = rows["sv-plan-v1"]
    assert abs(p["capture_rate_mean"] - 1.0) < 1e-9
    assert p["false_positive_rate"] == 0.0

    # ReAct+Reflection：反思把路由断言修回 404 → 全捕获、无假阳性、用了反思
    re = rows["sv-react-v1"]
    assert abs(re["capture_rate_mean"] - 1.0) < 1e-9
    assert re["false_positive_rate"] == 0.0
    assert re["reflection_used"] is True

    # 对比表 + 捕获矩阵渲染出来；wrong-status 被 plan/react 抓到、被 direct 漏掉
    assert "捕获率" in result["table_md"]
    assert "状态" in result["table_md"]
    assert "wrong-status" in result["matrix_md"]
    m = result["capture_matrix"]
    assert m["wrong-status"]["sv-plan-v1"] is True
    assert m["wrong-status"]["sv-react-v1"] is True
    assert m["wrong-status"]["sv-direct-v1"] is False
    for runs in result["runs_by_strategy"].values():
        for run in runs:
            for variant in run.variants.values():
                probe_check = variant["probe_check"]
                assert probe_check["status"] == "passed"
                assert probe_check["clean_actual"] == probe_check["clean_expected"]
                assert probe_check["buggy_actual"] == probe_check["buggy_expected"]


def test_probe_check_rejects_equal_clean_and_buggy_value(tmp_path):
    bug = BUGS[0]
    variant_root = dataset.materialize_variant(bug, tmp_path / "variant")

    with pytest.raises(ProbeCheckError, match="clean_value and buggy_value must differ"):
        check_variant_probe(
            clean_root=dataset.CLEAN_ROOT,
            variant_root=variant_root,
            ground_truth={
                "target_kind": bug.kind,
                "probe": bug.probe,
                "clean_value": bug.clean_value,
                "buggy_value": bug.clean_value,
                "patch_artifact": {"patch": {"file": bug.file}},
            },
            variant_id=bug.id,
        )


def test_probe_check_accepts_structured_probe_metadata(tmp_path):
    bug = BUGS[0]
    variant_root = dataset.materialize_variant(bug, tmp_path / "variant")

    result = check_variant_probe(
        clean_root=dataset.CLEAN_ROOT,
        variant_root=variant_root,
        ground_truth={
            "probe": {
                "target_kind": bug.kind,
                "probe": bug.probe,
                "clean_value": bug.clean_value,
                "buggy_value": bug.buggy_value,
            },
            "patch_artifact": {"patch": {"file": bug.file}},
        },
        variant_id=bug.id,
    )

    assert result is not None
    assert result["status"] == "passed"
    assert result["clean_actual"] == bug.clean_value
    assert result["buggy_actual"] == bug.buggy_value


def test_probe_check_accepts_none_expected_value(tmp_path):
    clean_root = tmp_path / "clean"
    variant_root = tmp_path / "variant"
    for root, return_value in ((clean_root, "None"), (variant_root, "False")):
        package = root / "shop"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "lookup.py").write_text(
            "def find_user(user_id):\n"
            f"    return {return_value}\n",
            encoding="utf-8",
        )

    result = check_variant_probe(
        clean_root=clean_root,
        variant_root=variant_root,
        ground_truth={
            "target_kind": "function",
            "probe": "find_user(1)",
            "clean_value": None,
            "buggy_value": False,
            "patch_artifact": {"patch": {"file": "shop/lookup.py"}},
        },
        variant_id="variant-none-return",
    )

    assert result is not None
    assert result["status"] == "passed"
    assert result["clean_actual"] is None
    assert result["buggy_actual"] is False

def test_probe_check_rejects_non_literal_function_probe(tmp_path):
    bug = BUGS[0]
    variant_root = dataset.materialize_variant(bug, tmp_path / "variant")

    with pytest.raises(ProbeCheckError, match="arguments must be constants"):
        check_variant_probe(
            clean_root=dataset.CLEAN_ROOT,
            variant_root=variant_root,
            ground_truth={
                "target_kind": bug.kind,
                "probe": "apply_discount(total, True)",
                "clean_value": bug.clean_value,
                "buggy_value": bug.buggy_value,
                "patch_artifact": {"patch": {"file": bug.file}},
            },
            variant_id=bug.id,
        )


def _mk(sid, rep, captured_ids, clean_failed, tokens, all_ids):
    variants = {bid: {"captured": bid in captured_ids} for bid in all_ids}
    return CleanRun(
        strategy_id=sid, strategy_name=sid, repeat=rep, status="completed",
        files={}, clean_passed=set(), clean_failed=clean_failed, n_cases=0,
        tokens=tokens, tool_calls=3, reflection_used=False, variants=variants,
    )


def test_aggregate_repeat_stats():
    ids = [f"b{i}" for i in range(6)]
    # 三次重复捕获数 6/4/5 → 率 1.0/0.667/0.833，均值 0.833、std>0；一次干净失败 → 假阳性 1/3
    runs = [
        _mk("s", 0, ids[:6], False, 100, ids),
        _mk("s", 1, ids[:4], True, 100, ids),
        _mk("s", 2, ids[:5], False, 100, ids),
    ]
    [row] = metrics.aggregate({"s": runs}, total_in_scope=6)
    assert abs(row["capture_rate_mean"] - (1.0 + 4 / 6 + 5 / 6) / 3) < 1e-9
    assert row["capture_rate_std"] > 0  # 重复间有差异，标准差非零
    assert abs(row["false_positive_rate"] - 1 / 3) < 1e-9
    assert row["cost_per_captured_bug"] == 300 / 15  # total_tokens / total_captured

    # 0 捕获：成本记 None（no_bug_captured），不用 0 伪装
    zero = [_mk("z", 0, [], False, 50, ids)]
    [zrow] = metrics.aggregate({"z": zero}, total_in_scope=6)
    assert zrow["cost_per_captured_bug"] is None


def test_comparison_table_marks_invalid_test_set():
    ids = [f"b{i}" for i in range(2)]
    invalid = _mk("invalid", 0, [], False, 0, ids)
    invalid.invalid_test_set = True
    rows = metrics.aggregate({"invalid": [invalid]}, total_in_scope=2)

    table = metrics.comparison_table_md(rows)

    assert "不可评价" in table
    assert "| invalid | 不可评价 |" in table


def test_failed_clean_run_is_not_evaluable_zero_capture():
    ids = [f"b{i}" for i in range(2)]
    failed = _mk("failed", 0, [], False, 0, ids)
    failed.status = "failed"
    failed.error_code = "LLM_PROVIDER_ERROR"

    [row] = metrics.aggregate({"failed": [failed]}, total_in_scope=2)
    table = metrics.comparison_table_md([row])

    assert row["metric_status"] == "invalid_test_set"
    assert row["repeats"] == 0
    assert row["invalid_test_set_count"] == 1
    assert "| failed | 不可评价 |" in table


def test_format_run_error_keeps_smoke_failure_actionable():
    run = _mk("s", 0, [], False, 0, ["b0"])
    run.status = "failed"
    run.error_code = "LLM_PROVIDER_ERROR"
    run.error_message = "OpenAI API HTTP 401 Unauthorized: bad key"

    assert _format_run_error(run) == "LLM_PROVIDER_ERROR: OpenAI API HTTP 401 Unauthorized: bad key"


def test_failed_smoke_runs_detects_non_completed_runs():
    ok = _mk("ok", 0, [], False, 0, ["b0"])
    failed = _mk("failed", 0, [], False, 0, ["b0"])
    failed.status = "failed"

    assert _failed_smoke_runs({"runs_by_strategy": {"ok": [ok], "failed": [failed]}}) == [failed]
