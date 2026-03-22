# SmartVoyage SPEC

## 1. 目标

SmartVoyage 要从当前 `P3` 基线重构为一个“规格驱动、行为可预测、便于调试”的交通出行购票多智能体项目。

项目只聚焦这些能力：

- 天气查询
- 时间查询
- 火车票 / 机票查询
- 火车票 / 机票购买
- 查询我的交通订单
- 取消交通订单
- 改签交通订单
- 基于天气、时间与票务信息判断“更适合高铁还是飞机”
- 在复杂任务中按决策结果可选进入购票执行

核心原则：

- 先写规格，再实现代码
- 先定义状态、接口、验收标准，再写 prompt
- prompt 只负责受控抽取和受控生成，不承担业务真相来源
- 后端规则、库存一致性、状态流转都必须由确定性代码负责

## 2. 范围

### 2.1 本期要做

- CLI 交互
- 交通票务查询
- 交通订单生命周期：创建 / 查询 / 取消 / 改签
- 天气 / 时间查询
- 交通方式建议
- LangSmith 回归与自定义 runner

### 2.2 本期不做

- 酒店查询、预订、取消、改期
- 景点推荐
- 旧 `travel_plan`
- 多日行程规划
- Web / Streamlit 前端
- 用户画像维护流程
- 复杂个性化推荐

## 3. 目标架构

### 3.1 总体原则

- 保留 A2A 与 MCP，但严格缩减服务数量
- A2A 只承载“需要对话状态和语义工作流”的能力
- MCP 只承载“确定性工具和数据库操作”
- Orchestrator 只做最小跨域编排，不堆业务规则

### 3.2 固定服务划分

最终固定为：

- `1` 个本地 orchestrator
- `2` 个 A2A 服务
- `2` 个 MCP 服务
- `1` 个 CLI 入口
- `1` 个 MySQL 数据库

#### A2A 服务

1. `TravelDecisionAgent`
   - 天气查询
   - 时间查询
   - 票务查询
   - “高铁还是飞机”建议
   - 复杂任务下的可选购票决策

2. `TransportOrderAgent`
   - `create_order`
   - `query_orders`
   - `cancel_order`
   - `change_order`

#### A2A 协议

所有 A2A 服务统一暴露：

- `GET /health`
- `GET /metadata`
- `POST /invoke`

`POST /invoke` 响应固定采用双通道：

- `text`
  - 面向用户展示的自然语言结果
- `data`
  - 供 orchestrator 或下游工作流继续消费的结构化 payload
- `meta`
  - 调试、追踪、工具来源、线程信息等元数据

#### MCP 服务

1. `TravelReadTools`
   - 当前时间
   - 天气数据
   - `train_tickets`
   - `flight_tickets`

2. `TransportOrderTools`
   - 创建订单
   - 查询订单
   - 取消订单
   - 改签订单
   - 库存扣减 / 回补

### 3.3 为什么这样拆

- 天气、时间、查票、交通建议都属于只读能力，适合收束到一个 `TravelDecisionAgent`
- 买票、查单、退票、改签都属于订单生命周期，适合收束到一个 `TransportOrderAgent`
- 这样可以避免当前项目“一个小能力一个服务”的过度服务化

## 4. 技术栈与约束

### 4.1 固定技术栈

- Python `3.12+`
- `LangChain v1.x`
- `LangGraph v1.x`
- A2A 服务承载：`FastAPI / ASGI`
- MCP 服务承载：`FastAPI / ASGI`
- 数据库：`MySQL`
- 前端：仅 CLI
- 模型接入：`OpenAI-compatible` 或 `Ollama`
- 测试 / 评测：`LangSmith + 自定义 runner`

### 4.2 非功能约束

- 默认 UTF-8
- 所有关键状态流转必须打日志
- 副作用操作必须记录：
  - `request_id`
  - `domain`
  - `action`
  - 抽取结果
  - 校验结果
  - 工具调用参数
  - 数据库变化摘要
- CLI 输出优先清晰、简洁

## 5. LangGraph 与 LangChain 的边界

### 5.1 必须用 LangGraph 的地方

只在存在显式状态迁移、分支和副作用语义时使用 `LangGraph`。

