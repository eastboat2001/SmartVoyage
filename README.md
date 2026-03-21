# SmartVoyage 项目指南

## 1. 项目概览

SmartVoyage 是一个基于 `A2A + MCP + LangChain v1.x + LangGraph` 的智能旅游助手示例项目。

当前包含：

- 4 个 MCP 服务
  - 票务查询：`mcp_server/mcp_ticket_server.py`
  - 天气查询：`mcp_server/mcp_weather_server.py`
  - 订单工具：`mcp_server/mcp_order_server.py`
  - 酒店工具：`mcp_server/mcp_hotel_server.py`
- 4 个 A2A 服务
  - 票务查询：`a2a_server/ticket_server.py`
  - 天气查询：`a2a_server/weather_server.py`
  - 订单 Agent：`a2a_server/order_server.py`
  - 酒店 Agent：`a2a_server/hotel_server.py`
- 2 个入口
  - Web：`app.py`
  - CLI：`main.py`

当前已落地能力：

- 天气查询、交通票务查询、交通订单创建 / 查询 / 退票 / 改签
- 酒店查询、酒店预订、酒店取消 / 改期、酒店订单查询
- 统一订单视图：`train / flight / hotel`
- `travel_plan` 第一版联动规划：天气 + 交通 + 酒店
- 用户画像接入：`home_city`、交通偏好、预算偏好等
- 统一 state 设计：订单域 / 酒店域 / `travel_plan` 已转为 `LLM 结构化抽取 + 后端强校验 + pending_context + MCP/tool` 模式
- LangSmith 第一版回归基线

当前默认演示用户：`demo_user`

## 2. 核心架构

### 2.1 分层

- 编排层：`utils/orchestrator.py`
- 结构化 schema：`utils/structured_outputs.py`
- 提示词：`main_prompts.py`
- 订单域：`a2a_server/order_server.py`
- 酒店域：`a2a_server/hotel_server.py`
- 模型工厂：`utils/model_factory.py`

### 2.2 当前 state 设计

当前遵循“小全局状态 + 域内状态”原则。

- 编排层只保留最小跨域上下文
- 订单域、酒店域、`travel_plan` 各自维护自己的工作流状态
- 统一核心字段：`domain / action / slots / missing_slots / pending_context / execution_payload`

### 2.3 `travel_plan` 当前形态

`travel_plan` 已从简单顺序调用升级为独立 workflow/subgraph：

- `prepare -> weather -> plan -> ticket -> hotel/order -> finalize`

当前支持：

- 多轮补参
- 结合已预订交通 / 酒店订单判断“是否齐备、还缺哪一段”
- 识别住宿部分覆盖
- 使用 `travel_date + travel_date_text + order_intent` 做结构化抽取与补参恢复
- 条件式联动下单：例如“有合适票就直接订”“如果高铁合适就直接订”
- 在缺出发地时结合 `home_city` 做确认性追问，但不会自动补全下单条件

`order_intent` 当前含义：

- `none`：只做方案或查票，不进入下单
- `any`：有合适票就直接下单
- `train_if_suitable`：只有推荐高铁时才继续下单
- `flight_if_suitable`：只有推荐飞机时才继续下单

## 3. 环境准备

### 3.1 Python 与依赖

建议 Python `3.12`。

