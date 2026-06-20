# TRACE V2.2 B 线对接文档

## 1. 分工边界

B 线负责项目理解、数据集和生成约束：

- source context / retrieval trace
- Contract Guard / 生成与反思约束
- Dataset task / seeded bug / variant / mutation confirmation
- prompt-safe EvaluationEvent 回灌

A 线负责执行平台和评测尺子：executor、runtime profile、setup/replay/cache/cleanup、metrics 主体和 runtime evidence。B 线只通过稳定合同消费 A 线输出，不解析 A 线内部 executor payload。

## 2. 当前新增对接点

本轮 B 线补强了 route source context 与 Contract Guard 的常量证据链：

- source context 会把目标函数体读取到的同文件模块级赋值作为 `dependency` 证据片段。
- demo `get_price` 的 prompt 证据现在包含 `_PRICES = {"book": 30.0, "pen": 2.5}`。
- Contract Guard 会把 source context 中可静态求值的顶层赋值放入源码求值环境。
- 当测试对 `/price/apple` 断言 200 / 业务 JSON，而源码证据显示 `_PRICES` 只包含 `book`、`pen` 时，Guard 会拒绝该 route oracle。

该改动不改变 replay、metrics、runtime profile、executor 或 captured bug 定义。

随后将依赖切片实现从 source_context.py 机械拆分到 source_context_dependencies.py，只降低模块复杂度，不改变 source context / retrieval trace 合同。

本轮继续将 support target 收集、去重和 incomplete 判断机械拆到 source_context_support.py；不改变 prompt 内容、snippet 预算、retrieval trace 字段或 completeness 语义。

随后将 route target 字符串解析 helper 机械拆到 source_context_routes.py；不改变 route handler 定位、fallback 顺序或 source context 输出。

## 3. A/B 接口约定

A 线可继续把 source context completeness 作为 clean run gate 使用：

- 缺目标源码仍应进入 `context_incomplete_blocking / invalid_test_set`，不能压成 0 捕获。
- B 线新增的常量片段只是 prompt/guard 证据，不改变 A 线 replay 口径。
- replay 仍只执行冻结测试集，不调用 LLM。
- provider_failure、artifact_hash_mismatch、executor/setup/cache 事件默认 audit/report，不自动进入 prompt。

B 线消费 A 线事件时只允许 prompt-safe 子集：

- `flaky_clean_replay`
- `probe_check_failed`
- `replay_failure`

其它事件进入 UI/report 审计即可。

B 线已把 prompt-safe 事件类型策略抽到 `evaluation_event_policy.py`，`source_context_failures.py` 在低层再次过滤 audit-only / non-consumable EvaluationEvent。即使调用方误传 `provider_failure` 或 `artifact_hash_mismatch`，也只留下 risk note，不生成 prompt snippet。

Mutation confirmation 入口不再信任客户端回传的 audit candidate。confirm 时会按 audit report 的 `sample_seed`、`max_selected`、`target_scope` 在服务端重新 dry-run discovery，并要求候选内容与服务端当前 selected candidate 完全一致，避免伪造/过期 audit 把未选中 mutant 入库。该入口只允许创建 `bug_type=auto_mutation`，不允许客户端把 auto mutation 伪装成普通 seeded bug。普通 `create_bug_variant` 入口也拒绝 `ground_truth.source=auto_mutation`，防止绕过 probe confirmation 污染 sampled mutation score。

Dataset UI 已接入后端已有 task / seeded bug / variant / mutation discovery API 合同。UI 目前只做最小链路：对选中 task 发起 mutation dry-run、展示 selected/excluded candidate、由用户选择 selected candidate 并提交 probe JSON 调用 `confirm-selected`。由于 dry-run API 返回 discovery 而不是完整 audit report，UI 会基于当前 task、dry-run 参数和 discovery 组装 `v2.mutation_discovery_audit`；这不是信任前端授权，服务端 confirm 仍会按 audit report 参数重新 discovery 并逐字段比对 candidate。UI 不提供绕过 confirmation 的 `auto_mutation` variant 自由创建入口。

Dataset UI 也提供普通 authoring 入口，用于创建 eval task、seeded bug 和 `source=seeded_bug` 的 patch variant；前端会拒绝 `ground_truth.source=auto_mutation`，后端普通 variant API 仍是最终防线。

Dataset UI 的结构状态只基于当前 API 返回体派生，检查 task、target scope、seeded bug、variant 和 ground truth 是否缺口；它不是 replay 结果，也不把 incomplete/provider_failure 等审计状态解释为 0 捕获。

## 4. 当前测试证据

已验证：

```powershell
conda run -n trace pytest backend/tests/test_source_context.py -q
conda run -n trace pytest backend/tests/test_contract_guard.py -q
```

本轮合并后两组一起验证：

```text
116 passed
```

新增覆盖：

- target function 使用模块级常量时，source context 只切入被引用常量，不带无关常量。
- route oracle 使用不存在的 path 参数值时，在有常量证据的情况下被 Contract Guard 拒绝。
- route oracle 使用存在的 path 参数值时继续放行。
- route case 的真实 HTTP 请求路径必须命中声明的目标 route template，避免 cases 元数据绑定目标路由但测试实际请求其它 endpoint。
- 当目标 route 合同显式带 `GET/POST/...` 时，Contract Guard 会拒绝请求路径命中但 HTTP method 不匹配的 route case。
- 当已有请求字段证据时，Contract Guard 会拒绝 route test 在 `params=` 中使用无证 query 字段。
- 当没有任何请求字段证据时，Contract Guard 会拒绝 route test 直接使用 `json=` 或 `params=` 构造请求字段。
- route handler 签名中的普通 query 参数会作为请求字段证据传给 Guard；`Depends`、Request/Response 和请求模型入参不会被误当成 query 字段。
- audit-only EvaluationEvent 不进入 source context prompt，只留下过滤风险说明。
- mutation confirmation 拒绝伪造/过期 audit report 中的 selected candidate。
- mutation confirmation 拒绝非 `auto_mutation` 的 bug_type。
- 普通 variant API 拒绝绕过 confirmation 创建 `auto_mutation` ground truth。


补充验证：

```powershell
conda run -n trace pytest backend/tests/test_evaluation_events.py -q
conda run -n trace pytest backend/tests/test_context_builder_guard_inputs.py backend/tests/test_reflection_prompt_context.py backend/tests/test_evaluation_events.py -q
conda run -n trace pytest backend/tests/test_stage3_evaluation_seed_and_api.py -q
```

```text
5 passed
9 passed
6 passed
```

## 5. 后续 B 线候选

这些是 B 线候选，不是 A 线阻塞项：

- 继续机械拆分 `source_context.py`，优先拆 dependency/support context 和 retrieval provider glue。
- Dataset UI 接后端已有 task / seeded bug / variant / mutation discovery confirm API。
- Contract Guard 继续围绕源码证据补窄规则，先写失败测试再实现。
- mutation discovery v0 保持最小链路，不宣传为完整 mutation engine。
