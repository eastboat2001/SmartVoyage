# SmartVoyage Prompt Skill 化重构设计

## 1. 设计结论

当前 SmartVoyage **不适合**把整个工作流直接抽象成一个 runtime skill。

更合理的方向是：

- 保留现有 `orchestrator + 2 A2A + 2 MCP + LangGraph` 架构
- 把 `main_prompts.py` 从“单文件大 Prompt 集合”重构为一套 **skill-like prompt package**
- 借鉴 skill 的“渐进式披露、按场景加载、把规则和资源拆开”的思想
- 不把确定性业务逻辑、状态机和数据库规则迁移到 prompt / skill 中

一句话概括：

> 要 skill 化的是“提示词组织方式”，不是“业务执行架构”。

## 2. 当前痛点

当前 [main_prompts.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main_prompts.py) 混合了多种职责：

- 意图识别
- Query Plan 生成
- 交通决策规划
- 订单动作分类
- 审批语义解析
- 日期归一化
- 文案总结

这会带来几个问题：

- 单文件越来越大，维护成本持续上升
- 规则重用差，同类约束在多个 prompt 中重复
- 很难做到“节点只加载自己需要的规则”
- 不利于后续增加新域或新能力
- 很难清晰区分：
  - 哪些是 LLM 语义规则
  - 哪些是后端确定性约束
  - 哪些只是文案风格

## 3. 设计目标

本次设计目标：

- 把 Prompt 按业务域和节点职责拆开
- 支持渐进式披露，而不是每次都加载整段大 Prompt
- 保持当前调用方式可平滑迁移
- 保持现有结构化输出 schema 不变
- 不改变现有 A2A / MCP / LangGraph 主体逻辑

非目标：

- 不把 `orchestrator` 改造成 skill runtime
- 不用 prompt 取代 SQL 编译、库存校验、订单唯一命中、checkpoint
- 不在第一阶段引入复杂 DSL、数据库存储 Prompt、远程 Prompt Hub

## 4. 边界划分

### 4.1 应该 skill 化的部分

这些内容适合迁移到 skill-like prompt package：

- 意图识别规则
- 自然语言改写策略
- 读取类型分类规则
- Query Plan 提示规范
- 交通决策建议规则
- 自动下单意图判断规则
- 订单动作分类规则
- HITL 审批回复语义映射
- 退票 / 改签字段抽取规则
- 天气 / 票务总结风格

### 4.2 不应该 skill 化的部分

这些必须继续保留在代码中：

- [utils/orchestrator.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/orchestrator.py) 的节点流转和跨服务编排
- [a2a_server/travel_decision_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/travel_decision_server.py) 的 Query Plan -> SQL 编译
- [a2a_server/order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/order_server.py) 的 LangGraph workflow、pending context 合并、interrupt/resume
- [mcp_server/mcp_order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/mcp_server/mcp_order_server.py) 的库存、一致性、状态流转
- [utils/structured_outputs.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/structured_outputs.py) 的 schema 定义

## 5. 目标形态

建议把 Prompt 系统拆成三层：

1. `registry`
- 统一登记 prompt id、用途、schema、依赖资源

2. `package`
- 每个业务域一个 prompt package
- 每个 package 只收本域相关规则和参考材料

3. `builder`
- 根据 prompt id 和当前任务上下文动态拼装最终 Prompt

这样最终执行链路变成：

`调用节点 -> PromptRegistry -> PromptBuilder -> ChatPromptTemplate -> ResilientModelInvoker`

## 6. 建议目录结构

建议新增一个独立目录，例如：

```text
prompt_packages/
  __init__.py
  registry.py
  builder.py
  types.py
  shared/
    base_rules.md
    output_rules.md
    relative_date_rules.md
  intent/
    core.md
    travel_query_context.md
    references/
      multi_intent.md
      transport_decision.md
      follow_up_rules.md
      query_rewrite_rules.md
  travel_read/
    kind.md
    weather_plan.md
    ticket_plan.md
    weather_summary.md
    ticket_summary.md
    references/
      query_plan_rules.md
      travel_read_guardrails.md
  transport_decision/
    plan.md
    auto_order.md
    references/
      recommendation_rules.md
      degradation_rules.md
  order/
    action.md
    review_decision.md
    operation_extraction.md
    date_resolution.md
    references/
      pending_context_rules.md
      extraction_guardrails.md
      hitl_rules.md
```

说明：

