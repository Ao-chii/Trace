<p align="center">
  <img src="assets/logo.svg" alt="TRACE logo" width="450">
</p>

TRACE（Test-generation Reflective Agent for Comparative Evaluation）是一个面向 Python/FastAPI + pytest 项目的测试生成、执行、追踪和评测平台。

TRACE 将测试生成做成一条可运行、可审计、可评测的工程链路：分析目标项目，生成 pytest，执行测试，记录 trace，生成报告，并在 benchmark 数据集上用 seeded bug replay 比较不同 Agent 策略的效果。项目有两条主要使用路径：

1. 普通项目测试生成

   对一个 Python/FastAPI 项目创建测试计划，让 Agent 分析目标代码、生成 pytest、执行测试、记录失败与报告。这个路径适合体验“生成测试并真实运行”的产品功能。
2. Benchmark 策略评测

   使用带 seeded bugs 的数据集创建 experiment。系统会先在 clean 项目上生成并验证测试，再把同一批冻结测试重放到 bug variants 上，计算 capture rate、false positive rate、invalid test set 等指标。这个路径适合比较 Direct、Plan-and-Execute、ReAct + Reflection 等策略。

普通项目没有标准答案时，TRACE 可以证明测试被生成和执行，但不能严肃计算缺陷捕获率。只有 benchmark/dataset 路径有 seeded bug 和 oracle，指标才有明确分母和证据。

## 核心能力

- 项目分析：通过受控工具读取文件、搜索代码、分析函数、路由、模型和 fixture。
- 测试生成：根据目标范围和策略生成 pytest 测试文件。
- Contract Guard：在测试写入和执行前拦截弱断言、目标漂移、未知 fixture、越界请求字段、缺少业务 oracle 等无效输出。
- pytest 执行：运行生成测试并收集用例级结构化结果。
- Reflection：ReAct + Reflection 策略可在失败后进行一次受约束修复，禁止通过 skip、空测试或删断言来“变绿”。
- Trace 审计：记录 stage、tool call、LLM 输入输出、生成文件、pytest 结果、attempt 和 run event。
- 报告生成：输出结构化 report 和可读 Markdown 报告。
- 策略对比：支持 Direct、Plan-and-Execute、ReAct + Reflection。
- Seeded bug evaluation：在 clean run 与 variant replay 之间计算捕获率、误报率、成本和证据链。
- Web UI：提供项目、计划、运行详情、trace timeline、pytest results、dataset、experiment、metrics、evidence 和 report 页面。

## 项目架构图

![系统架构](assets\trace_overall_architecture.png)

## 内置 Demo Benchmark

TRACE 内置一个可直接运行的 demo benchmark，不只是 Docker Compose 壳子。

Docker Compose 启动时，后端入口脚本会自动执行：

```text
scripts/init_db.py
scripts/seed_strategies.py
scripts/seed_eval_demo.py
```

这会初始化：

- 三个内置策略版本：`sv-direct-v1`、`sv-plan-v1`、`sv-react-v1`
- demo dataset：`dataset-demo-v2`
- demo benchmark suite：3 个 task、16 个 seeded bug variants
- 默认 MockLLM：`mock / mock-1`

因此，即使没有真实 LLM key，也可以用 Docker Compose 跑通 Web 产品栈、dataset 页面、experiment 创建、worker 执行、replay、metrics 和 report。

注意两个 demo 概念不要混：

- `dataset-demo-v2` 是后端数据库里的真实 demo benchmark，可以通过 API、Worker 和 pytest replay 跑出指标。
- 前端 `VITE_TRACE_DATA_SOURCE=demo` 是静态 UI 预览数据，只用于看页面，不代表真实 API、数据库、pytest 或 LLM 状态。

## 技术栈

- Backend：FastAPI、Pydantic、SQLAlchemy、Alembic
- Worker / Queue：Celery、Redis
- Database：PostgreSQL
- Frontend：Vue 3、TypeScript、Vite
- Test / Evaluation：pytest、seeded bug replay、mutation discovery
- LLM：MockLLM、OpenAI Responses API、OpenAI Chat Completions 兼容接口
- Delivery：Docker Compose

## 快速体验：Docker Compose

推荐从 Docker Compose 开始。它会启动完整产品栈：

```text
PostgreSQL + Redis + Backend API + Worker + Frontend
```

默认配置使用 MockLLM，不需要 API key，适合演示和 smoke test。

### 前提

- 已安装 Docker Desktop。
- Docker Desktop 已启动，并使用 Linux containers。
- 本机端口 `5186`、`8000`、`5432`、`6379` 未被占用。

### 启动

在仓库根目录执行：

```powershell
docker compose up --build
```

