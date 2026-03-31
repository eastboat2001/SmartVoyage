# SmartVoyage

SmartVoyage 是一个面向交通出行购票场景的 AI Agent 项目，当前主线已经收敛为：

- `MCP` 负责工具与数据边界
- `Supervisor-style Multi-Agent` 负责任务编排
- `Skill Runtime` 负责按角色和能力加载 prompt skill
- `LangGraph` 负责订单链路中的状态流转、HITL 审批与恢复
- `LangSmith` 负责端到端回归与副作用校验

当前版本聚焦交通查询、交通决策和订单事务主线。

补充文档：

- `docs/ARCHITECTURE.md`：正式架构说明
- `docs/TEST_STRATEGY.md`：当前测试分层与覆盖面
- `docs/INTERVIEW_GUIDE.md`：面试讲稿与常见追问回答
- `docs/INTERVIEW_QA.md`：详细 Q&A 题库
- `docs/METRICS.md`：最新一轮 LangSmith 回归结果摘要

## 当前能力

- 查询当前时间
- 查询天气
- 查询火车票与机票
- 支持相对日期表达，例如今天、明天、后天
- 查询我的订单
- 购买交通票
- 退票
- 改签
- 基于用户偏好做 `home_city` 补问
- 执行 `transport_decision`
  - 先查天气和票务
  - 再判断高铁还是飞机更合适
  - 条件满足时继续进入下单链路
- 对下单、退票、改签执行 `HITL review + resume`

## 当前架构

当前固定为 `1` 个 Supervisor、`2` 个本地 Subagent、`2` 个 MCP 服务：

- `SmartVoyageSupervisor`
  - 意图识别
  - 跨域路由
  - 用户偏好读取
  - `transport_decision` 编排
  - HITL 恢复后的统一收尾
- `TravelReadSubagent`
  - 时间、天气、票务只读查询
  - `read_kind / weather_plan / ticket_plan`
- `OrderSubagent`
  - 下单、查单、退票、改签
  - 缺槽追问
  - HITL 审批与恢复
- `TravelReadTools`
  - 当前时间
  - 天气数据
  - 火车票与机票数据
  - Redis 只读缓存
- `OrderTools`
  - 订单创建、查询、取消、改签
  - 库存扣减与回补

Supervisor 直接持有并调用本地 subagent，MCP 只用于工具层。

## Skill Runtime

当前的 skill 是本地 runtime skill。

- skill 目录位于 `skills/`
- 每个 skill 由 `SKILL.md + assets/ + references/` 组成
- 运行时按照 `role + capability + flags` 确定性选择 skill
- `core/prompts.py` 负责按角色和能力构建运行时 Prompt，底层由 skill runtime 驱动

当前固定 4 个 skill：

- `intent-routing`
- `travel-read`
- `transport-decision`
- `order-operation`

## 当前测试基线

截至当前仓库状态：

- `18` 个 Python 测试文件
- `71` 个 Python 测试函数
- `30` 条 LangSmith 回归 case

当前回归不只验证文本输出，还会校验：

- `intent_match`
- `route_match`
- `pending_context_match`
- `response_semantic_match`
- `db_state_match`

## 目录

核心目录如下：

- `agents/`
  - `supervisor.py`
  - `travel_read.py`
  - `order.py`
- `skills/`
  - `intent-routing/`
  - `travel-read/`
  - `transport-decision/`
  - `order-operation/`
  - `runtime.py`
- `mcp_server/`
  - `mcp_travel_read_server.py`
  - `mcp_order_server.py`
- `contracts/`
  - `agent_protocol.py`
  - `structured_outputs.py`
  - `order_action_tag.py`
  - `travel_read_tag.py`
- `infra/`
  - `db.py`
  - `cache.py`
  - `json_encoder.py`
  - `persistent_checkpointer.py`
- `llm/`
  - `model_factory.py`
  - `resilient_llm.py`
- `observability/`
  - `metrics.py`
  - `request_context.py`
- `core/`
  - `config.py`
  - `logging.py`
  - `prompts.py`
  - `http.py`
  - `clock.py`
  - `errors.py`
- `langsmith_eval/`
  - `cases.json`
  - `run_langsmith_eval.py`
- `test/`
  - helper / workflow / MCP / E2E tests
- `web_app.py`
  - FastAPI Web 入口
- `main.py`
  - CLI 入口
- `run_all.py`
  - 一键启动脚本

## 环境准备

### 1. 安装依赖

```powershell
uv venv --python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 初始化 MySQL

默认配置：

- host: `localhost`
- user: `root`
- password: `123456`
- database: `travel_rag`

初始化数据库：

```powershell
Get-Content sql\create_table.sql | mysql -u root -p123456
Get-Content sql\insert_data.sql | mysql -u root -p123456
```

当前示例数据主要覆盖 `2026-03-21` 到 `2026-03-25` 的天气和票务场景。

### 3. 可选启动 Redis

如果希望启用 TravelRead 只读缓存，请启动本地 Redis，并确保 `.env` 中的下列配置可用：

- `SMARTVOYAGE_CACHE_ENABLED=1`
- `SMARTVOYAGE_REDIS_URL=redis://127.0.0.1:6379/0`

如果 Redis 不可用，系统会自动降级为 no-cache。

### 4. 配置模型

先复制模板：

