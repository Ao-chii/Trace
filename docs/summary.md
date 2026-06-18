# TRACE V2 本轮完善总结

## 这轮主要解决了什么

这轮重点把 V2 从“能展示已有评测结果”推进到“本地可以创建、运行、查看一轮实验”，同时加固了评测结果是否可信的口径。

简单说，现在实验页不再只是一个只读列表。你可以在前端创建实验，选择数据集、策略、重复次数和模型配置，然后启动实验。实验跑完后，可以继续在详情页看捕获率、clean run、replay、artifact、nodeid 和逐 bug 捕获证据。

## 后端完善

### 1. 数据库初始化更稳

新增了 `backend/scripts/init_db.py`。

它会自动处理两种情况：

- 新数据库：正常执行 Alembic 迁移。
- 旧数据库已经有表但没有 `alembic_version`：自动补 Alembic 版本标记，避免再次出现 `runtime_profiles already exists`。

同时，`docker-compose.yml`、`.env.example` 和 `.env.local` 的默认数据库配置已经统一为：

```text
postgresql+psycopg://postgres:root@127.0.0.1:5432/trace
```

### 2. Clean flaky gate

V2 实验现在会在 clean snapshot 上做稳定性检查。

以前流程是：

```text
生成测试集 -> clean replay 一次 -> replay 到 bug variants
```

现在流程变成：

```text
生成测试集 -> clean replay -> clean 连续复跑稳定性检查 -> replay 到 bug variants
```

默认 clean replay 会验证 3 次。只有三次的 pytest nodeid 集合和每个 nodeid 的结果都稳定，才会进入 bug variant replay。

如果 clean 测试本身不稳定，这个 clean unit 会被标记为：

```text
invalid_test_set / flaky_clean_replay
```

这样不稳定测试不会混进 mutation score，也不会被误解释成模型捕获能力差。

### 3. Repeat 捕获比例

以前 capture matrix 是布尔值：

```text
某个 bug 在任一 repeat 被捕获 => true
```

这会有一点夸大稳定性的风险。

现在后端额外返回：

```text
capture_matrix_counts
```

它记录每个 bug / 每个策略：

- 捕获了几次
- 总共有几个可评价 repeat
- 捕获比例是多少

前端矩阵现在会显示类似：

```text
已捕获 2/3
未捕获 0/3
```

旧的 `capture_matrix` 仍然保留，用来兼容已有页面和脚本。

### 4. Experiment metrics 仍保持可审计

这轮没有把指标改成前端临时计算。

核心结果仍然来自：

- `experiment_clean_runs`
- `test_replays`
- `experiment_replay_runs`
- generated test set artifact
- pytest case result

也就是说，前端只是展示，指标真相仍在 DB 和 artifact 里。

## 前端完善

### 1. 实验页可以创建实验

实验列表页新增了创建区域。

可以填写：

- 实验 ID，可选
- 实验名称
- 数据集
- 策略版本
- 重复次数
- provider
- model

创建后会调用：

```text
POST /api/v1/experiments
```

创建成功后跳转到实验详情。

### 2. 实验页可以启动实验

已有的启动按钮仍然保留。

草稿、排队、取消状态的实验可以从列表页启动：

```text
POST /api/v1/experiments/{experiment_id}/runs
```

### 3. 数据集页可以创建数据集

数据集详情页新增了一个创建数据集表单。

可以填写：

- 数据集 ID，可选
- 名称
- 版本
- 描述
- project snapshot ids

创建后会调用：

```text
POST /api/v1/eval-datasets
```

注意：这只是创建 dataset 外壳和关联 snapshot，不是完整 seeded bug 编辑器。

### 4. 捕获矩阵展示 repeat 比例

实验详情页的 capture matrix 现在不仅展示“捕获 / 未捕获”，还展示 repeat 计数，例如：

```text
已捕获 3/3
未捕获 0/3
```

这比单纯打勾更诚实。

## 当前完整项目能实现什么

现在本地可以完成这条 V2 主链路：

```text
初始化数据库
-> seed 策略和 demo 数据集
-> 前端创建 experiment
-> 启动 experiment
-> clean run 生成测试集
-> clean replay 和 flaky gate
-> bug variant replay
-> DB 聚合 metrics
-> 前端查看 capture matrix / clean run / replay evidence
```

也可以继续跑普通 run：

```text
创建项目
-> 创建 snapshot
-> 创建 test plan
-> 启动 run
-> 查看 trace / pytest result / report
```

## 仍然没有做的事

这些不是本轮完成范围，也不应该伪装成已完成：

- Docker executor 未做，V2.1 仍是本地 subprocess，不是安全沙箱。
- 自动 mutation / sampled mutation score 未做。
- LSP、ast-grep、多 provider retrieval 排序没有完整接入。
- 完整 seeded bug 创建器没有做，目前前端只支持创建 dataset，不支持在 UI 里创建 task / seeded bug / variant。
- Prompt version、tool schema version、runtime profile 的完整管理页面还没有做。

## 本地使用建议

首次启动或数据库异常时：

```powershell
cd D:\17524\MyCode\Trace
docker compose up -d

cd backend
python scripts\init_db.py
python scripts\seed_strategies.py
python scripts\seed_eval_demo.py
```

启动后端：

```powershell
cd D:\17524\MyCode\Trace\backend
python scripts\run_api.py
```

启动 worker：

```powershell
cd D:\17524\MyCode\Trace\backend
python scripts\run_worker.py
```

启动前端：

```powershell
cd D:\17524\MyCode\Trace\frontend
npm run dev
```

打开：

```text
http://127.0.0.1:5186/#/experiments
```

