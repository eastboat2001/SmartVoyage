# SmartVoyage 项目指南

## 1. 项目简介

SmartVoyage 是一个基于 A2A + MCP + LangGraph 的旅行助手示例项目，当前包含：

- `3` 个 MCP 服务
  - 票务查询：`mcp_server/mcp_ticket_server.py`
  - 天气查询：`mcp_server/mcp_weather_server.py`
  - 票务预定：`mcp_server/mcp_order_server.py`
- `3` 个 A2A 服务
  - 票务查询：`a2a_server/ticket_server.py`
  - 天气查询：`a2a_server/weather_server.py`
  - 票务预定：`a2a_server/order_server.py`
- `2` 个入口
  - Streamlit 前端：`app.py`
  - 命令行入口：`main.py`

当前项目已经改造成：

- LangChain `v1.x`
- LangGraph `v1.x`
- 支持结构化输出，避免模型文本格式漂移打断逻辑
- 支持 `provider + model factory`
- 新增统一编排层，支持跨 Agent 协作链路
- 新增失败恢复与降级：Agent 超时兜底、结构化输出重试、模型 fallback provider
- 已完成 P0/P1 的最小落地版本
  - 已移除演唱会票主流程
  - 已新增默认演示用户 `demo_user`
  - 已支持订票落库、查询我的订单、防重复下单
- 已完成 P2 的最小落地版本
  - 已支持退票
  - 已支持改签
  - 已支持订单状态流转与库存回补/扣减
  - 退票/改签已改为 `LLM 结构化抽取 + 后端强校验 + 会话级轻量补参`
- 已完成 P3 的第一版最小落地
  - 已新增 `user_preferences`
  - 已支持读取用户偏好画像参与 `travel_plan` 出行推荐
  - 已支持使用 `home_city` 作为追问候选，但不会自动补全查询条件或自动下单
- 当前支持两种模型提供方式：
  - `openai_compatible`
  - `ollama`


## 1.1 项目目录结构

下面是项目根目录下主要目录和文件的说明。

### 根目录文件

- `app.py`
  - Streamlit 图形界面入口。
  - 负责用户对话、意图识别、调用各个 A2A Agent，并展示结果。
- `main.py`
  - 命令行入口。
  - 功能和 `app.py` 类似，但通过终端交互运行。
- `config.py`
  - 项目配置入口。
  - 负责读取 `.env`、提供默认值，并在关键配置缺失时打印提示。
- `create_logger.py`
  - 日志初始化。
  - 为控制台和日志文件统一创建 logger。
- `main_prompts.py`
  - 提示词集中定义。
  - 包含意图识别、天气总结、票务总结、景点推荐等 Prompt。
- `run_all.py`
  - 一键启动脚本。
  - 用于统一拉起 6 个后端服务，并可选附带启动 Streamlit 前端。
- `requirements.txt`
  - Python 依赖列表。
- `.env`
  - 当前实际运行配置。
  - 建议仅本地保存，不要提交真实密钥。
- `.env.example`
  - `.env` 模板文件。
- `README.md`
  - 项目使用指南。

### `a2a_server/`

这一层是 A2A Agent 服务层，对外提供智能体能力。

- `a2a_server/ticket_server.py`
  - 票务查询 Agent。
  - 接收自然语言查询，调用模型生成 SQL，再访问票务 MCP 服务返回结果。
- `a2a_server/weather_server.py`
  - 天气查询 Agent。
  - 接收自然语言查询，调用模型生成 SQL，再访问天气 MCP 服务返回结果。
- `a2a_server/order_server.py`
  - 票务预定 Agent。
  - 基于 LangGraph 编排“查单 / 下单 / 退票 / 改签”流程。

### `mcp_server/`

这一层是 MCP 服务层，对外暴露工具能力。

- `mcp_server/mcp_ticket_server.py`
  - 票务查询 MCP 服务。
  - 负责执行火车票、机票 SQL 查询。
- `mcp_server/mcp_weather_server.py`
  - 天气查询 MCP 服务。
  - 负责执行天气表 SQL 查询。
- `mcp_server/mcp_order_server.py`
  - 票务预定 MCP 服务。
  - 提供火车票、机票预定与用户订单查询工具。

### `sql/`

数据库初始化和测试数据目录。

- `sql/create_table.sql`
  - 创建数据库 `travel_rag` 和相关表结构。