#### `TransportOrderAgent`

固定工作流：

- `extract`
- `review`
- `validate`
- `query_orders / lookup_ticket`
- `create / cancel / change`
- `finalize`

原因：

- 有稳定 action
- 有 `pending_context`
- 有状态流转
- 有失败分支
- 有数据库副作用
- 有高风险副作用前的人工审核节点

#### `TravelDecisionAgent` 的复杂任务

固定工作流：

- `extract`
- `read_weather`
- `read_time`
- `read_ticket`
- `decide`
- `optional_order_handoff`
- `finalize`

原因：

- 这是标准的 `plan-and-execute`
- 多步依赖明确
- 决策结果会影响是否继续执行购票

### 5.2 不必用 LangGraph 的地方

只在单轮、轻量、无复杂状态机的地方使用 `LangChain`：

- 结构化抽取
- 简单 ReAct 查询
- prompt + tool 调用
- 建议文案生成

### 5.3 固定决策原则

- 有状态机、需要 `pending_context`、需要显式分支：`LangGraph`
- 单轮工具调用或单轮 reasoning：`LangChain`
- 不允许为了展示复杂度把所有能力都塞进 `LangGraph`

## 6. 编排模式

### 6.1 简单任务：ReAct

适用：

- 查天气
- 查时间
- 查票
- 查我的订单

行为：

- orchestrator 识别意图
- 直接路由到对应 A2A
- A2A 内部用 `LangChain` 调 MCP 工具
- 不进入复杂工作流

### 6.2 复杂任务：plan-and-execute

适用：

- “结合天气和票务判断坐高铁还是飞机”
- “如果更合适就直接帮我买票”

固定流程：

- `intent`
- `slots extraction`
- `missing slot follow-up`
- `weather/time/ticket reads`
- `decision`
- `optional order execution`
- `final summary`

复杂任务统一命名为 `transport_decision`，不再沿用旧 `travel_plan`。

## 7. 中间件设计

当前项目是本地 CLI 调试项目，只保留最小必要中间件。

### 7.1 必须有

1. 请求 ID 中间件
   - 为每次 A2A / MCP 请求生成 `request_id`
   - 用于串联日志和问题排查

2. 统一异常处理中间件
   - 把未捕获异常转成稳定 JSON 响应

3. 访问日志中间件
   - 记录 `path / method / status / latency / request_id`

4. 超时中间件
   - 对 A2A / MCP 入口设置合理超时
   - 超时要返回明确错误

### 7.2 暂不需要

- CORS
- 认证 / 鉴权
- 限流
- 复杂 tracing 系统

当前阶段先用 `request_id + 结构化日志` 即可。

## 8. HITL 与 Checkpoint

### 8.1 当前要求

订单副作用操作必须支持人工审核插口：

- 下单
- 退票
- 改签

### 8.2 当前实现

- `TransportOrderAgent` 使用 LangGraph `interrupt + resume`
- 在副作用节点前统一进入 `review` 节点
- checkpoint 已持久化到本地文件
- CLI 通过 `yes / no` 恢复或取消执行

### 8.3 当前限制

- 当前持久化只覆盖工作流 checkpoint，本地 CLI 不会单独持久化待审批上下文
- 如果客户端丢失 `thread_id` 或待审批上下文，仍需要重新发起该操作

### 8.4 当前默认实现

- 默认 checkpoint 路径：`data/checkpoints/transport_order.pkl`
- 默认时区：`Asia/Shanghai`
- 支持通过 `SMARTVOYAGE_NOW_OVERRIDE` 固定当前时间，供评测与相对日期理解使用

## 9. 状态设计

### 8.1 全局状态

只保留：

- `conversation_history`
- `pending_context`
- `current_username`
- `last_domain`

### 8.2 TravelDecisionState

- `action`
- `slots`
- `missing_slots`
- `tool_requests`
- `decision_result`
- `final_response`

### 8.3 TransportOrderState

- `action`
- `slots`
- `missing_slots`
- `validation_result`
- `execution_payload`
- `final_response`

### 8.4 规则

- 不做 mega-state
- 所有 action 必须是枚举
- prompt 不得决定数据库真相

