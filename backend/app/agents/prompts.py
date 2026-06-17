# Prompt 工程：system 角色 + 各阶段消息构造。prompt 正文放代码里（prompt_ref 版本化），
# 建 run 时由 orchestrator 冻结进 strategy_snapshot（V1实施清单 §3：不允许只存版本号）。
from __future__ import annotations

from app.agents.llm import Message
from app.schemas.agent import PlanItemDraft
from app.schemas.strategy import StrategyVersionSpec
from app.schemas.tools import AnalyzeProjectOutput, RunPytestOutput

SYSTEM_PROMPT_V1 = """你是 TRACE 的测试生成 Agent，专为 Python/FastAPI 项目生成 pytest 测试。
原则：
- 只依据给你的项目分析摘要写测试，不臆测不存在的接口；
- 测试要可执行、断言有业务意义，覆盖正常路径和关键边界；
- 只输出要求的 JSON，不要解释、不要 markdown 围栏。"""

REFLECTION_CONTRACT_V1 = """Reflection 红线（违反即判失败）：
- 只允许修生成测试自身的问题：import 路径、fixture、调用签名、测试数据构造、断言写反、TestClient 用法；
- 禁止修改业务源码；
- 禁止删除核心断言、改成空洞断言（如只断言 200、断言 True）；
- 禁止用 pytest.skip / 条件跳过 / 宽泛 except 吞掉失败；
- 禁止为了变绿而迁就明显错误的业务行为。
若不削弱断言就无法通过，停止修复，把 suspected_code_bug 置 true，并在 fix_reason 说明疑点。"""


def build_system_prompt(strategy: StrategyVersionSpec) -> str:
    # V1 只有 v1；后续按 strategy.prompt_ref 选不同正文
    return SYSTEM_PROMPT_V1


def summarize_analysis(analysis: AnalyzeProjectOutput, limit: int = 40) -> str:
    """给 LLM 的项目摘要：结构化要点，绝不贴整仓源码。"""
    lines: list[str] = []
    if analysis.routes:
        lines.append("FastAPI 路由：")
        for r in analysis.routes[:limit]:
            lines.append(f"  - {r.method} {r.path}  (handler={r.handler}, file={r.file})")
    if analysis.functions:
        lines.append("函数：")
        for fn in analysis.functions[:limit]:
            lines.append(f"  - {fn.signature}  (file={fn.file})")
    if analysis.models:
        lines.append("Pydantic 模型：")
        for m in analysis.models[:limit]:
            lines.append(f"  - {m.name}  (file={m.file})")
    if analysis.existing_tests:
        lines.append("已有测试：")
        for t in analysis.existing_tests[:limit]:
            lines.append(f"  - {t.path}（{len(t.test_functions)} 个测试函数）")
    if analysis.warnings:
        lines.append("分析告警：")
        for w in analysis.warnings[:limit]:
            lines.append(f"  - {w}")
    return "\n".join(lines) or "（分析未发现可测对象）"


_GEN_SCHEMA_HINT = (
    '返回 JSON：{"test_file_content":"完整 pytest 文件源码字符串",'
    '"cases":[{"test_name":"test_xxx","target_route":null,"target_function":"fn","assertion_summary":"断言要点"}],'
    '"coverage_notes":"覆盖说明","risks":"风险/假设"}。'
    'test_file_path 可省略，文件路径由系统决定。'
)

GEN_PREFIX = "生成一个 pytest 测试文件。"

GEN_CONTRACT = """生成硬约束（违反会被 PIPELINE_REJECT）：
- 每个检查必须写成 assert 或 pytest.raises；不要写裸表达式，例如 response.json()["total"] == 270.0。
- 成功路由测试不能只断言 2xx status_code，必须断言响应 JSON 的业务字段和值。
- 不要给测试函数添加未知参数；只能使用 pytest 内置 fixture（如 monkeypatch/tmp_path）或项目分析中列出的 fixture。
- 使用 unittest.mock.patch(..., new_value) 时不要额外声明 mock 参数；可优先使用 pytest 内置 monkeypatch。
- cases[*].test_name 必须对应真实 test 函数名；参数化测试可使用 test_x[...]。"""

PLAN_INSTRUCTION = (
    '把目标拆成若干测试任务，返回 JSON：'
    '{"items":[{"index":0,"target_type":"function|route|module","target_ref":"标识","goal":"该任务测什么","planned_assertions":["..."]}]}'
)

REFLECT_JSON_TAIL = (
    '返回 JSON：{"fixed_content":"修复后的完整文件源码","fix_reason":"为何这么修",'
    '"changed_points":["改了哪些"],"lowered_assertion_strength":false,"suspected_code_bug":false}'
)


