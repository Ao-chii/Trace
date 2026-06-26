# TRACE Docker Compose 交付运行说明

本文描述的是“TRACE 作为软件运行”的 Docker Compose 交付路径。它默认使用 MockLLM，目标是让接收方一条命令启动完整 Web 产品栈：

```text
PostgreSQL + Redis + Backend API + Worker + Frontend
```

这不是 Docker executor 验收。默认评测执行仍走 `local_subprocess`，Docker executor 作为后续隔离执行增强单独验证。

## 1. 前提

- 已安装 Docker Desktop。
- Docker Desktop 已启动，并切到 Linux containers。
- `docker info` 能看到 Server 信息。
- 本机 5186、8000、5432、6379 端口未被占用。

## 2. 一键启动

在仓库根目录执行：

```powershell
docker compose up --build
```

首次启动会：

1. 构建后端镜像和前端镜像。
2. 启动 PostgreSQL / Redis。
3. 后端 API 等待 DB/Redis ready。
4. 执行 `scripts/init_db.py`、`scripts/seed_strategies.py`、`scripts/seed_eval_demo.py`。
5. 启动 API、Worker 和 nginx 前端。

启动成功后访问：

```text
http://127.0.0.1:5186
```

API 也会暴露在：

```text
http://127.0.0.1:8000
```

## 3. Smoke 检查

另开一个终端执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_smoke.ps1
```

如果要同时验证 Worker 能消费队列并跑完一次最小评测闭环，执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_smoke.ps1 -RunExperiment
```

如果要跑三策略 benchmark，执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose_benchmark.ps1
```

如果要生成更像验收 benchmark 的稳定证据，建议显式 repeat=3：

```powershell
$id = "compose-benchmark-suite-r3-" + (Get-Date -Format "yyyyMMddHHmmss")
powershell -ExecutionPolicy Bypass -File scripts/compose_benchmark.ps1 -ExperimentId $id -RepeatCount 3 -OutputPath "docs\evidence\$id.json"
```

期望输出：

```text
TRACE Compose smoke passed.
```

该脚本检查：

- `/healthz` 返回 `ok=true`。
- `GET /api/v1/llm-options` 中 MockLLM 可选。
- `GET /api/v1/eval-datasets` 中存在 `dataset-demo-v2`。
- 前端页面返回 TRACE app shell。

带 `-RunExperiment` 时额外检查：

- 创建 `sv-direct-v1` 单策略 experiment。
- 启动 Worker 执行。
- 等待 `completed`。
- 按 `dataset-demo-v2` 的 task / variant 数动态验证 clean run 和 variant replay records。
- 验证 `metric_status=ok`，replay `llm_calls=[0]`。

`compose_benchmark.ps1` 额外检查：

- 创建 Direct / Plan / ReAct 三策略 experiment。
- 按 `dataset-demo-v2` 的 task / variant 数动态验证 clean run 和 variant replay records。
- 验证三行 `metric_status=ok`，replay `llm_calls=[0]`。

## 4. 演示路径

打开 `http://127.0.0.1:5186` 后：

1. 确认右上角数据源是 `API`。
2. 进入“数据集”，打开 `dataset-demo-v2`。
3. 点击“创建实验”，确认 Dataset 自动预选。
4. LLM option 选择 `MockLLM / mock-1`。
5. Strategy 选择 Direct / Plan / ReAct，repeat count 为 1。
6. 创建 experiment 后进入详情页，点击 Start。
7. 等待 completed，查看 Metrics / Evidence / Report / Export。

## 5. 真实 LLM 可选配置

默认 compose 使用 MockLLM，不需要密钥。

如果要在容器中展示真实 LLM option，推荐在仓库根目录创建 `.env`：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

然后在 `.env` 中填写：

```env
TRACE_LLM_PROVIDER=openai_chat_compat
TRACE_LLM_MODEL=your-model
TRACE_LLM_API_KEY=your-api-key
TRACE_LLM_BASE_URL=https://example.com/v1
```

如果栈还没启动，按第 2 节执行 `docker compose up --build`。如果栈已经在运行，让 API / Worker 重新读取配置：

```powershell
docker compose up -d --force-recreate api worker
```

验证后端是否识别真实 LLM：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/llm-options
```

不要把真实密钥写入 `.env.example` 或任何会提交到 Git 的文件。

## 6. 数据与清理

Compose 使用这些 named volumes：

- `trace_postgres_data`：数据库。
- `trace_redis_data`：Redis。
- `trace_experiment_work`：API 与 Worker 共享的 experiment workspace。

停止服务：

```powershell
docker compose down
```

连同数据一起删除：

```powershell
docker compose down -v
```

## 7. 边界

- 这条路径证明 TRACE 产品栈可以容器化运行。
- 默认 MockLLM 用于稳定演示，不和真实 LLM 指标混讲。
- 默认 runtime profile 仍是 `local_subprocess`，它在 worker 容器内运行本地子进程，不等于 Docker executor。
- Docker executor、远程 Runner、对象存储和完整 OTel 不属于本交付路径。

## 8. 本机验证记录

2026-06-22 已在本机 Docker Desktop Linux engine 上完成交付栈验证：

| 项 | 结果 |
| --- | --- |
| `docker info` | Server 可用 |
| `docker compose config` | passed |
| `docker compose up --build -d` | passed |
| API | `trace-api` healthy，`0.0.0.0:8000->8000` |
| Frontend | `trace-frontend` healthy，`0.0.0.0:5186->80` |
| PostgreSQL | `trace-postgres` healthy |
| Redis | `trace-redis` healthy |
| Basic smoke | `TRACE Compose smoke passed.` |
| Worker/eval smoke | `compose-smoke-20260622113028` completed |
| Worker/eval smoke metrics | `direct` / `metric_status=ok` / `capture_rate_mean=0.875` / `false_positive_rate=1.0` |
| Worker/eval smoke replay | 25 replay runs，16 variant replay records，`llm_calls=[0]` |
| Real LLM option | `openai_chat_compat` / `deepseek-v4-flash`，`selectable=true` |
| Real LLM smoke | `real-llm-smoke-20260622093052` completed |
| Real LLM smoke metrics | `direct` / `metric_status=ok` / `capture_rate_mean=1.0` / `false_positive_rate=0.0` |
| Real LLM smoke replay | 9 replay runs，`llm_calls=[0]` |
| Compose benchmark suite | `compose-benchmark-suite-r3-20260622112519` completed，repeat=3 |
| Compose benchmark dataset | 3 tasks，16 bug variants |
| Compose benchmark metrics | Direct 0.875 / Plan 1.0 / ReAct 1.0，三行 `metric_status=ok` |
| Compose benchmark replay | 225 replay runs，144 variant replay records，`llm_calls=[0]` |

备注：首次构建期间 Docker Hub token 请求曾短暂超时；单独 `docker pull python:3.12-slim`、`docker pull node:20-alpine`、`docker pull nginx:1.27-alpine` 后继续构建成功。
