# SmartVoyage

## 0. 当前状态

当前仓库以根目录 [SPEC.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/SPEC.md) 为唯一主规格文档。

当前主线已经收敛为交通出行购票 Agent，已完成这些能力：

- FastAPI Web 调试页面
- 时间查询
- 天气查询
- 火车票 / 机票查询
- 相对日期查询
  - 支持今天 / 明天 / 后天
  - 支持在 LangSmith 中通过固定时钟做稳定回归
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
- Streamlit 前端
- 面向最终用户的完整产品化前端

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
- `OrderTools`
  - 订单创建 / 查询 / 取消 / 改签
  - 库存扣减 / 回补

## 2. 技术栈

- Python `3.12+`
- `LangChain v1.x`
- `LangGraph v1.x`
- A2A 服务承载：`FastAPI / ASGI`
- MCP 服务承载：`FastAPI / ASGI`
- 数据库：`MySQL`
- 交互入口：CLI + FastAPI 页面
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
- [web_app.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/web_app.py)
  - FastAPI Web 页面入口
- [run_all.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/run_all.py)
  - 一键启动后端服务，并可选拉起 CLI / Web 页面
- [config.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/config.py)
  - 环境配置与默认值
- [main_prompts.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main_prompts.py)
  - Prompt 兼容 facade，统一转发到 `prompt_skills/` registry
- `prompt_skills/`
  - Prompt skill 资源目录，按标准 skill 结构拆分为：
    - `intent-routing/`
    - `travel-read/`
    - `transport-decision/`
    - `order-operation/`
  - 每个 skill 目录包含：
    - `SKILL.md`
    - 可选 `references/`
    - 可选 `assets/`
- [create_logger.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/create_logger.py)
  - 统一日志配置
- [PROJECT_ISSUES.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/PROJECT_ISSUES.md)
  - 当前主线问题与重构经验总结
- [templates/index.html](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/templates/index.html)
  - Web 页面模板
- [static/app.css](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/static/app.css)
  - Web 页面样式
- [static/app.js](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/static/app.js)
  - Web 页面交互脚本

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

当前主线对自然语言理解采用“结构化抽取优先”的策略：

- orchestrator 负责统一意图识别
- `TravelDecisionAgent` 对读取类型优先消费显式 `kind` 上下文，缺失时再做结构化分类
- `TravelDecisionAgent` 的天气 / 票务查询先生成结构化 `Query Plan`，再由后端编译为受控 SQL
- `TransportOrderAgent` 对订单动作优先消费显式 `order_action` 上下文，缺失时再做结构化分类
- HITL 审批回复通过结构化决策映射为 `approved / rejected / unclear`
- 订单查询日期与自动下单意图也优先走结构化抽取，而不是依赖固定短语或日期正则

### `mcp_server/`

- [mcp_travel_read_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/mcp_server/mcp_travel_read_server.py)
  - `TravelReadTools`
- [mcp_order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/mcp_server/mcp_order_server.py)
  - `OrderTools`

### `utils/`

- [orchestrator.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/orchestrator.py)
  - 主编排入口
- [structured_outputs.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/structured_outputs.py)
  - 结构化 schema
- [order_action_context.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/order_action_context.py)
  - 订单域显式动作上下文
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
- [test_prompt_skill_registry.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/test/test_prompt_skill_registry.py)
  - Prompt skill registry / builder 的本地构建测试

### `langsmith_eval/`

- [cases.json](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/cases.json)
  - 当前自动化基础回归集（仅非 HITL 快测样例）
- [run_langsmith_eval.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/run_langsmith_eval.py)
  - LangSmith runner
- [HITL_MANUAL_TESTS.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/HITL_MANUAL_TESTS.md)
  - 人工审核链路的手工测试说明

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
- `OrderTools` on `8003`
- `TravelDecisionAgent` on `5005`
- `TransportOrderAgent` on `5007`

### 5.2 启动后端并进入 CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-cli
```

### 5.3 启动后端并打开 FastAPI 页面

```powershell
.\.venv\Scripts\python.exe run_all.py --with-web
```

页面地址：

- `http://127.0.0.1:8501`

### 5.4 同时启动后端、Web 页面和 CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-web --with-cli
```

### 5.5 单独启动 CLI

在后端服务已经启动后：

```powershell
.\.venv\Scripts\python.exe main.py
```

### 5.6 单独启动 FastAPI 页面

在后端服务已经启动后：

```powershell
.\.venv\Scripts\python.exe web_app.py
```
## 6. 健康检查与联调

### 6.1 健康检查

```powershell
curl http://127.0.0.1:5005/health
curl http://127.0.0.1:5007/health
curl http://127.0.0.1:8501/health
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
- 当前 checkpointer 已持久化到文件，默认路径为 `data/checkpoints/transport_order.pkl`
- 重启 `TransportOrderAgent` 服务后，只要客户端仍持有 `pending_order_context.thread_id`，即可继续审批恢复
- 如果 CLI 本身丢失了待审批上下文，仍需要重新发起该操作

## 7. LangSmith 评测

当前自动化评测统一使用一个 [cases.json](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/langsmith_eval/cases.json) 作为最终自动化基础集，当前共 19 条，覆盖：

- 时间查询
  - 当前时间
  - 星期 / 日期类表达
- 天气查询
  - 单天绝对日期
  - 多天范围查询
  - 相对日期查询
- 票务查询
  - 高铁按路线查询
  - 高铁按车次查询
  - 机票按路线查询
  - 机票按航班号查询
  - 相对日期票务查询
- `transport_decision`
  - 绝对日期只建议链路
  - 相对日期只建议链路
- 订单查询
  - 查询我的订单
  - 当前订单 / 查询当前订单
  - 相对日期订单查询
- 订单域多轮补参
  - 退票缺信息追问
  - 改签缺信息追问

涉及人工审核的订单副作用执行链路，当前不放入自动化基础集，而是单独放在：

- [HITL_MANUAL_TESTS.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/HITL_MANUAL_TESTS.md)

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

- 相对日期 case 可以通过 `now_override` 固定当前时间，避免测试结果随真实日期漂移
- 当前自动化集优先覆盖非 HITL 稳定链路，便于高频快测
- 如果你后续要补更难的自动化测试，可以继续直接往同一个 `cases.json` 里追加
- 人工审核与副作用链路建议继续按 [HITL_MANUAL_TESTS.md](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/HITL_MANUAL_TESTS.md) 手工回归

## 8. 日志

统一日志目录：[logs](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs)

- [app.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/app.log)
  - 统一应用日志
- [a2a.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/a2a.log)
  - A2A 服务日志
- [mcp.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/mcp.log)
  - MCP 服务日志
- [web.log](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/logs/web.log)
  - FastAPI Web 页面日志

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
- 面向最终用户的完整产品化前端（当前仅提供调试型 FastAPI 页面）




