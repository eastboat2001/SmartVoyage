# SmartVoyage

SmartVoyage 是一个面向交通出行购票场景的 AI Agent 项目，当前主线已经收敛为：

- `MCP` 负责工具与数据边界
- `Supervisor-style Multi-Agent` 负责任务编排
- `Skill Runtime` 负责按角色和能力加载 prompt skill
- `LangGraph` 负责订单链路中的状态流转、HITL 审批与恢复

当前版本只聚焦交通查询与订票主线。

## 当前能力

- 查询当前时间
- 查询天气
- 查询火车票与机票
- 支持相对日期表达，例如今天、明天、后天
- 查询我的订单
- 购买交通票
- 退票
- 改签
- 执行 `transport_decision`
  - 先查天气和票务
  - 再判断高铁还是飞机更合适
  - 条件满足时继续进入下单链路

## 当前架构

当前固定为 `1` 个 Supervisor、`2` 个本地 Subagent、`2` 个 MCP 服务：

- `SmartVoyageSupervisor`
  - 意图识别
  - 跨域路由
  - `transport_decision` 编排
- `TravelReadSubagent`
  - 时间、天气、票务只读查询
- `OrderSubagent`
  - 下单、查单、退票、改签
- `TravelReadTools`
  - 当前时间
  - 天气数据
  - 火车票与机票数据
- `OrderTools`
  - 订单创建、查询、取消、改签
  - 库存扣减与回补

Supervisor 直接持有并调用本地 subagent，MCP 只用于工具层。

## Skill Runtime

当前的 skill 是本地 runtime skill。

- skill 目录位于 `skills/`
- 每个 skill 由 `SKILL.md + assets/ + references/` 组成
- 运行时按照 `role + capability + flags` 确定性选择 skill
- `main_prompts.py` 现在只是兼容 facade，实际 prompt 构建由 skill runtime 完成

当前固定 4 个 skill：

- `intent-routing`
- `travel-read`
- `transport-decision`
- `order-operation`

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
- `utils/`
  - `agent_protocol.py`
  - `structured_outputs.py`
  - `persistent_checkpointer.py`
- `langsmith_eval/`
  - `cases.json`
  - `run_langsmith_eval.py`
- `test/`
  - MCP smoke tests
  - subagent smoke tests
  - skill runtime tests

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

示例数据主要覆盖 `2026-03-21` 到 `2026-03-25`。

### 3. 配置模型

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

### 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest test.test_prompt_skill_registry
.\.venv\Scripts\python.exe -m unittest test.test_travel_decision_agent_server
.\.venv\Scripts\python.exe -m unittest test.test_order_agent_server
.\.venv\Scripts\python.exe -m unittest test.test_supervisor
.\.venv\Scripts\python.exe -m unittest test.test_travel_read_mcp_server
.\.venv\Scripts\python.exe -m unittest test.test_order_mcp_server
```

### 真实端到端测试

下面这组测试会真实调用外部模型接口，因此不默认加入常规回归。运行前需要：

- MCP 服务可由测试自动拉起
- 模型配置可用
- 显式设置 `SMARTVOYAGE_RUN_E2E=1`

```powershell
$env:SMARTVOYAGE_RUN_E2E="1"
.\.venv\Scripts\python.exe -m unittest test.test_supervisor_e2e
```

### CLI 冒烟建议

```text
现在几点
查询2026-03-21杭州的天气
查询2026-03-21北京到上海的高铁票
根据2026-03-21上海的天气，帮我判断从北京去上海坐高铁还是飞机更合适，如果有合适票就直接帮我订一张
查询我的订单
帮我退掉2026-03-21北京到上海的高铁票
把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座
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

手工回归说明见：

- `HITL_MANUAL_TESTS.md`

## LangSmith 评测

自动化评测使用：

- `langsmith_eval/cases.json`
- `langsmith_eval/run_langsmith_eval.py`

当前基础集覆盖：

- 时间查询
- 天气查询
- 票务查询
- `transport_decision` 建议链路
- 订单查询
- 订单域缺参补问

当前 runner 使用混合评测：

- 确定性断言：`intent_match`、`route_match`、`pending_context_match`、`db_state_match`
- `LLM judge`：`response_semantic_match`，用于判断最终回复是否在语义上满足案例目标

运行方式：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-dataset --replace-dataset
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --run
```

## 日志

日志目录位于 `logs/`。

- `app.log`
- `mcp.log`
- `web.log`

当前日志统一携带 `request_id`，用于串联 supervisor、本地 subagent 和 MCP 请求链路。


