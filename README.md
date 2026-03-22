# SmartVoyage

## 0. 当前状态

当前仓库以根目录 [SPEC.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/SPEC.md) 为唯一主规格文档。

当前主线已经收敛为交通出行购票 Agent，已完成这些能力：

- 时间查询
- 天气查询
- 火车票 / 机票查询
- 查询我的订单
- 购买交通票
- 退票
- 改签
- `transport_decision` 复杂任务
  - 先查天气和票务
  - 再判断高铁还是飞机更合适
  - 在条件满足时继续调用订单服务完成下单

当前不做：

- 酒店
- 景点
- 旧 `travel_plan`
- Web / Streamlit 前端

## 1. 项目简介

SmartVoyage 是一个基于 `LangChain + LangGraph + FastAPI + MCP` 的交通出行购票多智能体示例项目。

当前固定为 `2` 个 A2A 服务 + `2` 个 MCP 服务：

- `TravelDecisionAgent`
  - 天气查询
  - 时间查询
  - 票务查询
  - 交通方式建议
- `TransportOrderAgent`
  - 下单
  - 查单
  - 退票
  - 改签
- `TravelReadTools`
  - 当前时间
  - 天气数据
  - 火车票 / 机票数据
- `TransportOrderTools`
  - 订单创建 / 查询 / 取消 / 改签
  - 库存扣减 / 回补

## 2. 技术栈

- Python `3.12+`
- `LangChain v1.x`
- `LangGraph v1.x`
- A2A 服务承载：`FastAPI / ASGI`
- MCP 服务承载：`FastAPI / ASGI`
- 数据库：`MySQL`
- 交互入口：CLI
- 模型接入：
  - `openai_compatible`
  - `ollama`
- 评测：
  - 手工 CLI 冒烟
  - 测试脚本
  - `LangSmith + 自定义 runner`

## 3. 目录结构

### 根目录

- [SPEC.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/SPEC.md)
  - 当前主规格文档
- [main.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main.py)
  - CLI 入口
- [run_all.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/run_all.py)
  - 一键启动 4 个后端服务
- [config.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/config.py)
  - 环境配置与默认值
- [main_prompts.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main_prompts.py)
  - Prompt 定义
- [create_logger.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/create_logger.py)
  - 统一日志配置
- [PROJECT_ISSUES.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/PROJECT_ISSUES.md)
  - 当前主线问题与重构经验总结

### `a2a_server/`

- [travel_decision_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/travel_decision_server.py)
  - `TravelDecisionAgent`
- [order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/order_server.py)
  - `TransportOrderAgent`

两个 A2A 服务统一暴露：

- `GET /health`
- `GET /metadata`
- `POST /invoke`

当前 `POST /invoke` 的响应采用双通道：

- `text`
  - 面向用户的自然语言结果
- `data`
  - 供编排层继续消费的结构化 payload
- `meta`
  - 调试和追踪使用的元信息

### `mcp_server/`

- [mcp_travel_read_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/mcp_server/mcp_travel_read_server.py)
  - `TravelReadTools`
- [mcp_order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/mcp_server/mcp_order_server.py)
  - `TransportOrderTools`

### `utils/`

- [orchestrator.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/orchestrator.py)
  - 主编排入口
- [structured_outputs.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/structured_outputs.py)
  - 结构化 schema
- [service_protocol.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/service_protocol.py)
  - A2A 协议对象
- [fastapi_middleware.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/fastapi_middleware.py)
  - 请求 ID、异常、访问日志等中间件
- [request_context.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/request_context.py)
  - `request_id` 上下文

### `test/`

- [test_travel_read_mcp_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/test/test_travel_read_mcp_server.py)
- [test_travel_decision_agent_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/test/test_travel_decision_agent_server.py)
- [test_order_agent_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/test/test_order_agent_server.py)
- [test_order_mcp_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/test/test_order_mcp_server.py)

### `langsmith_eval/`

- [cases.json](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/cases.json)
  - 当前混合基础回归集（无副作用 + 副作用）
- [run_langsmith_eval.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/run_langsmith_eval.py)
  - LangSmith runner

### `sql/`

- [create_table.sql](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/sql/create_table.sql)
- [insert_data.sql](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/sql/insert_data.sql)

当前示例数据主要覆盖 `2026-03-21` 到 `2026-03-25`。

## 4. 环境准备

### 4.1 安装依赖

推荐：

