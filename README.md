<p align="center">
  <img src="assets/logo.svg" alt="TRACE logo" width="450">
</p>

TRACE（Test-generation Reflective Agent for Comparative Evaluation）是一个面向 Python/FastAPI 后端项目的测试生成 Agent 原型。它把“模型生成 pytest 文本”推进成一条可执行、可追踪、可评测的工程闭环：

```text
分析项目 -> 生成测试 -> 执行 pytest -> 读取结构化失败 -> 一次受约束 Reflection -> 生成报告 -> seeded bug 评测
```

当前仓库已经完成：

- **A 分工：Agent + 工具契约 + 评测 harness**
- **B 分工 V1：PostgreSQL 持久化、FastAPI 管理 API、Celery + Redis 异步执行主链路**

前端页面仍属于 C 线后续接入范围。

## 当前状态

- M0：工具契约、路径白名单、pytest 结构化执行已完成。
- M1：MockLLM、三策略 Agent loop、Reflection 契约、trace/report 已完成。
- M2：demo seeded bugs、污染隔离评测 harness、策略对比表已完成。
- 真实 LLM：已接入官方 OpenAI Responses 与 OpenAI Chat Completions 兼容入口；provider 失败会脱敏记录并用 `LLM_PROVIDER_ERROR` 标记。
- 当前测试：A 线测试 + B 线数据库/API/Celery 测试已通过。
- 评测产物：`eval/results/comparison.md`、`eval/results/comparison.json`。
- B 线已验证一次真实异步 smoke run：`queued -> completed`，并成功落库 `run / events / attempts / pytest_results / report`。

本项目以 `需求分析.md`、`系统设计.md`、`V1实施清单.md` 为技术依据。`初步构想.md` 是旧草稿，不作为当前实现依据。

## 目录结构

```text
TRACE/
  backend/
    app/
      core/       # 错误码、ID
      schemas/    # 工具、trace、agent、record、strategy 契约
      tools/      # list/read/analyze/write/run_pytest + subprocess executor
      agents/     # MockLLM、三策略、orchestrator、report、contract guard
      api/        # FastAPI 路由
      db/         # SQLAlchemy / 配置 / session
      models/     # 14 张 V1 表
      repositories/
      services/
      workers/    # Celery + Redis 异步执行
      recorders/  # SQLAlchemyRunRecorder
    alembic/      # 数据库迁移
    scripts/      # 启动 API/worker、seed、smoke 验证
    tests/        # 单元/集成测试
  eval/
    demo/clean/   # demo 被测项目：shop 定价函数 + FastAPI 路由
    demo/bugs.py  # 6 个 seeded bug ground truth
    harness/      # 干净生成 + 变体重放 + 指标聚合
    results/      # comparison.md/json
```

## 快速开始

建议使用 Python 3.11。Windows 上如果系统 `python` 是 Microsoft Store 占位符，请直接使用实际解释器路径。

```bash
cd TRACE
pip install -r backend/requirements.txt
```

如果要使用 B 线 V1 的数据库和异步执行，还需要本机启动：

- PostgreSQL
- Redis

本仓库本地默认从根目录 `.env.local` 读取配置，常用变量如下：

```text
TRACE_DB_URL=postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/trace
TRACE_REDIS_URL=redis://127.0.0.1:6379/0
TRACE_API_HOST=127.0.0.1
TRACE_API_PORT=8000
```

运行测试：

```bash
python -m pytest backend/tests -q --rootdir backend -p no:cacheprovider
```

只跑 B 线相关测试：

```bash
cd backend
python -m pytest tests/test_db_models.py tests/test_sqlalchemy_recorder.py tests/test_report_sqlalchemy.py tests/test_api_async_run.py tests/test_api_retry_cancel.py -q --rootdir . -p no:cacheprovider
```

如果本机临时目录有权限问题，可指定仓库外或可写临时目录：

```bash
python -m pytest backend/tests -q --rootdir backend -p no:cacheprovider -o tmp_path_retention_policy=none --basetemp="D:/tmpdir/Temp/trace_pytest"
```

运行评测：

```bash
python eval/harness/run_eval.py
```

评测会生成：

```text
eval/results/comparison.md
eval/results/comparison.json
```

## B 线 V1：数据库与异步执行

### 已落地能力

- PostgreSQL 14 张 V1 表已建模并接入 Alembic baseline。
- `SQLAlchemyRunRecorder` 已把 Agent 运行记录落到数据库。
- FastAPI 已提供项目、快照、计划、运行、trace、report、strategy、adoption 等接口。
- `POST /api/v1/test-plans/{plan_id}/runs` 已改为 **Celery + Redis 后台执行**。
- `retry` 会创建新的 `test_runs` 记录并重新入队。
- `cancel` 当前是 V1 软取消，只改状态，不杀已有子进程。