- `core.md` / `*.md` 只放某个 prompt 的最小核心规则
- `references/` 放可选加载的补充规则
- `shared/` 放跨 prompt 通用约束，例如：
  - 不要编造未给出的业务事实
  - 相对日期解释规则
  - 结构化输出规则

## 7. Prompt Registry 设计

建议引入统一注册表，而不是继续靠 `SmartVoyagePrompts.xxx()` 硬编码分发。

### 7.1 最小注册结构

```python
@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    package: str
    template_path: str
    mode: Literal["structured", "text"]
    references: tuple[str, ...] = ()
    optional_references: tuple[str, ...] = ()
```

### 7.2 建议 prompt id

```text
intent.recognize
intent.travel-query-context
travel-read.kind
travel-read.weather-plan
travel-read.ticket-plan
travel-read.weather-summary
travel-read.ticket-summary
transport-decision.plan
transport-decision.auto-order
order.action
order.review-decision
order.date-resolution
order.operation-extraction
```

### 7.3 价值

- 调用点只依赖稳定 id，不依赖具体文件名
- 可以给同一个 prompt 配置固定依赖和可选依赖
- 后续可以做 prompt 版本化

## 8. Prompt Builder 设计

### 8.1 builder 职责

`PromptBuilder` 负责：

- 读取主模板
- 注入 shared rules
- 按任务上下文加载补充 references
- 输出最终 `ChatPromptTemplate`

### 8.2 上下文驱动的渐进式加载

建议支持一个轻量 `PromptBuildContext`：

```python
@dataclass
class PromptBuildContext:
    has_pending_context: bool = False
    has_relative_date: bool = False
    is_transport_decision: bool = False
    is_multi_intent: bool = False
    weather_degraded: bool = False
```

### 8.3 加载示例

#### `intent.recognize`

固定加载：

- `shared/base_rules.md`
- `shared/output_rules.md`
- `intent/core.md`

条件加载：

- 多意图场景时加载 `intent/references/multi_intent.md`
- 涉及复杂规划时加载 `intent/references/transport_decision.md`
- 需要追问时加载 `intent/references/follow_up_rules.md`

#### `travel-read.ticket-plan`

固定加载：

- `shared/base_rules.md`
- `shared/output_rules.md`
- `travel_read/ticket_plan.md`

条件加载：

- 相对日期时加载 `shared/relative_date_rules.md`
- 复杂票务约束时加载 `travel_read/references/query_plan_rules.md`

#### `order.operation-extraction`

固定加载：

- `shared/base_rules.md`
- `shared/output_rules.md`
- `order/operation_extraction.md`

条件加载：

- 有待补上下文时加载 `order/references/pending_context_rules.md`
- 改签场景时加载 `order/references/extraction_guardrails.md`

## 9. 与现有代码的接入方式

### 9.1 第一阶段不要直接删除 `SmartVoyagePrompts`

最稳妥的方案不是一步删掉 [main_prompts.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main_prompts.py)，而是先把它降级成兼容层：

```python
class SmartVoyagePrompts:
    @staticmethod
    def intent_prompt():
        return prompt_registry.build("intent.recognize")
```

好处：

- 调用点几乎不用动
- 可以渐进替换
- 回归失败时更容易定位

### 9.2 推荐新增入口

建议新增：

- `utils/prompt_registry.py`
- `prompt_packages/registry.py`
- `prompt_packages/builder.py`

当前调用点保持不变，例如：

- [utils/orchestrator.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/utils/orchestrator.py)
- [a2a_server/travel_decision_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/travel_decision_server.py)
- [a2a_server/order_server.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/a2a_server/order_server.py)

只替换它们背后的 prompt 获取方式。

## 10. 现有 Prompt 到新结构的映射

### 10.1 orchestrator 域

- `intent_prompt` -> `intent.recognize`
- `travel_query_context_prompt` -> `intent.travel-query-context`
- `transport_decision_prompt` -> `transport-decision.plan`
- `auto_order_intent_prompt` -> `transport-decision.auto-order`

### 10.2 travel read 域

- `travel_read_kind_prompt` -> `travel-read.kind`
- `weather_query_plan_prompt` -> `travel-read.weather-plan`
- `ticket_query_plan_prompt` -> `travel-read.ticket-plan`
- `summarize_weather_prompt` -> `travel-read.weather-summary`
- `summarize_ticket_prompt` -> `travel-read.ticket-summary`

