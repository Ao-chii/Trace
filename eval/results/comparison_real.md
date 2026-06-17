# TRACE 策略评测对比（真实模型 deepseek-v4-flash）

> provider=openai_chat_compat　model=deepseek-v4-flash　temperature=None　reasoning_effort=None　repeats=1　生成于 2026-06-17 17:26:45
> experiment_id=exp-ae74f9a4-66e7-4b33-aa81-2761c8c68457
> 数据来自 `eval/harness/run_eval.py --real`：3 策略 × 1 重复，干净代码生成 → 6 个 bug 变体重放。
> 与 MockLLM 的 comparison.md 并存：本表是真实测量，捕获/假阳性/反思/token 皆来自真模型，逐次可能不同。

## 策略对比

| 策略 | 状态 | 捕获率(均值±std) | 假阳性率 | 反思 | 平均 token | 平均工具调用 | cost/captured |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Direct v1 | 可评价 | 1.00 ± 0.00（6.0/6） | 0.00 | 否 | 4029 | 3.0 | 671.5 |
| Plan-and-Execute v1 | 可评价 | 1.00 ± 0.00（6.0/6） | 1.00 | 否 | 18153 | 15.0 | 3025.5 |
| ReAct+Reflection v1 | 可评价 | 1.00 ± 0.00（6.0/6） | 0.00 | 否 | 4707 | 3.0 | 784.5 |

## 逐 bug 捕获矩阵

| bug | 类型 | Direct v1 | Plan-and-Execute v1 | ReAct+Reflection v1 |
| --- | --- | --- | --- | --- |
| variant-cmp-flip-discount | variant-cmp-flip-discount | ✓ | ✓ | ✓ |
| variant-cmp-flip-freeship | variant-cmp-flip-freeship | ✓ | ✓ | ✓ |
| variant-boundary-shipping | variant-boundary-shipping | ✓ | ✓ | ✓ |
| variant-boundary-loyalty | variant-boundary-loyalty | ✓ | ✓ | ✓ |
| variant-missing-clamp | variant-missing-clamp | ✓ | ✓ | ✓ |
| variant-wrong-status | variant-wrong-status | ✓ | ✓ | ✓ |