### 启动方式

在 `backend/` 目录下：

```bash
python scripts/run_api.py
```

另开一个终端：

```bash
python scripts/run_worker.py
```

Windows 下 worker 默认走 `--pool=solo`，这是为了让 Celery 在本地环境更稳定。

也可以直接用一个脚本同时拉起开发用 API 和 worker：

```bash
python scripts/spawn_dev_services.py
```

日志会写到：

```text
backend/.tmp-logs/api.out.log
backend/.tmp-logs/api.err.log
backend/.tmp-logs/worker.out.log
backend/.tmp-logs/worker.err.log
```

### 异步 smoke 验证

在 API 和 worker 都已启动的前提下，执行：

```bash
python scripts/smoke_async_run.py
```

这个脚本会：

1. 创建项目
2. 创建 snapshot
3. 创建 test plan
4. 调用 `POST /runs` 入队
5. 轮询 run 状态直到结束
6. 读取 events 和 report，确认真实异步链路完成

默认使用的联调夹具项目在：

- [backend/tests/fixtures/async_demo_project/calc.py](/D:/17524/MyCode/Trace/backend/tests/fixtures/async_demo_project/calc.py)

### 当前异步工作流

`POST /api/v1/test-plans/{plan_id}/runs` 的处理流程如下：

1. API 在 `test_runs` 中创建一条 `queued` 记录。
2. API 追加一条 `run_enqueued` 事件。
3. API 调用 Celery 任务 `trace.execute_run`，只把 `run_id` 和可选预算覆盖传进队列。
4. Celery worker 从 Redis 队列 `trace_test_runs` 取出任务。
5. worker 根据 `run_id` 回查数据库，加载 plan、snapshot、strategy、project。
6. worker 构造 `ToolContext`、`StrategyVersionSpec`、`SQLAlchemyRunRecorder`。
7. worker 调用 A 线已有的 `execute_run(...)`。
8. Agent 在执行过程中持续写入：
   - `test_runs`
   - `run_plan_items`
   - `run_attempts`
   - `trace_steps`
   - `run_events`
   - `generated_test_files`
   - `generated_test_cases`
   - `pytest_case_results`
   - `run_artifacts`
   - `test_reports`
9. API 侧通过 `GET /api/v1/test-runs/{run_id}` 和相关查询接口读取实时/最终结果。

对应关键代码：

- [backend/app/api/routes/test_runs.py](/D:/17524/MyCode/Trace/backend/app/api/routes/test_runs.py)
- [backend/app/services/test_runs.py](/D:/17524/MyCode/Trace/backend/app/services/test_runs.py)
- [backend/app/workers/tasks.py](/D:/17524/MyCode/Trace/backend/app/workers/tasks.py)
- [backend/app/workers/celery_app.py](/D:/17524/MyCode/Trace/backend/app/workers/celery_app.py)
- [backend/app/recorders/sqlalchemy_recorder.py](/D:/17524/MyCode/Trace/backend/app/recorders/sqlalchemy_recorder.py)

接入真实 LLM API：

```powershell
$env:TRACE_LLM_API_KEY="你的 OpenAI API key"
python eval/harness/run_eval.py --provider openai --list-models
python eval/harness/run_eval.py --real --provider openai --model gpt-5 --smoke
```

真实 API 只通过环境变量读取 key；最好不要把 key 写进命令、README、`.env` 示例或提交记录。`--list-models` 可验证当前 key 能访问哪些模型，`--smoke` 只跑 1 个策略 × 1 次重复，用来确认真实调用能走通。LLM HTTP 层会对连接抖动、429、5xx 做有限退避重试；401/400 等配置错误快速失败并脱敏报错。

OpenAI Chat Completions 兼容端点（如 DeepSeek/Qwen/企业兼容网关）走 `openai_chat_compat`，必须显式给 base URL 和模型：

```powershell
$env:TRACE_LLM_API_KEY="你的兼容端点 key"
python eval/harness/run_eval.py --provider openai_chat_compat --base-url "https://api.deepseek.com/v1" --list-models
python eval/harness/run_eval.py --real --provider openai_chat_compat --base-url "https://api.deepseek.com/v1" --model "deepseek-chat" --smoke
```

确认冒烟跑通后，再跑全量真实评测：

```powershell
python eval/harness/run_eval.py --real --provider openai --model gpt-5 --repeats 3
```

`~/.codex/config.toml` 和 `~/.codex/auth.json` 是 Codex CLI 的个人配置；TRACE 不依赖也不读取其中的 key。运行评测时优先用上面的环境变量，避免把个人凭据和项目代码绑定。

如果未来要接企业网关或其它官方兼容的 Responses 端点，只给 `openai` 覆盖 base URL：

```powershell
python eval/harness/run_eval.py --real --provider openai --base-url "https://api.example.com/v1" --model "模型id" --smoke
```

