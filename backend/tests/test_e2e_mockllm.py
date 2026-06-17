# M1 端到端：用 MockLLM 把三策略 + Reflection 闭环真跑一遍（真起 pytest 子进程）。
# 验证的不是「LLM 聪明」，而是「编排/落库/契约/报告」这条链路在闭环里站得住。
from __future__ import annotations

import json
from pathlib import Path

from app.agents.orchestrator import execute_run
from app.agents.recorder import InMemoryRecorder
from app.agents.runtime import PlanInput
from app.agents.seeds import SEED_STRATEGIES
from app.tools import default_registry
from app.tools.base import ToolContext

# ---- 被测 mini 项目的两份源码：一份正确，一份埋了真 bug ----
CALC_OK = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def is_even(n):\n"
    "    return n % 2 == 0\n"
)
CALC_BUG = (
    "def add(a, b):\n"
    "    return a + b + 1  # 故意 bug：多加 1\n"
    "\n"
    "\n"
    "def is_even(n):\n"
    "    return n % 2 == 0\n"
)

# ---- MockLLM 会「生成」的测试文件源码 ----
T_BOTH_OK = (
    "from calc import add, is_even\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
    "\n"
    "def test_is_even():\n"
    "    assert is_even(4) is True\n"
)
T_ADD_OK = (
    "from calc import add\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
)
T_ADD_WRONG = (  # 断言期望值写成 6，在 CALC_OK 下必然失败
    "from calc import add\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 6\n"
)
T_ISEVEN_OK = (
    "from calc import is_even\n"
    "\n"
    "def test_is_even_true():\n"
    "    assert is_even(4) is True\n"
    "\n"
    "def test_is_even_false():\n"
    "    assert is_even(3) is False\n"
)
T_CLASS_OK = (
    "from calc import add\n"
    "\n"
    "class TestCalc:\n"
    "    def test_add(self):\n"
    "        assert add(2, 3) == 5\n"
)


def _mini_project(tmp_path, src=CALC_OK):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text(src, encoding="utf-8")
    twd = root / "tests" / "generated"
    twd.mkdir(parents=True)
    return ToolContext(root=root, test_write_dir=twd)


def _blob(messages):
    # 扫所有消息（含 system）找任务标记，避免万一重试时只看最后一条用户消息漏判
    return "\n".join(m.content for m in messages)


def _gen_payload(content, cases):
    # 故意不给 test_file_path（schema 已改可选，路径由系统强制决定，与 prompt「可省略」一致）
    return json.dumps(
        {
            "test_file_content": content,
            "cases": cases,
            "coverage_notes": "mock 生成",
        },
        ensure_ascii=False,
    )


def _case(name, fn, summary):
    return {"test_name": name, "target_function": fn, "assertion_summary": summary}


def _run(ctx, strategy_key, responder, *, allow_reflection=False, artifacts=None):
    rec = InMemoryRecorder()
    from app.agents.llm import MockLLM

    outcome = execute_run(
        tools=ctx,
        registry=default_registry(),
        llm=MockLLM(responder),
        recorder=rec,
        strategy_spec=SEED_STRATEGIES[strategy_key],
        plan_input=PlanInput(target_scope=[], goal="测试 calc 模块", allow_reflection=allow_reflection),
        artifacts_dir=artifacts,
    )
    return outcome, rec


def _stages(rec):
    return [e.stage for e in rec.run_events if e.event_type == "stage_enter"]