- `sql/insert_data.sql`
  - 初始化用户、天气、交通票务等演示数据。
  - 当前内置的数据以 `2026-03-21` 到 `2026-03-25` 的天气和交通票务为主，适合直接演示查询、订票与订单查询链路。

### 当前能力边界

- 当前订票和查单默认基于单个演示用户 `demo_user`
- 当前订单类型只支持 `train` / `flight`
- 当前已支持：
  - 交通票务查询
  - 交通票务预定
  - 订单落库
  - 查询我的订单
  - 防止同一用户对同一车次/航班、同一席位重复下单
  - 退票
  - 改签
  - 退票/改签缺字段时的多轮补参
  - 基于用户偏好的 `travel_plan` 个性化推荐
  - 出发地缺失时，基于 `home_city` 的确认性追问
- 当前尚未支持：
  - 登录/切换用户
  - 酒店订单
  - 模糊改签（如“改签到下午”）
  - 完整持久化的用户画像采集与维护流程

### `test/`

手动验证脚本目录，方便单独测试某条链路。

- `test/test_ticket_mcp_server.py`
  - 单独测试票务 MCP 服务。
- `test/test_weather_mcp_server.py`
  - 单独测试天气 MCP 服务。
- `test/test_order_mcp_server.py`
  - 单独测试订票 MCP + LangChain Agent 链路。
- `test/test_ticket_agent_server.py`
  - 单独测试票务查询 Agent。
- `test/test_weather_agent_server.py`
  - 单独测试天气查询 Agent。
- `test/test_order_agent_server.py`
  - 单独测试票务预定 Agent。
- `test/weather_api.py`
  - 与天气数据相关的辅助测试脚本。

### `utils/`

通用工具与基础能力封装。

- `utils/model_factory.py`
  - 模型工厂。
  - 根据 `provider` 创建 `ChatOpenAI` 或 `ChatOllama`，并提供结构化输出包装和订票 Agent 构造。
- `utils/structured_outputs.py`
  - 结构化输出 Schema。
  - 定义意图识别、天气 SQL、票务 SQL、出行规划等 Pydantic 模型。
- `utils/format.py`
  - 数据格式化工具。
  - 主要用于日期、时间、Decimal 等对象的 JSON 序列化。
- `utils/spider_weather.py`
  - 天气数据抓取与入库脚本。
  - 从和风天气接口获取天气数据并写入 MySQL。

### `logs/`

- `logs/app.log`
  - 项目运行日志文件。

### 其他目录

- `__pycache__/`
  - Python 编译缓存，可忽略。
- `.idea/`
  - IDE 工程配置文件，可忽略。
- `.jbeval/`
  - IDE/工具生成目录，一般不参与业务逻辑。

### 建议的阅读顺序

如果你后面要快速理解这个项目，建议按这个顺序看：

1. `README.md`
2. `config.py`
3. `run_all.py`
4. `app.py` 或 `main.py`
5. `a2a_server/`
6. `mcp_server/`
7. `utils/model_factory.py`
8. `utils/structured_outputs.py`
9. `sql/`


## 2. 环境准备

### 2.1 Python 与依赖管理

建议使用 `uv` + Python `3.12`。

创建虚拟环境并安装依赖：

```powershell
uv venv --python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果你已经创建过 `.venv`，只需要重新安装一次依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```


### 2.2 MySQL

项目依赖本地 MySQL，默认配置如下：

- host: `localhost`
- user: `root`
- password: `123456`
- database: `travel_rag`

你可以在 `.env` 中修改这些值。


## 3. 配置文件

项目使用 `.env` 读取配置，`config.py` 会：

- 优先读取 `.env`
- 如果 `.env` 不存在，则回退到默认值
- 在关键配置缺失时给出提示

先复制模板：

```powershell
Copy-Item .env.example .env
```


## 4. 模型 Provider 配置

### 4.1 openai_compatible

适用于：

- OpenAI 官方 API
- 阿里 DashScope OpenAI 兼容接口
- 各类 OpenAI 兼容中转 API

`.env` 示例：

```env
SMARTVOYAGE_PROVIDER=openai_compatible
SMARTVOYAGE_MODEL_NAME=qwen-plus
SMARTVOYAGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SMARTVOYAGE_API_KEY=你的真实API_KEY

SMARTVOYAGE_OLLAMA_BASE_URL=http://127.0.0.1:11434

SMARTVOYAGE_DB_HOST=localhost
SMARTVOYAGE_DB_USER=root
SMARTVOYAGE_DB_PASSWORD=123456
SMARTVOYAGE_DB_NAME=travel_rag
```