```powershell
uv venv --python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3.2 MySQL

默认配置：

- host: `localhost`
- user: `root`
- password: `123456`
- database: `travel_rag`

可在 `.env` 中修改。

### 3.3 配置文件

先复制模板：

```powershell
Copy-Item .env.example .env
```

关键环境变量：

- 模型
  - `SMARTVOYAGE_PROVIDER`
  - `SMARTVOYAGE_MODEL_NAME`
  - `SMARTVOYAGE_BASE_URL`
  - `SMARTVOYAGE_API_KEY`
  - `SMARTVOYAGE_OLLAMA_BASE_URL`
  - `SMARTVOYAGE_FALLBACK_PROVIDER`
  - `SMARTVOYAGE_FALLBACK_MODEL_NAME`
- 数据库
  - `SMARTVOYAGE_DB_HOST`
  - `SMARTVOYAGE_DB_USER`
  - `SMARTVOYAGE_DB_PASSWORD`
  - `SMARTVOYAGE_DB_NAME`
  - `SMARTVOYAGE_DEFAULT_USERNAME`
- 运行控制
  - `SMARTVOYAGE_AGENT_TIMEOUT_SECONDS`
  - `SMARTVOYAGE_STRUCTURED_RETRY_COUNT`
  - `SMARTVOYAGE_TEXT_RETRY_COUNT`

### 3.4 Provider 说明

支持两类 provider：

- `openai_compatible`
- `ollama`

`provider=ollama` 时需先本地拉模型，例如：

```powershell
ollama pull qwen2.5:7b
```

## 4. 初始化数据库

第一次运行前执行：

```powershell
Get-Content sql\create_table.sql | mysql -u root -p123456
Get-Content sql\insert_data.sql | mysql -u root -p123456
```

推荐优先使用这些绝对日期做演示：

- 天气 / 票务：`2026-03-21` 到 `2026-03-25`
- 酒店库存：主要覆盖 `2026-03-21` 到 `2026-03-23`

## 5. 启动方式

### 5.1 一键启动后端

```powershell
.\.venv\Scripts\python.exe run_all.py
```

### 5.2 后端 + Web

```powershell
.\.venv\Scripts\python.exe run_all.py --with-ui
```

### 5.3 后端 + CLI

```powershell
.\.venv\Scripts\python.exe run_all.py --with-cli
```

### 5.4 开发模式自动重载

```powershell
.\.venv\Scripts\python.exe run_all.py --dev-reload
```

如果要带 Web：

```powershell
.\.venv\Scripts\python.exe run_all.py --with-ui --dev-reload
```

说明：

- `--dev-reload` 当前不支持和 `--with-cli` 同时使用
- 推荐开发方式：
  - 终端 A：`run_all.py --dev-reload`
  - 终端 B：`python main.py`

### 5.5 单独启动入口

```powershell
.\.venv\Scripts\python.exe main.py
```

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 6. 测试

### 6.1 推荐测试顺序

1. 启动后端服务

```powershell
.\.venv\Scripts\python.exe run_all.py --dev-reload
```

2. 手工回归时另开终端启动 CLI

```powershell
.\.venv\Scripts\python.exe main.py
```

3. 参考：

- `TEST_CASES.md`
- `SIDE_EFFECT_TEST_PLAN.md`

### 6.2 单链路脚本

```powershell
.\.venv\Scripts\python.exe test\test_order_mcp_server.py
.\.venv\Scripts\python.exe test\test_ticket_agent_server.py
.\.venv\Scripts\python.exe test\test_weather_agent_server.py
.\.venv\Scripts\python.exe test\test_ticket_mcp_server.py
.\.venv\Scripts\python.exe test\test_weather_mcp_server.py
```

### 6.3 推荐演示问题

```text
查询2026-03-21上海的天气
查询2026-03-21北京到上海的高铁票
查询2026-03-21北京到上海的机票
帮我预订2026-03-21北京到上海的高铁票，二等座1张
查询我的订单
查询我的飞机票和酒店和火车票
帮我退掉2026-03-21北京到上海的高铁票
把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座
查询2026-03-21上海的酒店
帮我订2026-03-21上海外滩云际酒店的高级大床房，住2晚1间
取消我订的2026-03-21上海外滩云际酒店
把我2026-03-21上海外滩云际酒店改到2026-03-22
结合2026-03-21上海开始两天的天气、交通和酒店，帮我做一个出行方案
```

## 7. LangSmith 评测

当前已提供一套不依赖 `pytest` 的 LangSmith 评测骨架：

- 核心无副作用回归集：`langsmith_eval/cases.json`
- 第二批副作用数据集：`langsmith_eval/side_effect_cases.json`
- 运行脚本：`langsmith_eval/run_langsmith_eval.py`
- 副作用测试方案：`SIDE_EFFECT_TEST_PLAN.md`

运行前必须先启动项目后端服务。

配置环境变量：

- `LANGSMITH_API_KEY`
- 可选：`LANGSMITH_ENDPOINT`
- 可选：`LANGSMITH_PROJECT`

常用命令：

基线集同步并运行：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-dataset --replace-dataset --run
```