```powershell
Copy-Item .env.example .env
```

至少配置一组模型接入参数。

`openai_compatible` 示例：

- `SMARTVOYAGE_PROVIDER=openai_compatible`
- `SMARTVOYAGE_BASE_URL=...`
- `SMARTVOYAGE_API_KEY=...`
- `SMARTVOYAGE_MODEL_NAME=...`

`ollama` 示例：

- `SMARTVOYAGE_PROVIDER=ollama`
- `SMARTVOYAGE_OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `SMARTVOYAGE_MODEL_NAME=...`

## 分模型灰度建议

当前支持按阶段把低风险结构化任务切到轻模型，失败时自动回退主模型。

### 配置方法

在 `.env` 中新增一组轻模型参数：

```powershell
SMARTVOYAGE_LIGHT_MODEL_PROVIDER=openai_compatible
SMARTVOYAGE_LIGHT_MODEL_NAME=qwen-turbo
SMARTVOYAGE_LIGHT_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SMARTVOYAGE_LIGHT_MODEL_API_KEY=...
SMARTVOYAGE_LIGHT_MODEL_PHASES=intent_recognition,weather_plan,ticket_plan,order_date_resolution
```

含义：

- `SMARTVOYAGE_LIGHT_MODEL_NAME` 为空时，系统不会启用轻模型路由
- `SMARTVOYAGE_LIGHT_MODEL_PHASES` 是逗号分隔的阶段白名单，只有这些阶段会先尝试轻模型
- 轻模型阶段如果调用失败、结构化输出非法或重试耗尽，会自动回退到主模型

### 当前建议边界

推荐优先下沉：

- `intent_recognition`
- `weather_plan`
- `ticket_plan`
- `order_date_resolution`

暂不建议下沉：

- `decision_plan`
- `review_decision`
- `order_operation_extract_cancel_order`
- `order_operation_extract_change_order`
- 任何会直接影响下单、退票、改签执行结果的最终判断阶段

### 推荐实验顺序

建议按下面顺序做灰度，每次只新增一个阶段：

1. `intent_recognition`
2. `weather_plan`
3. `ticket_plan`
4. `order_date_resolution`
5. `order_action_classify`

每轮都应重新跑 LangSmith 基线，重点关注：

- `30/30` 是否继续通过
- 总体 `P50 / P95 / P99`
- `transport_decision` 平均时延
- `transport_decision` 平均 token
- 是否出现新的 route / semantic / db regression

## 启动

### 1. 只启动后端服务

```powershell
.\.venv\Scripts\python.exe run_all.py
```

默认会启动：

- `TravelReadTools` on `8001`
- `OrderTools` on `8003`

### 2. 启动后端并进入 CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-cli
```

### 3. 启动后端并打开 Web 页面

```powershell
.\.venv\Scripts\python.exe run_all.py --with-web
```

页面地址：

- `http://127.0.0.1:8501`

### 4. 同时启动后端、Web 页面和 CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-web --with-cli
```

### 5. 单独启动入口

在后端服务已启动的前提下：

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe web_app.py
```

## 验证

### Web 健康检查

```powershell
curl http://127.0.0.1:8501/health
```

### 运行代码级测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py"
```

如果你只想先跑关键模块，可以优先执行：

```powershell
.\.venv\Scripts\python.exe -m unittest test.test_supervisor
.\.venv\Scripts\python.exe -m unittest test.test_order_agent_server
.\.venv\Scripts\python.exe -m unittest test.test_travel_read_mcp_server
.\.venv\Scripts\python.exe -m unittest test.test_task_model_routing
```

### 真实端到端测试

`test/test_supervisor_e2e.py` 会真实调用模型接口，因此需要：

- MCP 服务已启动
- 模型配置可用
- 显式设置 `SMARTVOYAGE_RUN_E2E=1`

```powershell
$env:SMARTVOYAGE_RUN_E2E="1"
.\.venv\Scripts\python.exe -m unittest test.test_supervisor_e2e
```

### LangSmith 回归

运行前需要：

- MCP 服务已启动
- MySQL 数据可初始化
- 模型配置可用
- `LANGSMITH_API_KEY` 已配置

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-dataset --replace-dataset
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --run
```

## CLI 冒烟建议

```text
现在几点
查询2026-03-21杭州的天气
查询2026-03-21北京到上海的高铁票
根据2026-03-21上海的天气，帮我判断从北京去上海坐高铁还是飞机更合适，不要下单
帮我预订2026-03-21北京到上海的G5二等座高铁票
查询我的订单
帮我退掉2026-03-21北京到上海的G5二等座高铁票
把我2026-03-21北京到上海的G5二等座高铁票改签到2026-03-22二等座
```

## HITL

订单副作用链路已经接入 `LangGraph interrupt + resume`。

以下操作会先进入审批态：

- 下单
- 退票
- 改签

当前实现说明：

- 使用 checkpoint 持久化审批状态
- 默认 checkpoint 文件位于 `data/checkpoints/transport_order.pkl`
- 应用重启后，只要客户端仍持有 `pending_order_context.thread_id`，即可继续恢复审批流

## 日志

日志目录位于 `logs/`。

- `app.log`
- `mcp.log`
- `web.log`

当前日志统一携带 `request_id`，用于串联 supervisor、本地 subagent 和 MCP 请求链路。