def prompt_bundle(strategy: StrategyVersionSpec) -> dict:
    """冻结进 strategy_snapshot 的完整 prompt 正文。
    只存 prompt_ref 不够：以后改这里的任何模板，历史 run 就不可复现了（V1实施清单 §3）。"""
    return {
        "prompt_ref": strategy.prompt_ref,
        "version": "v1",
        "system": SYSTEM_PROMPT_V1,
        "reflection_contract": REFLECTION_CONTRACT_V1,
        "plan_instruction": PLAN_INSTRUCTION,
        "generate_prefix": GEN_PREFIX,
        "generate_contract": GEN_CONTRACT,
        "generate_schema_hint": _GEN_SCHEMA_HINT,
        "reflect_json_tail": REFLECT_JSON_TAIL,
    }


def build_plan_messages(
    strategy: StrategyVersionSpec, analysis: AnalyzeProjectOutput, scope: list[str], goal: str
) -> list[Message]:
    user = (
        f"TASK: PLAN\n目标：{goal}\n范围：{scope or '整个项目'}\n\n"
        f"项目分析：\n{summarize_analysis(analysis)}\n\n"
        f"{PLAN_INSTRUCTION}"
    )
    return [Message("system", build_system_prompt(strategy)), Message("user", user)]


def build_generate_messages(
    strategy: StrategyVersionSpec,
    analysis: AnalyzeProjectOutput,
    scope: list[str],
    goal: str,
    item: PlanItemDraft | None,
    source_context: str = "",
) -> list[Message]:
    focus = f"\n本次聚焦任务：{item.goal}（target={item.target_type}:{item.target_ref}）" if item else ""
    source_block = f"\n目标源码片段：\n{source_context}\n" if source_context else "\n目标源码片段：\ncontext_incomplete\n"
    user = (
        f"TASK: GENERATE\n总目标：{goal}\n范围：{scope or '整个项目'}{focus}\n\n"
        f"项目分析：\n{summarize_analysis(analysis)}\n\n"
        f"{source_block}\n"
        f"{GEN_CONTRACT}\n"
        f"{GEN_PREFIX}{_GEN_SCHEMA_HINT}"
    )
    return [Message("system", build_system_prompt(strategy)), Message("user", user)]


def build_reflect_messages(
    strategy: StrategyVersionSpec,
    analysis: AnalyzeProjectOutput,
    failed_content: str,
    pytest_result: RunPytestOutput,
    relevant_source: str = "",
) -> list[Message]:
    fails = "\n".join(
        f"  - {f.nodeid} [{f.failure_type}] {f.exc_type}: {f.message}" for f in pytest_result.failures[:20]
    )
    collect = "\n".join(f"  - {c.nodeid} [{c.failure_type}] {c.message}" for c in pytest_result.collection_errors[:20])
    failure_context = _failure_context_block(pytest_result)
    # 反斜杠不能进 f-string 的 {} 表达式（Py<3.12），相关源码块先拼好
    source_block = f"相关源码：\n{relevant_source}\n" if relevant_source else "相关源码：\ncontext_incomplete\n"
    user = (
        f"TASK: REFLECT\n首轮生成的测试没通过，请按契约修复一次。\n\n"
        f"{REFLECTION_CONTRACT_V1}\n\n"
        f"当前测试文件：\n```python\n{failed_content}\n```\n\n"
        f"失败用例：\n{fails or '（无）'}\n收集错误：\n{collect or '（无）'}\n\n"
        f"失败上下文：\n{failure_context}\n\n"
        f"项目分析：\n{summarize_analysis(analysis)}\n"
        f"{source_block}\n"
        f"{REFLECT_JSON_TAIL}"
    )
    return [Message("system", build_system_prompt(strategy)), Message("user", user)]


def _failure_context_block(pytest_result: RunPytestOutput, *, limit: int = 8, traceback_chars: int = 1200) -> str:
    rows: list[str] = []
    for detail in [*pytest_result.failures, *pytest_result.collection_errors][:limit]:
        location = _failure_location(detail.file, detail.line)
        header = f"- {detail.nodeid or '<collect>'} [{detail.failure_type}] {detail.exc_type or ''} {location}".strip()
        rows.append(header)
        if detail.message:
            rows.append(f"  message: {_clip(detail.message, 300)}")
        if detail.traceback:
            rows.append("  traceback:")
            rows.append(_indent(_clip(detail.traceback, traceback_chars), "    "))
    return "\n".join(rows) if rows else "（无）"


def _failure_location(path: str | None, line: int | None) -> str:
    if path and line:
        return f"@ {path}:{line}"
    if path:
        return f"@ {path}"
    return ""


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