仅同步基线集：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-dataset --replace-dataset
```

仅同步副作用集：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --sync-side-effect-dataset --replace-dataset
```

运行基线集：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --run
```

运行副作用集：

```powershell
.\.venv\Scripts\python.exe langsmith_eval\run_langsmith_eval.py --run --dataset-name "SmartVoyage Side Effect Regression" --db-reset-command ".\.venv\Scripts\python.exe scripts\reset_database.py" --reset-before-case
```

说明：`--run` 默认运行 `SmartVoyage Regression`，如果要跑副作用集，必须显式指定 `--dataset-name "SmartVoyage Side Effect Regression"`。

当前内置 evaluator：

- `intent_match`
- `route_match`
- `response_keywords_match`
- `pending_domain_match`
- `db_state_match`

当前基线状态：

- 已建立核心无副作用回归基线
- 当前基线集重点覆盖：天气、票务、订单混查、酒店查询、酒店取消补参、`travel_plan` 缺参/恢复/覆盖判断

副作用测试策略：

- 不与当前无副作用基线集混跑
- 跑整轮副作用回归前，建议先执行一次完整重建：`python scripts\reset_database.py`
- 第二批副作用 case 见 `SIDE_EFFECT_TEST_PLAN.md`
- `langsmith_eval/side_effect_cases.json` 已包含 `setup_profile + db_assertions`，会对订单状态和库存变化做自动断言
- `run_langsmith_eval.py` 已支持单独同步 side-effect dataset
- 重新同步已有 dataset 时，建议加 `--replace-dataset`，避免 LangSmith 中出现重复 case
- side-effect experiment 逐条重置时，可直接使用：`--db-reset-command "python scripts\reset_database.py" --reset-before-case`
- 当 `--db-reset-command` 指向项目自带的 `scripts\reset_database.py` 时，评测脚本会自动补上 `--skip-stop-services`，并改走保留服务的 soft reset，避免数据库重置后把后端服务停掉
- side-effect case 若依赖已预订订单，runner 会先按 `setup_profile` 注入前置订单，再抓取数据库前后快照做比对
- 当前推荐命令：`python langsmith_eval\run_langsmith_eval.py --run --dataset-name "SmartVoyage Side Effect Regression" --db-reset-command "python scripts\reset_database.py" --reset-before-case`
- 已提供数据库重置脚本：`scripts\reset_database.py`

## 8. 当前边界与限制

- 当前是单用户演示模式，不是真实登录系统
- `pending_context` 仅在当前会话内生效，不跨重启持久化
- `travel_plan` 还没有完整的一键联动酒店下单和更细的每日行程规划
- 订单多类型组合过滤仍有少量后端确定性归一逻辑
- 酒店库存是演示用离散库存，不是完整 CRS / PMS 模型
- 模糊改签表达（如“改到下午”）当前优先追问，不做自由脑补

## 9. 常见问题

### 9.1 `Unknown database 'travel_rag'`

数据库未初始化，先执行第 4 节 SQL 导入。

### 9.2 `Incorrect API key provided`

检查 `.env` 中的 `SMARTVOYAGE_API_KEY`。

### 9.3 连接不到 `8001/8002/8003/8004/5005/5006/5007/5008`

说明后端服务未启动，先运行：

```powershell
.\.venv\Scripts\python.exe run_all.py
```

### 9.4 切换 provider 后不生效

模型实例在服务启动时创建，改 `.env` 后需要重启服务。

### 9.5 Ollama 模型不存在

```powershell
ollama list
ollama pull 你的模型名
```

### 9.6 LangSmith 里出现大量“服务暂时不可用，请稍后重试。”

通常是本地 A2A/MCP 服务中断或自动重载中的短暂不可用，先重启后端再重跑，不要直接判定为能力回退。