# ---------- 场景 1：Direct 一把过 ----------
def test_direct_pass(tmp_path):
    ctx = _mini_project(tmp_path)

    def responder(messages):
        return _gen_payload(
            T_BOTH_OK,
            [_case("test_add", "add", "2+3=5"), _case("test_is_even", "is_even", "4 为偶数")],
        )

    outcome, rec = _run(ctx, "direct", responder, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message
    assert run.stage is None and run.error_code is None
    assert run.started_at and run.finished_at

    m = outcome.report.metrics
    assert m["plan_items"] == 1
    assert m["final_passed"] == 2 and m["final_failed"] == 0
    assert m["reflection_used"] is False

    # 审计链：analyze / generation / run_pytest 都落了 trace
    steps = rec.steps_of(run.id)
    assert any(s.tool_name == "analyze_project" for s in steps)
    assert any(s.step_type == "generation" for s in steps)
    assert any(s.tool_name == "run_pytest" for s in steps)
    # 生成步把每次 LLM 调用（输入 messages/原始输出/错误）落进 payload.attempt_logs（重试也能逐次复现）
    gen = next(s for s in steps if s.step_type == "generation")
    assert gen.payload and gen.payload.get("attempt_logs")
    first = gen.payload["attempt_logs"][0]
    assert any(msg["role"] == "user" for msg in first["messages"])
    assert "raw_text" in first and "error" in first
    assert gen.input_summary
    # 状态机事件 + 报告落盘
    assert any(e.event_type == "run_finish" for e in rec.run_events)
    assert Path(outcome.report.markdown_uri).exists()
    assert Path(outcome.report.json_uri).exists()
    # 快照冻结了完整 prompt bundle（含契约正文 + 各阶段模板），不只是 ref
    pb = run.strategy_snapshot["prompts"]
    assert "红线" in pb["reflection_contract"]
    assert pb["plan_instruction"] and pb["generate_schema_hint"] and pb["reflect_json_tail"]
    assert "PIPELINE_REJECT" in pb["generate_contract"]
    assert "响应 JSON" in pb["generate_contract"]
    assert "未知参数" in pb["generate_contract"]


def test_generated_class_nodeid_maps_with_class_name(tmp_path):
    ctx = _mini_project(tmp_path)

    def responder(messages):
        return _gen_payload(T_CLASS_OK, [_case("test_add", "add", "2+3=5")])

    outcome, rec = _run(ctx, "direct", responder, artifacts=tmp_path / "art")

    assert outcome.run.status == "completed", outcome.run.error_message
    [case] = rec.cases
    assert case.nodeid == "tests/generated/test_generated.py::TestCalc::test_add"
    [result] = rec.pytest_results
    assert result.nodeid == case.nodeid
    assert result.generated_test_case_id == case.id
    assert result.mapping_status == "matched"


# ---------- 场景 2：Plan-and-Execute 拆 2 个任务，各生成各执行 ----------
def test_plan_execute_two_items(tmp_path):
    ctx = _mini_project(tmp_path)

    def responder(messages):
        b = _blob(messages)
        if "TASK: PLAN" in b:
            return json.dumps(
                {
                    "items": [
                        {"index": 0, "target_type": "function", "target_ref": "add",
                         "goal": "覆盖 add", "planned_assertions": ["add(2,3)==5"]},
                        {"index": 1, "target_type": "function", "target_ref": "is_even",
                         "goal": "覆盖 is_even", "planned_assertions": ["is_even(4)"]},
                    ]
                },
                ensure_ascii=False,
            )
        if "TASK: GENERATE" in b:
            # 用 focus 行里的 target=function:xxx 区分是哪个任务（项目摘要里不会出现这个串）
            if "target=function:is_even" in b:
                return _gen_payload(
                    T_ISEVEN_OK,
                    [_case("test_is_even_true", "is_even", "真"), _case("test_is_even_false", "is_even", "假")],
                )
            return _gen_payload(T_ADD_OK, [_case("test_add", "add", "2+3=5")])
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "plan_execute", responder, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message
    m = outcome.report.metrics
    assert m["plan_items"] == 2
    assert m["attempts"] == 2  # 每任务一个 initial
    assert m["final_passed"] == 3 and m["final_failed"] == 0  # add 1 + is_even 2
    # 两个任务写到不同文件
    assert {f.path for f in rec.files} == {
        "tests/generated/test_item0.py",
        "tests/generated/test_item1.py",
    }
    assert "planning" in _stages(rec)


# ---------- 场景 3：ReAct+Reflection 首轮失败 → 反思修好 → 通过 ----------
def test_react_reflection_recovers(tmp_path):
    ctx = _mini_project(tmp_path, CALC_OK)

    def responder(messages):
        b = _blob(messages)
        if "TASK: REFLECT" in b:
            return json.dumps(
                {
                    "fixed_content": T_ADD_OK,
                    "fix_reason": "断言期望值写错，2+3 应为 5",
                    "changed_points": ["6 -> 5"],
                    "lowered_assertion_strength": False,
                    "suspected_code_bug": False,
                },
                ensure_ascii=False,
            )
        if "TASK: GENERATE" in b:
            return _gen_payload(T_ADD_WRONG, [_case("test_add", "add", "2+3=5")])
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "react_reflection", responder, allow_reflection=True, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message
    m = outcome.report.metrics
    assert m["reflection_used"] is True
    assert m["reflection_contract_violation"] is False
    assert m["suspected_code_bug"] is False
    assert m["final_passed"] == 1 and m["final_failed"] == 0
    assert m["attempts"] == 2

    attempts = sorted(rec.attempts.values(), key=lambda a: a.attempt_no)
    assert attempts[0].kind == "initial" and attempts[0].status == "failed"
    assert attempts[1].kind == "reflection" and attempts[1].status == "passed"
    stages = _stages(rec)
    assert "reflecting" in stages and "reexecuting" in stages


# ---------- 场景 4：Reflection 削弱断言却不标疑似缺陷 = 契约违规，不采纳 ----------
def test_react_reflection_contract_violation(tmp_path):
    ctx = _mini_project(tmp_path, CALC_OK)

    def responder(messages):
        b = _blob(messages)
        if "TASK: REFLECT" in b:
            return json.dumps(
                {
                    "fixed_content": "from calc import add\n\ndef test_add():\n    assert True\n",
                    "fix_reason": "为了变绿改成空洞断言",
                    "changed_points": ["断言改成 assert True"],
                    "lowered_assertion_strength": True,
                    "suspected_code_bug": False,
                },
                ensure_ascii=False,
            )
        if "TASK: GENERATE" in b:
            return _gen_payload(T_ADD_WRONG, [_case("test_add", "add", "2+3=5")])
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "react_reflection", responder, allow_reflection=True, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message  # 运行本身成功产出报告
    m = outcome.report.metrics
    assert m["reflection_contract_violation"] is True
    assert m["suspected_code_bug"] is False
    # 违规修复不采纳：最终测试集回退到首轮失败，绝不能掩盖成空集/全绿
    assert m["final_failed"] >= 1 and m["final_cases_total"] >= 1

    a2 = [a for a in rec.attempts.values() if a.kind == "reflection"][0]
    assert a2.status == "error"
    assert a2.error_code == "REFLECTION_CONTRACT_VIOLATION"
    assert "契约违规" in (outcome.report.risk_notes or "")


# ---------- 场景 5：断言正确但被真 bug 干翻 → 反思判定疑似业务缺陷，保留失败 ----------
def test_react_reflection_suspects_code_bug(tmp_path):
    ctx = _mini_project(tmp_path, CALC_BUG)  # add 返回 a+b+1

    def responder(messages):
        b = _blob(messages)
        if "TASK: REFLECT" in b:
            return json.dumps(
                {
                    "fixed_content": T_ADD_OK,  # 不改断言
                    "fix_reason": "断言正确，add(2,3) 期望 5 实得 6，疑似业务实现缺陷",
                    "changed_points": [],
                    "lowered_assertion_strength": False,
                    "suspected_code_bug": True,
                },
                ensure_ascii=False,
            )
        if "TASK: GENERATE" in b:
            return _gen_payload(T_ADD_OK, [_case("test_add", "add", "2+3=5")])
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "react_reflection", responder, allow_reflection=True, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message
    m = outcome.report.metrics
    assert m["suspected_code_bug"] is True
    assert m["reflection_used"] is True
    assert m["final_failed"] >= 1  # 保留失败用例，不迁就 bug
    assert "疑似业务缺陷" in (outcome.report.risk_notes or "")


# ---------- 场景 6：模型撒谎，偷偷削弱断言却报 lowered=False —— 静态闸门必须拦下 ----------
def test_react_reflection_static_guard_catches_lie(tmp_path):
    ctx = _mini_project(tmp_path, CALC_OK)

    def responder(messages):
        b = _blob(messages)
        if "TASK: REFLECT" in b:
            # 把断言偷换成 assert True，却谎称没有削弱（lowered=False, 不标疑似缺陷）
            return json.dumps(
                {
                    "fixed_content": "from calc import add\n\ndef test_add():\n    assert True\n",
                    "fix_reason": "已修复",
                    "changed_points": ["微调"],
                    "lowered_assertion_strength": False,
                    "suspected_code_bug": False,
                },
                ensure_ascii=False,
            )
        if "TASK: GENERATE" in b:
            return _gen_payload(T_ADD_WRONG, [_case("test_add", "add", "2+3=5")])
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "react_reflection", responder, allow_reflection=True, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "completed", run.error_message
    m = outcome.report.metrics
    # 靠静态比对 AST 拦下，而不是模型自觉
    assert m["reflection_contract_violation"] is True
    assert m["final_failed"] >= 1  # 谎言不被采纳，保留首轮失败，绝不掩盖成全绿
    a2 = [a for a in rec.attempts.values() if a.kind == "reflection"][0]
    assert a2.error_code == "REFLECTION_CONTRACT_VIOLATION"


# ---------- 场景 7：模型死活给不出合法 JSON → 运行 failed，且失败前留下带 attempt_logs 的 error trace ----------
def test_invalid_model_output_fails_with_error_trace(tmp_path):
    ctx = _mini_project(tmp_path, CALC_OK)

    def responder(messages):
        return "这绝对不是 JSON"  # 三次调用都给不出合法结构化输出

    outcome, rec = _run(ctx, "direct", responder, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "failed"
    assert run.error_code == "INVALID_MODEL_OUTPUT"
    assert outcome.report is None  # 没产出报告
    assert run.total_tokens > 0  # 失败重试烧的 token 也要累计，不能让成本指标撒谎
    # 失败前留痕：结构化输出失败那步带 attempt_logs + tokens（1 + 2 重试 = 3 次调用）
    steps = rec.steps_of(run.id)
    err = [s for s in steps if s.status == "error" and s.payload and s.payload.get("attempt_logs")]
    assert err
    e = err[0]
    assert len(e.payload["attempt_logs"]) == 3 and e.payload["attempts"] == 3
    assert e.tokens and e.tokens > 0


def test_generation_contract_rejects_weak_test_before_pytest(tmp_path):
    ctx = _mini_project(tmp_path, CALC_OK)

    def responder(messages):
        b = _blob(messages)
        if "TASK: GENERATE" in b:
            return _gen_payload(
                "from calc import add\n\n"
                "def test_add():\n"
                "    assert True\n",
                [{"test_name": "test_add", "target_function": "add", "assertion_summary": "空洞断言"}],
            )
        raise AssertionError("未预期的任务消息：" + b[:120])

    outcome, rec = _run(ctx, "direct", responder, artifacts=tmp_path / "art")
    run = outcome.run

    assert run.status == "failed"
    assert run.error_code == "PIPELINE_REJECT"
    assert any("空洞断言" in (step.error or "") for step in rec.trace_steps)
    assert rec.files == []
    assert outcome.report is None
    assert run.total_tokens > 0