```powershell
uv venv --python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果 `.venv` 已存在，只需要重新安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4.2 MySQL

默认配置：

- host: `localhost`
- user: `root`
- password: `123456`
- database: `travel_rag`

可以在 `.env` 中覆盖。

初始化数据库：

```powershell
Get-Content sql\create_table.sql | mysql -u root -p123456
Get-Content sql\insert_data.sql | mysql -u root -p123456
```

### 4.3 模型配置

先复制模板：

```powershell
Copy-Item .env.example .env
```

至少需要配置这些字段中的一组：

- `SMARTVOYAGE_PROVIDER=openai_compatible`
- `SMARTVOYAGE_BASE_URL=...`
- `SMARTVOYAGE_API_KEY=...`
- `SMARTVOYAGE_MODEL_NAME=...`

或者：

- `SMARTVOYAGE_PROVIDER=ollama`
- `SMARTVOYAGE_OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `SMARTVOYAGE_MODEL_NAME=...`

## 5. 启动方式

### 5.1 只启动后端服务

```powershell
.\.venv\Scripts\python.exe run_all.py
```

默认会拉起：

- `TravelReadTools` on `8001`
- `TransportOrderTools` on `8003`
- `TravelDecisionAgent` on `5005`
- `TransportOrderAgent` on `5007`

### 5.2 启动后端并进入 CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-cli
```

### 5.3 单独启动 CLI

在后端服务已经启动后：

```powershell
.\.venv\Scripts\python.exe main.py
```

## 6. 健康检查与联调

### 6.1 健康检查

```powershell
curl http://127.0.0.1:5005/health
curl http://127.0.0.1:5007/health
```

MCP 的 `/mcp` 端点是 SSE 协议，不适合直接裸 `curl` 当普通 JSON 接口使用。

### 6.2 单链路测试

```powershell
.\.venv\Scripts\python.exe test\test_travel_read_mcp_server.py
.\.venv\Scripts\python.exe test\test_travel_decision_agent_server.py
.\.venv\Scripts\python.exe test\test_order_agent_server.py
```

### 6.3 CLI 冒烟测试

建议至少走一轮：

```text
现在几点
查询2026-03-21杭州的天气
查询2026-03-21北京到上海的高铁票
根据2026-03-21上海的天气，帮我判断从北京去上海坐高铁还是飞机更合适，如果有合适票就直接帮我订一张
查询我的订单
帮我退掉2026-03-21北京到上海的高铁票
把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座
```

### 6.4 命令行 HITL

当前订单副作用操作已经接入 LangGraph 的 `interrupt + resume`。

在 CLI 中，当系统准备执行以下高风险动作时，会先进入审批态：

- 下单
- 退票
- 改签

CLI 会显示类似提示：

```text
下单审批：2026-03-21 07:00:00 北京到上海 G5 二等座 1张。
请回复 yes 确认执行，或回复 no 取消执行。
```

此时：

- 输入 `yes`
  - 恢复工作流并继续执行
- 输入 `no`
  - 取消本次副作用操作

当前实现说明：

- 已使用 LangGraph checkpoint + interrupt/resume
- 当前 checkpointer 为进程内存级
- 如果重启 `TransportOrderAgent` 服务，未完成的审批线程会丢失

## 7. LangSmith 评测

当前统一使用一个 [cases.json](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/cases.json) 作为基础测试集，同时包含：

- 无副作用基线
  - 时间查询
  - 天气查询
  - 火车票查询
  - 机票查询
  - `transport_decision` 只建议链路
- 副作用基线
  - 直接下单
  - 退票
  - 改签
  - `transport_decision` 自动下单

运行前要求：

- 后端服务已启动
- 已配置 `LANGSMITH_API_KEY`
- 可选配置：
  - `LANGSMITH_ENDPOINT`
  - `LANGSMITH_PROJECT`

同步数据集：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-dataset --replace-dataset
```

运行基础评测：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --run
```

当前内置 evaluator：

- `intent_match`
- `route_match`
- `response_keywords_match`
- `pending_context_match`
- `db_state_match`

说明：

- runner 会在每条副作用 case 执行前自动重建数据库，并按 `setup_profile` 注入前置订单
- 当前副作用断言使用数据库前后指标差值：
  - 订单行数变化
  - 票务库存字段变化
- 如果你后续要补更难的测试，可以继续直接往同一个 `cases.json` 里追加

## 8. 日志

统一日志目录：[logs](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs)

- [app.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/app.log)
  - 统一应用日志
- [a2a.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/a2a.log)
  - A2A 服务日志
- [mcp.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/mcp.log)
  - MCP 服务日志

当前日志统一携带 `request_id`，用于串联 orchestrator、A2A、MCP 之间的请求链路。

## 9. 当前边界

当前主线已经稳定支持：

- 简单任务 `ReAct`
- 复杂任务 `transport_decision`
- 订单域异步 LangGraph 工作流
- 缺字段时追问补参
- 尽量避免用正则承担核心语义抽取

当前仍未覆盖：

- 酒店
- 景点
- 多日 travel planning
- Web 前端
- 持久化 checkpoint / 跨重启恢复的 HITL