## 核心能力

### 工具层

Agent 只能通过白名单工具访问项目：

- `list_files`
- `read_file`
- `analyze_project`
- `write_test_file`
- `run_pytest`

`write_test_file` 和 `run_pytest` 都做 canonical path 校验，生成测试只能写入受控目录。`run_pytest` 通过自定义 pytest 插件返回结构化结果，包括 `case_results`、`failures`、`collection_errors` 和 `failure_type`。

### Agent 策略

当前实现三种策略：

- Direct：单任务生成并执行，不做规划，不做 Reflection。
- Plan-and-Execute：先拆分多个测试目标，再逐个生成和执行，不做 Reflection。
- ReAct + Reflection：单任务生成和执行，失败后允许一次受约束 Reflection。

Reflection 的红线：

- 不修改业务代码。
- 不删除核心断言。
- 不用 `pytest.skip` / `xfail` 掩盖失败。
- 不把具体断言改成空洞断言。
- 如果无法在不削弱断言的前提下变绿，应保留失败并标记疑似业务缺陷。

系统用静态 contract guard 辅助拦截常见“为了变绿而削弱测试”的行为。

### Trace 与报告

运行过程会记录：

- stage/status 变化；
- 工具调用和观察；
- LLM 结构化输出及重试日志；
- pytest 用例级结果；
- Reflection 结果；
- 最终 Markdown/JSON 报告。

评测和报告共享同一套 final attempt 选择逻辑，避免报告口径和重放口径分叉。

## 评测方法

M2 评测遵循污染隔离：

1. 在干净 demo 项目上运行 Agent，生成最终测试集。
2. 如果策略允许 Reflection，只在干净代码上进行一次修复。
3. 保存最终测试内容。
4. 为每个 seeded bug 物化独立变体目录。
5. 把干净期定型的同一批测试重放到变体目录。
6. 只有“同一 nodeid 在干净代码通过、在变体上发生 assertion failure”才算捕获。

核心指标：

- `seeded_bug_capture_rate`
- `clean_false_positive_rate`
- `cost_per_captured_bug`
- `avg_tokens`
- `avg_tool_calls`

`eval/demo/bugs.py` 中定义了 6 个 seeded bug，覆盖比较符翻转、边界错误、漏校验、错状态码四类。

## 当前评测结果

数据来自 scripted MockLLM 作者：它刻意建模三种策略的典型行为，用来验证 harness 能否区分捕获、漏判、假阳性和 Reflection 修复路径。

| 策略                | 捕获率      | 假阳性率 | 反思 | 平均 token | 平均工具调用 |
| ------------------- | ----------- | -------- | ---- | ---------- | ------------ |
| Direct v1           | 0.83（5/6） | 1.00     | 否   | 610        | 3            |
| Plan-and-Execute v1 | 1.00（6/6） | 0.00     | 否   | 2293       | 13           |
| ReAct+Reflection v1 | 1.00（6/6） | 0.00     | 是   | 1342       | 5            |

这张表能说明：

- harness 的污染隔离、nodeid 对齐、捕获率和假阳性计算可运行；
- 三种策略的控制流差异能反映到工具调用成本上；
- scripted MockLLM 可以覆盖 Direct 漏判、Plan 全覆盖、ReAct 反思修复等路径。

这张表不能说明：

- ReAct 在真实 LLM 下必然优于 Direct 或 Plan；
- 当前 token/cost 是真实模型成本；
- demo bug 捕获率可以直接外推到真实项目。

换成真实 LLM 后，指标口径不变，但捕获率、假阳性率和成本需要重新测。

## 安全边界

V1 使用本地 subprocess 执行 pytest。它提供独立工作目录、路径白名单和硬超时，但 **不是安全沙箱**。

生成测试本质上仍是 Python 代码。不能把当前执行器当作隔离恶意代码的安全边界。真正的容器隔离、禁网、资源限制和 Runner 加固属于后续版本。

## 已知边界

- 当前只覆盖 Python/FastAPI + pytest。
- 默认评测使用 MockLLM/scripted author；真实 OpenAI / Chat Compat API 结果需要另跑 `--real`。
- 当前不落数据库，不提供正式 FastAPI 管理 API。
- 当前评测走脚本，不落评测表。
- 弱断言识别仍有边界，例如 `assert x == x`、`assert result is not None`、断言藏在永不执行分支等。

## 后续建议

短期优先级：

1. B 线补强 API 响应 schema、事务边界和运行中取消能力。
2. C 线基于 report/trace/comparison JSON 做页面展示。
3. A 线用真实 OpenAI 或 Chat Compat API 跑一轮 smoke，再决定是否跑全量真实评测。

真实 LLM 已通过 `LLMClient` adapter 接入，现有工具契约、trace、Reflection contract 和 eval harness 不需要改。