你需要重点改这几个字段：

- `SMARTVOYAGE_PROVIDER`
- `SMARTVOYAGE_MODEL_NAME`
- `SMARTVOYAGE_BASE_URL`
- `SMARTVOYAGE_API_KEY`
- 如果你希望模型故障时自动切到备用 provider，还可以额外配置：
  - `SMARTVOYAGE_FALLBACK_PROVIDER`
  - `SMARTVOYAGE_FALLBACK_MODEL_NAME`
  - `SMARTVOYAGE_FALLBACK_BASE_URL`
  - `SMARTVOYAGE_FALLBACK_API_KEY`


### 4.2 ollama

适用于本地运行模型。

你需要先安装并启动 Ollama，然后拉取一个支持聊天的模型，例如：

```powershell
ollama pull qwen2.5:7b
```

`.env` 示例：

```env
SMARTVOYAGE_PROVIDER=ollama
SMARTVOYAGE_MODEL_NAME=qwen2.5:7b
SMARTVOYAGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SMARTVOYAGE_API_KEY=
SMARTVOYAGE_OLLAMA_BASE_URL=http://127.0.0.1:11434

SMARTVOYAGE_DB_HOST=localhost
SMARTVOYAGE_DB_USER=root
SMARTVOYAGE_DB_PASSWORD=123456
SMARTVOYAGE_DB_NAME=travel_rag
```

你需要重点改这几个字段：

- `SMARTVOYAGE_PROVIDER=ollama`
- `SMARTVOYAGE_MODEL_NAME`
- `SMARTVOYAGE_OLLAMA_BASE_URL`

说明：

- `provider=ollama` 时，不依赖 `SMARTVOYAGE_API_KEY`
- 票务预定这条链路依赖工具调用能力，建议优先选择支持工具调用/结构化输出更稳定的 Ollama 模型
- 如果你主模型不是 Ollama，也可以把 Ollama 配成 `SMARTVOYAGE_FALLBACK_PROVIDER=ollama` 作为备用


## 5. 初始化数据库

第一次运行前，需要先创建库表并导入测试数据。

PowerShell 下执行：

```powershell
Get-Content sql\create_table.sql | mysql -u root -p123456
Get-Content sql\insert_data.sql | mysql -u root -p123456
```

如果你的数据库密码不是 `123456`，请先修改 `.env`。

导入完成后，推荐优先使用以下绝对日期做演示，避免“今天 / 明天 / 后天”与本地演示数据错位：

- 天气 / 票务：`2026-03-21` 到 `2026-03-25`
- 默认演示用户：`demo_user`


## 6. 启动项目

### 6.1 一键启动全部后端服务

项目根目录提供了一键启动脚本：

```powershell
.\.venv\Scripts\python.exe run_all.py
```

这会启动：

- `mcp_ticket_server`
- `mcp_weather_server`
- `mcp_order_server`
- `a2a_ticket_server`
- `a2a_weather_server`
- `a2a_order_server`


### 6.2 一键启动后端 + 前端

```powershell
.\.venv\Scripts\python.exe run_all.py --with-ui
```

这会额外启动：

- `streamlit run app.py`


### 6.3 一键启动后端 + 命令行入口

```powershell
.\.venv\Scripts\python.exe run_all.py --with-cli
```

这会额外启动：

- `python main.py`

说明：

- `run_all.py` 每次启动前会自动清空 `logs` 目录下已有的 `.log` 文件。
- MCP 服务的终端输出会聚合写入 `logs/mcp.log`。
- A2A 服务的终端输出会聚合写入 `logs/a2a.log`。
- 项目内部业务日志统一写入 `logs/app.log`。
- `streamlit` 和 `main.py` 会直接占用当前终端显示输出，其中 `main.py` 需要在终端中直接输入问题，因此不会单独写 `main-cli.log`。


### 6.4 单独启动前端