启动成功后访问：

```text
Frontend: http://127.0.0.1:5186
API:      http://127.0.0.1:8000
Health:   http://127.0.0.1:8000/healthz
```

### Smoke 检查

基础检查：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_smoke.ps1
```

同时验证 Worker 和最小 evaluation 闭环：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_smoke.ps1 -RunExperiment
```

三策略 benchmark：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_benchmark.ps1
```

生成 repeat=3 的稳定 benchmark 证据：

```powershell
$id = "compose-benchmark-suite-r3-" + (Get-Date -Format "yyyyMMddHHmmss")
powershell -ExecutionPolicy Bypass -File scripts/compose_benchmark.ps1 -ExperimentId $id -RepeatCount 3 -OutputPath "docs\evidence\$id.json"
```

脚本会检查 `dataset-demo-v2`、runtime profile、clean run、variant replay、metrics、replay LLM 调用次数等关键链路。

### Web 演示路径

1. 打开 `http://127.0.0.1:5186`。
2. 确认数据源是 API，而不是静态 demo。
3. 进入 Dataset 页面，打开 `dataset-demo-v2`。
4. 点击创建 experiment，确认 dataset 已预选。
5. LLM option 选择 `MockLLM / mock-1`。
6. Strategy 选择 Direct、Plan、ReAct，repeat count 可以先用 1。
7. 创建 experiment 后进入详情页，点击 Start。
8. 等待状态进入 completed。
9. 查看 Metrics、Evidence、Report、Export 和策略对比结果。

### 停止和清理

停止服务但保留数据库数据：

```powershell
docker compose down
```

停止服务并删除 named volumes：

```powershell
docker compose down -v
```

Compose 使用的 named volumes：

- `trace_postgres_data`
- `trace_redis_data`
- `trace_experiment_work`

## 配置真实 LLM

Compose 默认使用 MockLLM。如果要接入真实模型，在仓库根目录复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`：

```env
TRACE_LLM_PROVIDER=openai
TRACE_LLM_MODEL=gpt-5
TRACE_LLM_API_KEY=your-api-key
TRACE_LLM_BASE_URL=https://api.openai.com/v1
```

OpenAI Chat Completions 兼容接口示例：

```env
TRACE_LLM_PROVIDER=openai_chat_compat
TRACE_LLM_MODEL=your-model
TRACE_LLM_API_KEY=your-api-key
TRACE_LLM_BASE_URL=https://example.com/v1
```

让 API 和 Worker 重新读取配置：

```powershell
docker compose up -d --force-recreate api worker
```

检查后端识别到的 LLM options：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/llm-options
```

不要提交 `.env`、`.env.local`、`backend/llm.config.json` 或任何真实密钥。

## 本地开发运行

本地开发适合改后端、前端或测试。只想体验产品功能时，优先使用 Docker Compose。

### 后端依赖

建议使用 Python 3.11 或 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

也可以使用 Conda，只要保证当前环境安装了 `backend/requirements.txt` 中的依赖即可。

### 启动数据库和 Redis

可以只启动基础服务：

```powershell
docker compose up -d postgres redis
```

复制本地配置：

```powershell
Copy-Item .env.example .env.local
```

`.env.local` 默认连接本机 PostgreSQL 和 Redis：

```text
TRACE_DB_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/trace_test
TRACE_REDIS_URL=redis://127.0.0.1:6379/0
TRACE_API_HOST=127.0.0.1
TRACE_API_PORT=8000
```

### 初始化数据

在 `backend` 目录执行：

```powershell
python scripts/init_db.py
python scripts/seed_strategies.py
python scripts/seed_eval_demo.py
```

### 启动 API、Worker 和前端

终端 1：后端 API。

```powershell
cd backend
python scripts/run_api.py
```

终端 2：Worker。

```powershell
cd backend
python scripts/run_worker.py
```

Windows 下 worker 默认使用 Celery `solo` pool，适合本地开发和演示。

终端 3：前端。

```powershell
cd frontend
npm install
npm run dev
```

默认地址：

```text
Frontend: http://127.0.0.1:5186
API:      http://127.0.0.1:8000
```

如果后端端口不同，启动前端前设置代理目标：

```powershell
$env:VITE_TRACE_API_PROXY_TARGET="http://127.0.0.1:8001"
npm run dev
```

### 本地 LLM 配置

本地后端可以读取环境变量，也可以读取 `backend/llm.config.json`。

```powershell
cd backend
Copy-Item llm.config.example.json llm.config.json
```