### 10.3 order 域

- `order_action_prompt` -> `order.action`
- `review_decision_prompt` -> `order.review-decision`
- `date_resolution_prompt` -> `order.date-resolution`
- `order_operation_extraction_prompt` -> `order.operation-extraction`

## 11. 什么应该放进 shared rules

适合抽到 `shared/` 的只有“跨多个 prompt 的通用限制”，例如：

- 不要编造数据库中不存在的事实
- 只返回结构化字段，不输出 markdown
- 相对日期解释基于 `current_date`
- 用户未明确给出的城市/车次/航班号不能脑补

不应该放进 `shared/` 的内容：

- 具体某个 schema 的字段定义
- 某个单独任务的示例
- 订单域特有规则
- transport decision 的权衡逻辑

否则 shared 会重新膨胀成另一个 `main_prompts.py`。

## 12. Prompt 载体形式选择

建议分两步走。

### 12.1 第一阶段：Python + Markdown 混合

- Prompt 正文放 `.md`
- builder 负责读取 `.md` 并拼成字符串
- 最终仍然返回 `ChatPromptTemplate.from_template(...)`

优点：

- 成本低
- 好 diff
- 好复盘
- 不影响现有 `ResilientModelInvoker`

### 12.2 第二阶段：如果确实有必要，再引入更强配置

可选再考虑：

- YAML frontmatter
- Prompt 版本号
- Prompt metadata
- Prompt 实验开关

当前没必要第一天就做重。

## 13. 风险与约束

### 13.1 风险

- 拆得过细会让 prompt 查找变复杂
- `shared/` 设计不好会重新失控
- 动态拼装不当会导致回归难查
- Markdown 资源如果命名和边界不清，会形成“文档散落”

### 13.2 控制策略

- 第一阶段只拆成 3 个 package：`intent`、`travel_read`、`order`
- `transport_decision` 单独作为 1 个 package，不再继续细拆
- 每个 prompt 最多 1 个主模板 + 2 到 3 个补充 reference
- builder 输出最终 prompt 时，把加载来源打到日志里

## 14. 推荐迁移顺序

### Phase 1：兼容层重构

- 新增 `prompt_packages/`
- 新增 registry 和 builder
- 保留 `SmartVoyagePrompts` 作为 facade
- 行为保持完全等价

### Phase 2：拆主文件

- 先拆 `travel_read` 域
- 再拆 `order` 域
- 最后拆 `intent + transport_decision`

原因：

- `travel_read` 规则最清晰，风险最低
- `order` 次之
- `intent` 和 `transport_decision` 牵涉跨域语义，最后做更稳

### Phase 3：加渐进式披露

- 为特定 prompt 增加可选 references
- 按 `PromptBuildContext` 动态加载
- 保持调用接口不变

### Phase 4：补测试

- 给 builder 补单测
- 给 registry 补单测
- 给关键 prompt 的最终拼装结果补快照测试
- 跑现有 LangSmith 基础集确认行为无明显回退

## 15. 第一版实施建议

如果现在开始动手，建议第一版只做这些：

1. 新建 `prompt_packages/`
2. 实现最小 `PromptSpec` + `PromptRegistry` + `PromptBuilder`
3. 把这 5 个 prompt 先迁出去：
   - `travel_read_kind_prompt`
   - `weather_query_plan_prompt`
   - `ticket_query_plan_prompt`
   - `order_action_prompt`
   - `review_decision_prompt`
4. 保持 [main_prompts.py](/e:/Workstudy/projectstudy/04_Project/02_AIProject/Agent/SmartVoyage/04_Code/sh01_agent/SmartVoyage/main_prompts.py) API 不变
5. 跑基础回归后，再继续迁移剩余 prompt

这是最小可行方案，风险最低。

## 16. 最终建议

结论不是“把当前项目变成一个 skill”，而是：

- **把 Prompt 层 skill 化**
- **把执行层继续留在代码里**

对 SmartVoyage 而言，最优结构是：

- 代码负责：
  - 状态机
  - 编排
  - SQL 编译
  - MCP 调用
  - DB 真相
  - HITL checkpoint

- prompt package 负责：
  - 任务语义规则
  - 抽取规范
  - Query Plan 规范
  - 总结风格
  - 可选 references 的渐进加载

这条路线既能解决 `main_prompts.py` 过重的问题，又不会破坏你现在已经稳定下来的工程边界。