## 10. 核心 schema

### 9.1 `IntentRecognitionResult`

- `intents`
- `user_queries`
- `follow_up_message`

### 9.2 `TravelDecisionExtractionResult`

- `action`
- `departure_city`
- `arrival_city`
- `departure_date`
- `transport_preference`
- `should_order`
- `missing_slots`
- `follow_up_message`

### 9.3 `TransportDecisionPlanResult`

- `recommended_mode`
- `weather_brief`
- `time_brief`
- `ticket_query`
- `decision_reason`
- `should_execute_order`

### 9.4 `TransportOrderExtractionResult`

- `action`
- `order_type`
- `departure_date`
- `departure_city`
- `arrival_city`
- `transport_no`
- `ticket_type`
- `quantity`
- `new_departure_date`
- `new_transport_no`
- `new_ticket_type`
- `missing_fields`
- `follow_up_message`

## 11. 抽取与规则边界

- prompt 负责：
  - 意图识别
  - action 选择
  - slots 抽取
  - 交通建议生成
- 后端负责：
  - 最小进入条件
  - 缺字段判断
  - `pending_context` 合并
  - 订单唯一命中
  - 库存一致性
  - 状态流转

正则只允许用于：

- 包裹 / 解析 `pending_context`
- 包裹 / 解析显式上下文标记（如读取类型、订单动作）
- 确定性格式校验

不允许正则承担核心语义抽取。

此外，固定关键词列表不得作为自然语言主判定逻辑：

- 读取类型、订单动作、HITL 审批决定优先采用结构化抽取
- 日期归一化、自动下单意图判断优先采用结构化抽取
- 天气 / 票务读取优先采用 `Query Plan -> 后端编译 SQL`，而不是让 LLM 直接生成完整 SQL
- orchestrator 与 A2A 之间优先传显式上下文参数，而不是重复做字符串猜测

## 12. 测试要求

### 11.1 CLI 手工链路

必须覆盖：

- 查询天气
- 查询时间
- 查询高铁票
- 查询机票
- 购买高铁票
- 购买机票
- 查询我的订单
- 退票
- 改签
- “结合天气和票务推荐高铁还是飞机”
- “建议后直接购票”
- 订单域多轮补参

### 11.2 LangSmith 回归

统一使用单一基础数据集，包含两层：

1. 第一批基础基线
   - intent
   - route
   - `pending_context`
   - response keywords
   - `create_order / cancel_order / change_order`
   - `transport_decision` 自动下单
   - 数据库断言

2. 第二批基础增强基线
   - 相对日期天气 / 票务查询
   - 相对日期 `transport_decision` 自动下单
   - 多轮退票补参
   - 多轮改签补参
   - HITL 自动审批回归

### 11.3 必过断言

- 创建订单后 `orders.status = booked`
- 取消订单后 `orders.status = cancelled`
- 改签后旧订单 `changed`、新订单 `booked`
- 库存扣减 / 回补正确
- 失败操作不得修改数据库
- HITL 审批拒绝后不得修改数据库

## 13. 验收标准

- 常驻服务数不超过 `4`
- 没有酒店域
- 没有 Streamlit
- 简单任务与复杂任务边界清楚
- 副作用操作全部有数据库断言
- 高风险副作用全部支持 HITL
- 缺字段统一追问
- 不依赖正则做核心字段抽取
- 支持今天 / 明天 / 后天这类相对日期的稳定理解
- 同一测试数据下结果稳定

## 14. 迁移策略

当前仓库以 `P3` 分支为唯一重构基线。

迁移顺序固定：

1. 完成 `SPEC.md`
2. 删除旧酒店方向和 `smartvoyage_v2` 平行方案
3. 删掉 Streamlit 主线
4. 合并天气/票务读服务
5. 保留并重构订单服务
6. 重写 orchestrator、schema、prompt
7. 重建 LangSmith 基线

## 15. 明确不接受的实现方式

- 再次回到“先写 prompt 再逐轮补 bug”的开发方式
- 每个小功能都拆一个独立服务
- 继续维护酒店、景点、旧 `travel_plan`
- 在没有规格的情况下直接写业务代码
- 在没有验收标准的情况下宣布功能完成