示例：

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-5",
  "api_key": "your-api-key",
  "temperature": 0,
  "max_output_tokens": 8192
}
```

## 静态前端 Demo 模式

如果只想预览 UI，不启动后端 API，可以使用静态 demo 数据：

```powershell
cd frontend
$env:VITE_TRACE_DATA_SOURCE="demo"
npm run dev
```

这个模式只展示前端内置数据，不会创建真实 run，不会调用 Worker，不会执行 pytest，也不会产生真实 benchmark 指标。

## 测试和验证

后端测试：

```powershell
python -m pytest backend/tests -q --rootdir backend -p no:cacheprovider
```

前端构建检查：

```powershell
cd frontend
npm run build
```

异步 run smoke：

```powershell
cd backend
python scripts/smoke_async_run.py
```

评测 harness：

```powershell
python eval/harness/run_eval.py
```

输出位置：

```text
eval/results/comparison.md
eval/results/comparison.json
```

## 主要概念

- Project：一个被测项目。
- Project Snapshot：一次运行使用的项目快照，防止目标代码漂移。
- Test Plan：普通测试生成任务，描述目标范围、目标说明、预算和默认策略。
- Test Run：一次实际 Agent 执行，包含 strategy snapshot、runtime snapshot、trace、attempt、pytest 结果和 report。
- Strategy Version：不可变策略版本，当前内置 Direct、Plan-and-Execute、ReAct + Reflection。
- Dataset：benchmark 数据集，包含 task、seeded bug 和 variant。
- Experiment：在同一个 dataset 上用一个或多个策略重复运行并计算指标。
- Clean Run：在无缺陷版本上生成并验证测试。
- Variant Replay：不再调用 LLM，只把冻结测试重放到 bug variant 上。
- Capture Rate：clean 通过且 variant 失败时，认为测试捕获了该 seeded bug。
- False Positive Rate：clean 版本上测试失败的比例。
- Contract Guard：系统级规则闸门，不是 prompt 建议；违规输出会进入 pipeline reject 或 invalid test set 路径。

## 项目结构

```text
TRACE/
  backend/
    app/
      agents/        Agent 策略、LLM 适配、prompt、Contract Guard、报告
      api/           FastAPI 路由
      core/          错误码、ID、基础配置
      db/            数据库 engine、session、schema 初始化
      models/        SQLAlchemy ORM 模型
      recorders/     运行记录落库实现
      repositories/  数据访问层
      schemas/       API、工具、trace、策略和评测数据契约
      services/      项目、运行、实验、评测、source context 等业务逻辑
      tools/         list/read/search/analyze/write/run_pytest 等受控工具
      workers/       Celery app 和异步任务
    alembic/         数据库迁移
    docker/          容器入口脚本
    scripts/         初始化、启动、seed、smoke 脚本
    tests/           后端测试
  frontend/
    src/
      api/           前端 API client
      components/    运行详情、trace、pytest、report 等组件
      demo/          静态 UI demo 数据
      pages/         页面入口
  eval/
    demo/            demo benchmark 项目和 seeded bugs
    harness/         评测执行与指标聚合
    results/         harness 输出
  docs/              设计文档、验收报告和证据
  scripts/           Compose smoke 和 benchmark 脚本
```

## 关键源码入口

- API 创建 run：`backend/app/api/routes/test_runs.py`
- run 创建、入队和同步执行：`backend/app/services/test_runs.py`
- Worker 任务：`backend/app/workers/tasks.py`
- Agent 状态机：`backend/app/agents/orchestrator.py`
- 三策略共享执行步骤：`backend/app/agents/strategies/common.py`
- Direct 策略：`backend/app/agents/strategies/direct.py`
- Plan-and-Execute 策略：`backend/app/agents/strategies/plan_execute.py`
- ReAct + Reflection 策略：`backend/app/agents/strategies/react_reflection.py`
- Contract Guard：`backend/app/agents/contract_guard.py`
- Source Context：`backend/app/services/source_context.py`
- Experiment 执行：`backend/app/services/experiments.py`
- Compose benchmark：`scripts/compose_benchmark.ps1`

## 边界与限制

- 当前主要面向 Python/FastAPI + pytest 项目。
- Docker Compose 解决的是 TRACE 产品栈交付运行，不等于通用 Docker executor。
- 默认 runtime profile 仍是 `local_subprocess`，在本机或 worker 容器内用子进程跑 pytest，不是强安全沙箱。
- 生成测试本质上仍是 Python 代码，不应直接用于不可信项目。
- MockLLM 用于稳定演示和 benchmark smoke，不应和真实 LLM 指标混讲。
- 普通项目 run 没有 seeded bug oracle 时，不应宣称 capture rate 这类强评测指标。
- 真实 LLM 的效果、成本和稳定性取决于模型、prompt、上下文质量和 API 服务状态。