如果后端已经启动，也可以单独运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```


### 6.5 命令行入口

如果后端已经启动，也可以单独运行：

```powershell
.\.venv\Scripts\python.exe main.py
```


## 7. 测试方式

### 7.1 测试 MCP 订票链路

```powershell
.\.venv\Scripts\python.exe test\test_order_mcp_server.py
```


### 7.2 测试票务查询 Agent

```powershell
.\.venv\Scripts\python.exe test\test_ticket_agent_server.py
```


### 7.3 测试天气查询 Agent

```powershell
.\.venv\Scripts\python.exe test\test_weather_agent_server.py
```


### 7.4 测试票务 MCP

```powershell
.\.venv\Scripts\python.exe test\test_ticket_mcp_server.py
```


### 7.5 测试天气 MCP

```powershell
.\.venv\Scripts\python.exe test\test_weather_mcp_server.py
```

### 7.6 推荐演示问题

建议优先使用数据库里明确存在的绝对日期进行演示：

```text
查询2026-03-21杭州的天气
查询2026-03-21北京到杭州的高铁票
查询2026-03-21北京到杭州的机票
根据2026-03-21杭州的天气，帮我判断从北京去杭州更适合坐高铁还是飞机，并查询对应票务
根据2026-03-21上海的天气，帮我判断从北京去上海坐高铁还是飞机更合适，如果有合适票就直接帮我订一张
帮我预订2026-03-21北京到上海的高铁票，二等座1张
查询我的订单
2026-03-21北京到上海的高铁票，二等座还有多少张
帮我退掉2026-03-21北京到上海的高铁票
把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座
```

说明：

- `travel_plan` 协作链路建议优先测试北京到杭州、北京到上海。
- 如果你临时修改了 `sql/insert_data.sql`，记得重新导入数据库后再测试。


## 8. 当前关键配置项说明

### 模型相关

- `SMARTVOYAGE_PROVIDER`
  - 可选：`openai_compatible` / `ollama`
- `SMARTVOYAGE_MODEL_NAME`
  - 当前使用的聊天模型名称
- `SMARTVOYAGE_BASE_URL`
  - 仅 `openai_compatible` 使用
- `SMARTVOYAGE_API_KEY`
  - 仅 `openai_compatible` 使用
- `SMARTVOYAGE_OLLAMA_BASE_URL`
  - 仅 `ollama` 使用
- `SMARTVOYAGE_FALLBACK_PROVIDER`
  - 备用模型 provider，可选：`openai_compatible` / `ollama`
- `SMARTVOYAGE_FALLBACK_MODEL_NAME`
  - 主模型失败后的备用模型名称
- `SMARTVOYAGE_AGENT_TIMEOUT_SECONDS`
  - Agent 调用超时时间，默认 `18`
- `SMARTVOYAGE_STRUCTURED_RETRY_COUNT`
  - 结构化输出重试次数，默认 `2`
- `SMARTVOYAGE_TEXT_RETRY_COUNT`
  - 普通文本生成重试次数，默认 `2`

### 数据库相关

- `SMARTVOYAGE_DB_HOST`
- `SMARTVOYAGE_DB_USER`
- `SMARTVOYAGE_DB_PASSWORD`
- `SMARTVOYAGE_DB_NAME`
- `SMARTVOYAGE_DEFAULT_USERNAME`
  - 当前会话默认用户，默认值为 `demo_user`


## 9. 代码结构说明

### 模型工厂

统一模型接入在：

- `utils/model_factory.py`

当前通过 `build_chat_model(config)` 根据 `.env` 中的 provider 返回对应模型：

- `openai_compatible` -> `ChatOpenAI`
- `ollama` -> `ChatOllama`

结构化输出也在同一个文件统一封装：

- `build_structured_llm(...)`


### 结构化输出 Schema

定义在：

- `utils/structured_outputs.py`

当前覆盖：

- 意图识别
- 天气 SQL 生成
- 票务 SQL 生成
- 跨 Agent 出行规划
- 订单退票/改签参数抽取

这样做的好处是：

- 不再依赖模型输出固定 JSON 文本
- 不再依赖手工字符串拆解
- 模型格式漂移时更容易发现并定位问题

### 统一编排层

定义在：

- `utils/orchestrator.py`
- `utils/resilient_llm.py`
- `a2a_server/order_server.py` 内部订单图

当前新增能力：

- 对 `travel_plan` 意图做真正的跨 Agent 协作
  - 先查天气
  - 再基于天气和用户偏好画像决策高铁或飞机
  - 然后继续查票，必要时继续订票
- 当用户未明确出发地但画像里存在 `home_city` 时
  - 会先做确认性追问
  - 不会直接把 `home_city` 自动补全进查询或下单参数
- 对 Agent 调用增加超时控制
- 对结构化输出增加重试
- 对模型调用支持 fallback provider
- 当天气或票务服务不可用时，返回明确的降级说明，而不是直接报错中断
- 订单域确定性流程已改为 LangGraph v1.x
  - `query_orders`
  - `cancel_order`
  - `change_order`
  - `lookup_tickets -> create_order`
- 退票/改签参数不再依赖纯规则解析
  - 先由 LLM 按 schema 做结构化抽取
  - 缺字段时返回 `input_required`
  - 前端 / CLI 在当前会话内保存 `pending_order_context`，等待用户补充后继续执行
  - 当前补参机制是轻量会话态，不是 LangGraph 持久化 HITL


## 10. 常见问题

### 10.1 `Unknown database 'travel_rag'`

说明数据库还没初始化。执行第 5 节的 SQL 导入步骤。


### 10.2 `Incorrect API key provided`

说明 `openai_compatible` 模式下的 `SMARTVOYAGE_API_KEY` 无效。  
检查 `.env` 并重启所有服务。


### 10.3 运行测试时提示无法连接 `8001/8002/8003/5005/5006/5007`

说明后端服务没有启动。先执行：

```powershell
.\.venv\Scripts\python.exe run_all.py
```


### 10.4 切换 provider 后不生效

模型实例在服务启动时创建。  
修改 `.env` 后需要重启后端服务。


### 10.5 使用 Ollama 时报模型不存在

先确认本地模型已拉取：

```powershell
ollama list
```

如果没有，先：

```powershell
ollama pull 你的模型名
```

### 10.6 启动时报端口已被占用

如果日志里出现 `8001/8002/8003/5005/5006/5007` 已被占用，通常说明旧服务还没退出。

- 先关闭旧的 Python 进程
- 再重新执行 `run_all.py`

### 10.7 为什么查询余票和查询订单的回复风格不一样

这是当前 P1 阶段的设计取舍：

- “查询我的订单”走订单系统，返回事实型结果
- 普通票务查询默认仍会经过总结层
- 对“余票还有多少张”这类事实型问题，当前代码已经优先返回事实结果，并在命中同一车次/航班时附带当前用户的已订数量

当前已经补上了 P2 的最小订单生命周期能力，但“更自然的状态一致性”和“模糊改签理解”仍有继续优化空间。

### 10.8 改签为什么有时会要求我补充更明确的新日期或车次

当前改签和退票已经改为“LLM 结构化抽取 + 后端校验 + 会话级多轮补参”。

优先支持这类明确请求：

- `把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座`
- `把我2026-03-21北京到上海的机票改签到CA1852`

对于这类模糊请求，当前会先追问补充信息，而不是自由脑补：

- `改到下午`
- `换一班更合适的`

## 11. 当前已知限制

- 当前是单用户演示模式，默认用户由 `SMARTVOYAGE_DEFAULT_USERNAME` 控制，不是真正的登录系统。
- 当前“防重复下单”基于精确匹配：同一用户、同一出发时间、同一车次/航班、同一席位类型会被拦截；更宽泛的“相似订单理解”还没做。
- 当前重复下单时仍以直接拦截文案为主，还没有进入完整的“是否继续下单 / 改签 / 退票”多轮对话编排。
- 当前票务查询和订单查询已经能共享部分状态，但仍不是完整的统一会话状态管理。
- 当前退票/改签的补参仅在当前会话内生效，不做跨进程、跨重启持久化。
- 当前改签优先支持显式条件；对模糊时间表达（如“下午”“晚上”）会优先追问，不做自由脑补。

## 12. 推荐使用顺序

第一次运行建议按这个顺序：

1. 创建并激活虚拟环境
2. 安装依赖
3. 配置 `.env`
4. 初始化 MySQL 数据库
5. 根据你的演示方式选择：
6. `run_all.py`
7. 或 `run_all.py --with-ui`
8. 或 `run_all.py --with-cli`
9. 在前端、命令行入口或测试脚本中验证链路


## 13. 还需要改进的地方


目前已经有日志和基本降级文案，后续可以继续补：

- 每次跨 Agent 协作的阶段耗时
- 哪一步触发了 fallback provider
- 结构化输出重试次数
- 每类失败路径的命中统计
