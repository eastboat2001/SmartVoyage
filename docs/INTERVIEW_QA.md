# SmartVoyage 超细节面试 Q&A 题库

## 使用方式

这份题库不是泛泛的项目介绍，而是按项目目录结构拆开的“逐文件追问手册”。

推荐使用方式：

1. 先看“项目总览”和 `agents/`，掌握主链路。
2. 再看 `core/`、`llm/`、`infra/`、`mcp_server/`，补足工程细节。
3. 最后看 `skills/`、`langsmith_eval/` 和 `test/`，准备“为什么这样做”和“怎么验证”的问题。

回答原则：

- 永远先讲这个文件在系统里的职责，再讲关键实现，再讲为什么这么做。
- 面试官如果继续追问，就从“具体函数 / 字段 / 配置项 / 状态流转 / 边界条件”往下答。
- 不要装作每一行都背过，但要能把关键代码背后的动机讲清楚。

---

## 一、项目总览级问题

### Q1：这个项目一句话怎么定义？

SmartVoyage 是一个聚焦交通票务场景的多代理事务型 Agent 系统。它不是泛化聊天机器人，而是把“查询、决策、执行”串成一条可控链路：只读查询由 `TravelReadSubagent` 处理，订单事务由 `OrderSubagent` 处理，跨链路编排由 `SmartVoyageSupervisor` 负责，工具边界通过 MCP 暴露，订单高风险操作通过 LangGraph + HITL + checkpoint 控制，性能和回归则用 metrics、缓存、分模型路由和 LangSmith 评测来闭环。

### Q2：这个项目最值得讲的技术点是什么？

最值得讲的不是单一功能，而是完整工程闭环：

- 架构上，有 Supervisor + 两个本地子代理的明确职责拆分。
- 工具上，用 MCP 隔离数据库和副作用操作。
- Prompt 上，用 Skill Runtime 管理规则资产，而不是把 Prompt 散落在 Python 里。
- 状态上，订单链路用 LangGraph 建模，支持 HITL 审批和恢复。
- 性能上，有 deterministic formatting、Redis 缓存、phase metrics、light/fallback model routing。
- 验证上，有单测、组件测试、E2E 和 LangSmith 回归，还包含数据库副作用断言。

### Q3：为什么项目只拆两个子代理，不拆更多？

因为当前复杂度核心不在业务名词，而在执行语义。只读查询强调低时延、缓存和稳定格式化；事务执行强调状态机、审批、恢复和一致性控制。按“查 / 办”拆分比按“天气 / 票务 / 下单 / 退票”拆分更符合当前 scope。后者会增加 agent hop、上下文传递和编排复杂度。

### Q4：为什么不用单 Agent？

单 Agent 前期实现快，但会带来三个问题：

- 查询和事务逻辑混在一起，Prompt 膨胀很快。
- 有副作用的操作缺少明确边界，不利于 HITL 和状态恢复。
- 后续做性能优化、缓存和问题定位时，所有逻辑耦在一起，很难观测。

所以项目选择了编排层和执行层分离。

---

## 二、根目录文件

### 文件：`main.py`

#### Q1：`main.py` 在整个项目里的定位是什么？

它是 CLI 入口，用来做本地手工调试。它不承担业务逻辑，只负责维护一份 CLI 会话状态，然后把用户输入转交给 `SmartVoyageSupervisor`。

#### Q2：为什么 `main.py` 里要维护 `conversation_history` 和 `pending_order_context` 两个全局变量？

因为这两个变量分别解决两个不同问题：

- `conversation_history` 用来保留自然语言对话上下文，供 Supervisor 和子代理做意图识别、query rewrite 和多轮补问。
- `pending_order_context` 用来显式承接订单链路的结构化状态，比如缺槽字段、HITL review payload、thread_id 等。

一个偏文本上下文，一个偏工作流上下文，不能混成同一种状态。

#### Q3：为什么 CLI 不直接调用子代理，而是始终调用 Supervisor？

因为 Supervisor 才是真正的系统入口。只有它知道当前输入属于哪个子域、是否需要 home city 补问、是否需要走 `transport_decision`、是否处于 HITL 恢复阶段。CLI 只是输入输出壳层，不应该复制编排逻辑。

#### Q4：`process_user_input()` 里为什么先把 `User:` 追加进 `conversation_history`，再调用 Supervisor？

因为意图识别和 query rewrite 依赖当前轮输入也出现在对话历史里。很多多轮场景，比如“帮我退票”后再补充“退明天那张”，如果当前轮没进历史，就会影响模型对上下文的理解。

#### Q5：`pending_order_context.get("action") == "hitl_review"` 为什么要在 CLI 输出审批提示？

因为 OrderSubagent 在审批中断时返回的是 `input_required` 状态，但这类 `input_required` 和普通补槽不是一回事。普通补槽是缺字段，审批则是在等待 yes/no。CLI 明确提示用户输入 yes/no，是为了降低误操作概率。

#### Q6：面试官如果问“CLI 设计有哪些局限”怎么答？

可以答：

- 全局变量是单会话、单用户的，适合本地调试，不适合并发。
- 没有持久化 conversation history，重启后丢失。
- `messages` 列表现在只用于本地展示，没有统一消息协议。
- 但这恰好符合它的定位：本地 smoke test 壳层，而不是生产接口层。

### 文件：`web_app.py`

#### Q1：`web_app.py` 的职责是什么？

它提供 FastAPI Web 入口，把 Supervisor 暴露给浏览器，同时维护基于 cookie 的会话状态，支持多轮对话、HITL pending 展示和 metrics 返回。

#### Q2：为什么 `WebSessionState` 要同时存 `messages`、`conversation_history` 和 `pending_order_context`？

这三个字段各自服务不同层：

- `messages` 面向前端渲染。
- `conversation_history` 面向 LLM 和编排层的文本上下文。
- `pending_order_context` 面向订单工作流恢复。

如果只存 `messages`，每次都要从前端消息重组上下文，不稳定也不高效。

#### Q3：为什么 `SessionStore` 只是内存字典，不做 Redis / DB 持久化？

因为当前项目是本地单机场景，Web 主要用于演示和面试。对这个阶段来说，最重要的是把 Supervisor 链路稳定跑通，而不是先引入分布式会话复杂度。它的边界是：重启服务会丢 Web 会话，但不影响订单 checkpoint 文件恢复。

#### Q4：为什么 `process_chat_turn()` 里是同步调用 Supervisor，但外层接口用 `asyncio.to_thread()` 包一层？

因为 Supervisor 和子代理内部大量逻辑是同步 Python 调用，加上部分链路会阻塞较久。FastAPI 接口如果直接同步跑，会占住事件循环线程；用 `to_thread()` 可以把阻塞型业务逻辑放进线程执行，避免 Web 入口层阻塞。

#### Q5：`/api/bootstrap` 的作用是什么？

它是前端初始化接口，用来恢复当前 session 的 messages、agent cards、pending 状态和 review payload。这样刷新页面后，前端还能知道当前是否处于审批态。

#### Q6：为什么 `review_payload` 单独返回，而不是让前端自己解析 `pending_order_context`？

因为 `pending_order_context` 是工作流原始载荷，前端只关心“是否待审批”和“审批摘要是什么”。单独给 `review_payload` 可以减轻前端解析复杂度，也能降低前后端耦合。

#### Q7：如果面试官问“Web 层还可以怎么增强”怎么答？

可以答：

- 把 `SessionStore` 换成 Redis 或数据库，支持多实例。
- 增加接口测试，特别是 `/api/chat` 和 `/api/reset`。
- 对 `metrics` 做可视化面板。
- 给 HITL 增加明确的 approve/reject UI，而不只是文本框。

### 文件：`run_all.py`

#### Q1：为什么需要 `run_all.py`？

因为这个项目至少有两个 MCP 服务，还有可选的 CLI 和 Web。如果每次都手工分别起进程，联调成本很高。`run_all.py` 把它们统一成一个启动入口，简化本地演示和回归环境准备。

#### Q2：`SERVICES` 为什么只列 MCP，不把 Supervisor 单独起一个服务？

因为 Supervisor 不是独立进程服务，而是嵌在 CLI 和 Web 入口内部。MCP 才是真正需要独立进程运行的工具层边界。

#### Q3：为什么 `log_path_for()` 把所有 MCP 输出写到同一个 `mcp.log`？

这样做的目的不是精细分日志，而是方便本地联调时集中看工具层问题。当前阶段日志主要是为了定位链路故障，不是为了做复杂审计拆分。

#### Q4：`prepare_log_dir()` 为什么启动时会清空现有 `.log` 文件？

因为它的定位是本地演示启动器。每次启动清空旧日志，可以避免看日志时混入上一次的历史输出。当然它的代价是丢失长期日志，因此不适合生产环境。

#### Q5：为什么对子进程设置 `PYTHONIOENCODING=utf-8` 和 `PYTHONUTF8=1`？

因为项目大量日志和提示是中文，Windows 环境容易出现编码问题。显式设置 UTF-8 能降低 CLI、MCP、Web 输出乱码的风险。

---

## 三、`agents/` 目录

### 文件：`agents/supervisor.py`

#### Q1：`SmartVoyageSupervisor` 的核心职责是什么？

核心职责是编排，不是直接做所有业务。它负责：

- 意图识别
- 用户偏好加载
- home city follow-up
- 路由到对应子代理
- 编排 `transport_decision`
- 聚合 metrics
- 在 HITL 恢复后对结果做统一收尾

#### Q2：为什么 `DEFAULT_AGENT_METADATA` 要写在 Supervisor 里？

因为 Web 和 CLI 的 agent cards 是从 Supervisor 暴露出去的，而不是每个入口自己拼。这样所有入口看到的 agent 描述是一致的，也避免 UI 层知道太多 agent 内部细节。

#### Q3：`UserPreferenceProfile` 的作用是什么？为什么不直接用 dict？

它把用户偏好字段结构化了，并提供 `summary_text()` 统一生成用户画像摘要。用 dataclass 的好处是：

- 字段有默认值，不会到处判空。
- 类型更明确。
- 可以把“偏好字段 -> 提示词摘要”的逻辑收敛在一个地方。

#### Q4：为什么 `summary_text()` 里把偏好做成中文摘要字符串，而不是直接把结构化字段喂给 Prompt？

因为下游 `decision_plan` 更适合接自然语言偏好摘要，而不是再次学习字段语义。例如“预算中等、偏好直达、偏好上午出发”这种自然语言约束，更方便模型直接纳入决策理由。

#### Q5：`recognize_intent()` 为什么只截取最近 6 行对话？

因为意图识别任务不需要完整长对话。保留最近几轮能兼顾上下文和成本，避免把历史噪声全部塞进去。这是一种上下文裁剪策略。

#### Q6：`_normalize_intent_result()` 为什么要额外判断显式车次/航班号并关闭 `needs_home_city_follow_up`？

因为如果用户已经明确说了 `G5` 或 `CA1509`，说明他不是在泛查“从哪出发都行”的票，而是已经指定了具体运输编号。这时再追问 home city 很容易显得多余甚至错误。

#### Q7：`process_user_input()` 是整个系统最核心的函数，面试官追问时应该怎么拆解？

可以按顺序答：

1. 生成 request_id 并初始化 metrics。
2. 如果当前轮带着 `pending_order_context.action == hitl_review`，直接进入审批恢复逻辑。
3. 否则先做意图识别。
4. 再尝试把 pending order context 与本轮输入合并，判断是不是订单补充轮。
5. 处理 `out_of_scope`。
6. 处理 intent 层 follow-up。
7. 处理 home city follow-up。
8. 如果是 `transport_decision`，走显式工作流。
9. 否则按 intent 顺序调用对应子代理。
10. 汇总响应、路由信息、pending context 和 metrics。

#### Q8：为什么先处理 HITL review，再做普通意图识别？

因为审批恢复本质上已经有明确工作流状态了，不应该再让普通意图识别打断它。如果用户在审批阶段回复了 yes/no，系统应该优先恢复订单工作流，而不是把 yes/no 当普通自然语言去重新分类。

#### Q9：`_merge_pending_order_context()` 的意义是什么？

它解决的是“用户上一轮处于订单补槽，但本轮输入很短，比如‘明天那张’”这类场景。单看本轮文本，意图识别可能识别不出来；把 pending context 拼进用户提示后再识别，就更容易还原为订单意图。

#### Q10：为什么 `pending_order_context` 遇到只读意图时会被清空？

因为如果用户明确转去了天气、时间、查票或交通决策，就说明他当前不再沿着订单补槽链路继续。这时继续保留旧 pending context，反而可能污染当前路由。

#### Q11：`_with_user_context()` 为什么不是把 username 存在外部 session，而是每次注入到 query 里？

因为 OrderSubagent 后续可能还会经过 Prompt、工具调用甚至 agent 工具链。如果用户名不显式写进输入，很容易在中间层丢失。把“当前用户：xxx”前缀化，是一种简单但稳定的上下文显式传递策略。

#### Q12：为什么 `with_order_action()` 和 `with_travel_read_kind()` 用标签字符串，而不是单独字段传递？

因为当前 LocalAgentRequest 是文本驱动接口，把意图标签嵌进文本有几个优点：

- 不需要改 agent 协议结构。
- 可以在多轮上下文里携带。
- 可以被子代理快速提取并绕过重复分类。

这是在当前架构下权衡复杂度后的实现。

#### Q13：`transport_decision_workflow` 为什么单独用 LangGraph，而不是简单串函数？

这里实际上不是为了循环或复杂图，而是为了把多阶段编排显式节点化：`prepare -> weather -> plan -> ticket -> order/finalize`。这样更清晰，也更容易单测节点级行为和后续扩展。

#### Q14：`_route_transport_decision_after_ticket()` 为什么只有“order”和“finalize”两个分支？

因为 transport_decision 的核心选择只有一个：要不要继续进入订单链路。是否继续下单，取决于 `should_order` 和票务查询是否成功，两者不满足就直接收尾。

#### Q15：`_transport_decision_finalize_node()` 为什么要单独构造 `response_prefix`？

因为审批态下可能先给出“天气判断 + 出行建议 + 票务结果”，然后后续再恢复订单结果。为了让恢复后的最终回答保留建议上下文，需要把这个前缀写进 pending context，审批通过后再拼回去。

#### Q16：为什么 `weather_degraded` 和 `weather_no_data` 要区别处理？

它们代表不同风险：

- `weather_degraded` 是服务异常，意味着工具链路不稳定。
- `weather_no_data` 是数据集没有命中，不是服务坏了。

这两种情况对面试官来说很重要，因为它体现系统把“故障”和“缺数据”区分开了。

#### Q17：`_build_ticket_fact_response()` 为什么只在用户显式给出 transport_no 时才走直出事实响应？

因为这类查询用户通常想要的是一个准确的票务事实，而不是“前 3 条推荐列表”。如果已经指定车次或航班号，返回一条更简洁的事实句更符合用户意图，也更稳定。

#### Q18：`_related_order_context()` 为什么要查用户是否已订该车次/航班？

因为这能把“读取链路”和“订单链路”做弱关联，让票务查询结果更贴近业务。用户问某班次余票时，如果系统能补一句“你已经订过 1 张”，体验和业务价值都更高。

#### Q19：如果面试官问 `supervisor.py` 里最体现工程思维的细节是什么？

可以答三点：

- 用显式工作流和 helper 函数拆分编排，不把逻辑压成一个大 Prompt。
- 用 `pending_order_context` 和 tag 注入让上下文传递变成显式协议，而不是隐式依赖聊天历史。
- 在 finalize 阶段保留 response prefix，使审批恢复前后结果语义连续。

### 文件：`agents/travel_read.py`

#### Q1：TravelReadSubagent 的输入到输出主链路是什么？

主链路是：

1. 从对话里拿最新用户 query。
2. 先判断当前是 `time / weather / ticket` 哪种只读请求。
3. 如果是天气或票务，生成结构化 query plan。
4. 编译成 SQL。
5. 调用 TravelRead MCP。
6. 把结构化结果转成 deterministic 文本。
7. 返回 `LocalAgentResponse`。

#### Q2：为什么 `latest_query()` 要专门从 `User:` 标记里反解最后一轮？

因为 TravelRead 只关心当前用户最新问题，不想让整段对话污染 plan 生成。这个函数把长对话降维成当前轮 query，是一种轻量的上下文切片。

#### Q3：`infer_kind()` 为什么先看 `TRAVEL_READ_KIND` 标签，再决定要不要走 LLM？

因为如果 Supervisor 已经完成意图分类，就没必要再让 TravelRead 重复分类。标签优先能减少一次 LLM 调用，是明显的性能优化点。

#### Q4：为什么天气和票务 plan 仍然要用 LLM，而 SQL 编译不用？

因为“从自然语言抽出 route/date/type/transport_no”是语义问题，适合 LLM；但“把结构化字段稳定拼成 SQL”是确定性问题，适合代码。项目把语义层和执行层明确分开了。

#### Q5：`_normalize_ticket_plan()` 为什么在“显式 transport_no 且用户没给日期”时要清空 `date_from/date_to`？

这是一个防幻觉修正。模型看到车次号时，可能会脑补一个日期；但如果用户根本没给日期，系统不应该擅自补一个日期条件，否则可能查不到本来应该命中的票。

#### Q6：`compile_weather_sql()` 和 `compile_ticket_sql()` 为什么看起来像拼 SQL 字符串而不是 ORM？

因为这个项目里的只读 MCP 更像是“可解释查询层”，SQL 本身就是重要中间产物。把 SQL 显式产出有几个好处：

- 更容易日志化和调试。
- 更容易做缓存键哈希。
- 更容易在面试时解释“从自然语言到结构化查询”的中间态。

#### Q7：面试官如果问“手写 SQL 会不会注入风险”怎么答？

要诚实回答：当前做法不是生产级安全方案，但已经做了最低限度的字符串转义 `_sql_literal()`，而且系统输入域较窄。若要生产化，应该改成参数化查询或 query builder。这个回答比假装没风险更稳。

#### Q8：`compile_ticket_sql()` 为什么要把 `limit` 限制在 1 到 20 之间？

因为票务查询是给用户看的，不是批量导出接口。限制上限可以防止模型异常输出极大值，也避免一次查太多无意义数据，属于防御式编程。

#### Q9：`build_weather_summary()` 和 `build_ticket_summary()` 为什么用了 deterministic formatting？

因为天气和票务结果结构很规整，继续调用 LLM 总结只是增加时延和 token，不增加信息质量。确定性模板还带来一个额外收益：回归测试更稳定。

#### Q10：`format_weather_response()` / `format_ticket_response()` 为什么把 `no_data` 映射成 `input_required`，而不是 `failed`？

因为没查到数据不等于系统失败。很多时候是用户条件不完整、城市不存在或日期超出数据范围。把它归为 `input_required` 或用户可修正状态，更符合业务语义。

#### Q11：`_run_tool()` 为什么在子代理内部统一增加 `tool_call_count`？

因为从 agent 视角看，每次 MCP 调用就是一次工具调用。把工具调用计数放在统一入口里，比在每个天气/票务/time 分支里单独记更可靠。

#### Q12：为什么 `execute_ticket_plan()` 和 `invoke()` 都有 `asyncio.run` + 新 event loop 的兜底？

因为这些方法既可能在普通同步上下文里被调用，也可能在已有事件循环环境里被间接调用。用双路径兜底，可以避免“event loop already running”类问题，提高兼容性。

#### Q13：TravelReadSubagent 最容易被追问的边界条件是什么？

- 用户只给车次号，不给日期。
- 用户给相对日期。
- 查询返回 `no_data` 和 `error` 的区别。
- Redis 命中和未命中的 metrics 行为。
- 为什么 time 查询也走 TravelRead 而不是 Supervisor 自己返回。

### 文件：`agents/order.py`

#### Q1：OrderSubagent 的核心职责是什么？

它负责所有订单事务相关动作：查单、下单、退票、改签、补槽、审批解析和 LangGraph 工作流恢复。它不是单纯“工具调用封装”，而是系统里最强状态感知的 agent。

#### Q2：为什么 `extract_username()` 要从对话里反向扫描“当前用户”？

因为在订单链路里，用户名是绝对不能丢的信息。反向扫描保证取到最近一次显式声明的用户，避免旧上下文污染。

#### Q3：`_fast_normalize_date()` 为什么先做规则归一化，再 fallback 到 LLM？

因为今天/明天/后天、显式日期、月日表达这类场景完全可以代码快速处理，没必要每次都消耗模型。这是性能和确定性的双优化。

#### Q4：什么时候才会走 `date_resolution` 的 LLM 调用？

当 query 里出现模糊日期迹象，但规则又无法确定归一结果时。比如只有“周末”“未来几天”之类，更适合交给模型做解释。

#### Q5：`PENDING_CONTEXT_PATTERN` 的价值是什么？

它让订单工作流状态能被嵌入文本协议并被恢复。Supervisor 通过 `[PENDING_ORDER_CONTEXT]...[/PENDING_ORDER_CONTEXT]` 把结构化状态注入给 OrderSubagent，后者解析后恢复之前的补槽或 HITL 状态。

#### Q6：为什么 `classify_order_action()` 的优先级是 pending action > explicit tag > LLM 分类？

因为它体现了“确定性优先”。如果 pending context 已经说明当前在 cancel/change 补槽，就不该重新分类；如果 Supervisor 显式打了 `ORDER_ACTION` 标签，也不该再浪费模型。只有都没有时，才让模型做动作分类。

#### Q7：`parse_review_decision()` 为什么即使是 yes/no 这种看似简单输入，也仍然经过 Skill 解析？

因为真实用户回复不一定是标准 yes/no，可能是“可以”“帮我确认”“算了吧”“什么意思”。统一走 review_decision Skill，可以处理模糊表达并给出追问，而不是只支持硬编码关键词。

#### Q8：`normalize_missing_fields()` 为什么把 `current_order_selector` 和 `new_target` 这种抽象字段加进 missing_fields？

因为退票和改签真正缺的不是某一个具体字段，而是一组业务上足以唯一定位或更新订单的信息。抽象成 `current_order_selector` / `new_target`，能让后续 follow-up 生成更稳定，不被单个字段绑死。

#### Q9：`default_follow_up_message()` 为什么 cancel 和 change 分开写？

因为退票和改签的缺槽语义不一样。退票只需要定位当前订单；改签除了定位当前订单，还必须提供新的目标信息。把这两者混成一套 follow-up 文案会不准确。

#### Q10：`build_pending_order_context()` 为什么只保留已提取字段，而不是整个 extraction 对象？

因为 pending context 是跨轮传输的最小状态，它只需要保留后续继续完成任务所必要的信息。把整个 extraction 对象都带上会增加噪声，也会让协议不稳定。

#### Q11：为什么 `run_order_agent()` 还保留了一个 tool-agent 入口，而主链路下单最终没有完全依赖它？

这是因为项目演进过。当前主链路下单已经尽量走更确定的“先查票，再直接调用订单工具”，但保留 `run_order_agent()` 仍然有实验和扩展价值，也体现出这个项目不是一开始就完全定型，而是逐步从更自由的 agent 方式收敛到更确定的执行方式。

#### Q12：OrderSubagent 的 LangGraph 工作流为什么是这几个节点？

因为它刚好对应订单事务里真正稳定的阶段：

- `prepare`：识别动作、清理上下文、抽取参数
- `review`：进入人工审批
- `query_orders`：纯查询分支
- `cancel_order` / `change_order`：执行变更
- `lookup_tickets`：下单前先查真实票
- `create_order`：真正创建订单

这套节点拆分贴合业务语义，不是为了图复杂。

#### Q13：为什么 `create_order` 分支要先走 `lookup_tickets_node()`，而不是让用户输入什么就直接下单？

因为系统要求基于“真实票务结果”下单，不能凭自然语言直接编造车次、价格和席位。先查票再下单，可以避免模型编造参数，也让下单依据更可解释。

#### Q14：`_lookup_tickets_node()` 为什么把当前订单 query 用 `with_travel_read_kind(..., "ticket")` 包装后再交给 TravelRead？

这是为了跳过 TravelRead 的重复 read_kind 分类，直接告诉它“你现在就是在查票”。这比完全依赖自然语言再识别一次更省调用也更稳定。

#### Q15：为什么 `ticket_result.state != completed` 时，有时返回 `input_required`，有时返回 `failed`？

因为查票失败可能有两类：

- `input_required`：信息不足，用户可以补充。
- `failed`：工具异常或服务错误。

把这两类情况混在一起，会让前端和 Supervisor 不知道该继续追问还是直接报错。

#### Q16：`_build_review_payload()` 为什么对 create_order 和 cancel/change 用了两套不同的 payload 生成方式？

因为下单审批的数据来源于“查到的目标票务”；退票和改签审批的数据来源于“已锁定的当前订单和目标信息”。两个分支的数据来源不同，结构也不同，所以需要分别构造。

#### Q17：`interrupt(review_payload)` 在这里的价值是什么？

它不是简单停一下，而是把当前工作流上下文交给 LangGraph checkpoint 记录下来。这样用户后续回复 yes/no 时，可以基于同一个 thread_id 恢复，而不是重新走一遍前面的抽取和查票流程。

#### Q18：为什么审批被拒绝时 `final_state` 仍然是 `completed`？

因为从工作流视角看，“拒绝执行”也是一个正常完成结果，不是系统异常。它完成的是“安全地终止副作用操作”。

#### Q19：`_create_order_node()` 为什么只取第一张票？

因为当前业务假设是“优先选择最合适的一张真实票”，而 TravelRead 查询结果本身已经按出发时间排序。当前项目没有做复杂排序策略或多目标优化，所以选第一张是一个可解释、可测试的确定性策略。

#### Q20：OrderSubagent 里最容易被面试官细问的函数是哪几个？

最容易被细问的是：

- `_fast_normalize_date()`：为什么先规则后模型
- `classify_order_action()`：优先级如何设计
- `_extract_operation_payload()`：缺槽、follow-up、pending context 怎么协同
- `_review_node()`：为什么要 interrupt
- `_create_order_node()`：为什么基于真实票务结果下单
- `ainvoke()`：HITL 恢复和首次执行如何区分

---

## 四、`core/` 目录

### 文件：`core/config.py`

#### Q1：为什么要做一个统一的 `Config` 类？

因为项目涉及模型、数据库、缓存、时区、日志、checkpoint、fallback、light model 等大量运行参数。如果每个模块自己读环境变量，会出现默认值不一致、测试难 mock、启动时行为分散的问题。统一配置层是工程化必需品。

#### Q2：`_first_env()` 的价值是什么？

它允许同一个配置项兼容多个环境变量名称，比如 `SMARTVOYAGE_API_KEY`、`OPENAI_API_KEY`、`DASHSCOPE_API_KEY`。这能降低本地迁移和多 provider 切换成本。

#### Q3：为什么 `load_dotenv(..., override=False)`？

因为它希望“系统环境变量优先于 .env”。这对部署和 CI 更友好，避免本地 `.env` 不小心覆盖真正运行环境里的变量。

#### Q4：项目里最关键的配置项有哪些？

最关键的几组是：

- 主模型：`SMARTVOYAGE_PROVIDER`、`SMARTVOYAGE_MODEL_NAME`、`SMARTVOYAGE_BASE_URL`、`SMARTVOYAGE_API_KEY`
- fallback 模型：`SMARTVOYAGE_FALLBACK_PROVIDER`、`SMARTVOYAGE_FALLBACK_MODEL_NAME`
- light 模型：`SMARTVOYAGE_LIGHT_MODEL_NAME`、`SMARTVOYAGE_LIGHT_MODEL_PHASES`
- 数据库：`SMARTVOYAGE_DB_HOST`、`SMARTVOYAGE_DB_USER`、`SMARTVOYAGE_DB_PASSWORD`、`SMARTVOYAGE_DB_NAME`
- 时间：`SMARTVOYAGE_TIMEZONE`、`SMARTVOYAGE_NOW_OVERRIDE`
- checkpoint：`SMARTVOYAGE_ORDER_CHECKPOINT_PATH`
- 缓存：`SMARTVOYAGE_CACHE_ENABLED`、`SMARTVOYAGE_REDIS_URL`

#### Q5：`SMARTVOYAGE_LIGHT_MODEL_PHASES` 为什么是 CSV，而不是布尔开关？

因为系统不是简单的“开不开轻模型”，而是按任务阶段做灰度。CSV 让 phase 级白名单更灵活，也方便在 `.env` 里快速做实验。

#### Q6：为什么 `cache_enabled` 要把 `0/false/no` 都识别为关闭？

这是兼容不同环境变量书写习惯的防御式设计，减少配置误用。

#### Q7：`_warn_if_needed()` 的工程意义是什么？

它不是强制抛错，而是用可读 warning 提醒使用者：

- `.env` 不存在
- provider 不支持
- openai_compatible 模式没配 API Key

这样本地启动时更易诊断，但不会把某些可选配置场景直接拦死。

#### Q8：如果面试官问“为什么配置里默认 DB 密码写 123456，不安全吧？”怎么答？

要明确说这是本地 demo / 面试工程默认值，只用于降低开箱成本。生产环境必须由外部密钥管理或环境变量注入，不应保留硬编码弱口令。

### 文件：`core/prompts.py`

#### Q1：为什么还需要一个 `SmartVoyagePrompts`，不是有 Skill Runtime 了吗？

Skill Runtime 负责“根据 role/capability/flags 加载哪份 Prompt 资产”，而 `SmartVoyagePrompts` 负责“根据当前业务上下文判断 flags”。它是业务语义和 Prompt 资产之间的适配层。

#### Q2：`_contains_relative_date()` 这种函数看起来很小，为什么值得单独写？

因为 relative date 是多个 skill 共享的条件：intent recognition、weather_plan、ticket_plan、decision_plan、date_resolution 都可能受它影响。把这类逻辑统一收敛，能避免各处判断不一致。

#### Q3：`_has_query_rewrite_context()` 为什么只看对话行数是否大于 1？

因为当前实现的目标不是复杂会话分析，而是粗粒度判断“这是不是纯首轮问题”。多轮时才需要引入 query rewrite 类补充规则。

#### Q4：为什么 `transport_decision_prompt()` 要根据天气结果再决定是否加 `weather_degraded` / `weather_no_data` flag？

因为 decision plan 的 Prompt 需要根据上游数据状态切换规则：如果天气服务异常或无数据，决策策略应该更保守。这个 flag 让 Prompt 层感知上游质量，而不是一刀切。

#### Q5：`order_operation_extraction_prompt()` 为什么只有 `has_pending_context` 和 `is_change_order` 两个 flag？

因为订单抽取的变化点主要就在这两个维度：

- 有没有跨轮继承上下文
- 是不是改签，改签会多出一组新目标字段

目前这两个 flag 已经能覆盖主要分支，没必要过度设计更多条件。

### 文件：`core/clock.py`

#### Q1：为什么单独做一个时间模块？

因为时间语义对这个项目很关键：天气、票务、相对日期、LangSmith 回归、E2E 测试都依赖统一当前时间。如果每个模块各自 `datetime.now()`，测试就会变得不稳定。

#### Q2：`now_override` 的核心价值是什么？

它让系统可以在固定时间语义下运行。例如评测时把“明天”稳定映射到 `2026-03-21`，这样多次运行结果一致。

#### Q3：为什么 `get_now()` 支持 naive datetime 和 timezone-aware datetime 两种 override？

为了兼容不同输入来源。如果 override 没带时区，就按当前配置时区本地化；如果已经带时区，就转换到目标时区。这样更鲁棒。

#### Q4：为什么 `get_current_time_payload()` 里返回 `weekday` 是英文？

因为它直接来自 `strftime("%A")`。这是一个可以坦诚承认的小瑕疵：如果面向中文用户更彻底，可以再做一层本地化映射。

### 文件：`core/logging.py`

#### Q1：为什么项目级 logger 要注入 `request_id`？

因为一次用户请求会跨 Supervisor、子代理、MCP、Web。没有 request_id，很难在日志里把一条链路串起来。request_id 是这个项目观测性最基础的一环。

#### Q2：`logger.propagate = False` 为什么重要？

它防止日志重复输出到父 logger，避免同一条日志在控制台和文件里被打多次。

#### Q3：为什么控制台 handler 是 INFO，文件 handler 是 DEBUG？

因为本地交互时控制台更适合看高层信息，而文件日志适合保留更细节的调试输出。这是典型的双通道日志分级。

#### Q4：这个文件里有没有小问题？

有，logger 名字写成了 `SmartVoage`，少了一个 `y`。这不影响功能，但属于命名瑕疵，面试官如果看得细，可以主动承认并说这是低风险可修复问题。

### 文件：`core/http.py`

#### Q1：这个中间件解决了什么问题？

它统一处理三件事：

- request_id 注入
- 未捕获异常兜底为标准 JSON
- HTTP 访问日志和耗时记录

#### Q2：为什么异常时返回 `{"detail": "internal_server_error", "request_id": ...}`？

因为前端和调用方不一定需要原始异常栈，但一定需要一个稳定错误码和 request_id 用于排查。这是典型的“对外简化，对内保留日志”的策略。

### 文件：`core/errors.py`

#### Q1：如果面试官问这个文件存在的价值，怎么答？

它主要承担“异常格式化和错误细节统一”的职责，让 MCP 或 agent 层在捕获异常后能用统一方式输出错误文本。它的价值在于降低不同模块之间错误文案风格不一致的问题。

---

## 五、`contracts/` 目录

### 文件：`contracts/agent_protocol.py`

#### Q1：为什么需要 `LocalAgentRequest` 和 `LocalAgentResponse`？

因为 Supervisor 和子代理之间本质上也是协议边界。即便它们都在本地进程内，也不应该裸传字符串或任意 dict。统一协议有利于测试、metrics 透传和后续重构。

#### Q2：`LocalAgentRequest.metrics` 为什么默认用 `create_metrics()`？

因为每条链路都应该天然可观测。让 metrics 成为协议的一部分，而不是可有可无的外部变量，能保证链路里任何一层都可以累加指标。

#### Q3：`LocalAgentResponse.state` 为什么只有 `completed / input_required / failed` 三种？

因为当前编排需要的状态语义就是这三类：

- 正常完成
- 需要用户补充或确认
- 系统失败

再细分会增加编排复杂度，而当前场景不需要更多状态类型。

### 文件：`contracts/structured_outputs.py`

#### Q1：为什么大量使用 Pydantic schema，而不是只让模型返回 JSON 字符串？

因为这个项目高度依赖结构化输出做编排。如果只是 JSON 字符串，解析和校验都要手写，容易出错；Pydantic 能把字段验证、默认值补全和后置业务校验集中起来。

#### Q2：`IntentRecognitionResult` 为什么有 `has_explicit_departure_city` 和 `needs_home_city_follow_up` 两个字段？

因为这两个字段服务于不同层面：

- `has_explicit_departure_city` 表示模型从语义上看到了显式出发地。
- `needs_home_city_follow_up` 是更直接面向编排决策的布尔结果。

Supervisor 后续还会结合车次号等规则再修正它。

#### Q3：`TicketQuerySpec.validate_payload()` 为什么要求“route + date_from 或 transport_no”二选一？

因为票务查询至少要能唯一定位一类票。只给出出发地不给日期，是模糊的；只给日期不给 route 也意义不大；而显式 transport_no 已经足够构成查询条件。

#### Q4：`OrderOperationExtractionResult.validate_payload()` 为什么这么严格？

因为订单链路是有副作用的，抽取结果不能模糊。比如：

- `change_order` 完整时必须至少有一个 new target
- complete 和 missing_fields 不能同时存在
- incomplete 时必须有 follow_up_message

这保证了工作流分支不会建立在模棱两可的抽取结果上。

### 文件：`contracts/order_action_tag.py`

#### Q1：为什么订单动作用文本标签而不是 Enum 字段直接传？

因为当前子代理接口是基于文本输入的，本质上还是“把信息嵌入自然语言通道里”。标签比普通自然语言更容易被正则提取，也能减少重复动作分类。

### 文件：`contracts/travel_read_tag.py`

#### Q1：为什么只读类型标签只区分 `weather / ticket / time`？

因为 TravelReadSubagent 当前只负责这三种只读语义。再细分成 `train / flight` 没必要，因为票务 plan 阶段还会进一步决定 type。

---

## 六、`llm/` 目录

### 文件：`llm/resilient_llm.py`

#### Q1：为什么要做 `ResilientModelInvoker`，而不是直接 everywhere 调 `model.invoke()`？

因为项目对模型调用的要求不只是“能调用”，还包括：

- 轻重模型路由
- fallback
- 重试
- metrics 统计
- 结构化输出校验

如果分散在各个 agent 里实现，逻辑会重复且难统一。

#### Q2：`primary_model_spec`、`light_model_spec`、`fallback_model_spec` 的设计意义是什么？

它们把“模型选择”从“模型实例”分离开了。spec 是配置层对象，model 是运行层对象。这样更容易做 signature 去重、fallback 判断和测试 mock。

#### Q3：`_iter_models()` 为什么要用 `_model_signature()` 去重？

因为 light / primary / fallback 可能实际上指向同一个模型配置。如果不去重，系统可能重复尝试同一个模型三次，浪费调用成本。

#### Q4：为什么结构化调用和文本调用分别有自己的重试计数？

因为它们失败特征不同。结构化输出更容易因为 schema 不合法而重试；文本输出更关注模型响应异常。分别配置能更细粒度控制成本和可靠性。

#### Q5：`invoke_structured()` 为什么要有 `validate_result`？

因为仅仅“模型返回了对象”还不够，项目还需要确认这个对象是可用的，比如有 `model_dump`。这是对结构化输出再加一层运行时校验。

#### Q6：为什么 `phase_name` 和 `task_key` 分开？

因为有时一个调用既需要给 metrics 记一个阶段名，又需要用另一个 key 决定是否走轻模型路由。默认两者可相同，但保留分离让扩展更灵活。

#### Q7：面试官如果问“fallback 和 light model 的顺序为什么是这样”怎么答？

顺序是：light -> primary -> fallback。因为：

- light model 是成本优化尝试，只在白名单阶段启用。
- primary 是主力模型。
- fallback 是兜底，不应优先于 primary。

这体现的是成本优先尝试、能力优先保底的策略。

### 文件：`llm/model_factory.py`

#### Q1：为什么需要 model factory？

因为项目支持 `openai_compatible` 和 `ollama` 两类 provider。把 provider 差异收敛在这里，可以避免上层业务代码知道太多底层模型初始化细节。

#### Q2：`build_structured_llm()` 为什么对 Ollama 用 `json_schema`，其他模型用 `function_calling + strict=True`？

因为不同 provider 的结构化输出能力和接口习惯不同。这里做的是 provider-aware 适配，而不是假设所有模型都支持同一种 structured output 方法。

#### Q3：`ORDER_AGENT_SYSTEM_PROMPT` 的作用是什么？

它约束工具型订单 agent 的行为边界：

- 必须认真提取参数
- 不能忽略“当前用户”
- 参数不足必须追问
- 不能自行编造参数

这实际上是对工具 agent 做最小必要约束。

#### Q4：`extract_text_from_agent_result()` 为什么要从最后一条消息往前找文本？

因为 agent 结果里可能包含工具消息、系统消息或复杂 content 列表。倒序查找最接近最终回答，也更适合从多消息结构里抽最终文本。

---

## 七、`infra/` 目录

### 文件：`infra/db.py`

#### Q1：为什么数据库连接只做了一个简单函数封装？

因为当前项目规模下，不需要完整 ORM 或连接池抽象。统一入口的主要价值是避免每处自己 hardcode 连接参数，同时让测试更容易 patch。

### 文件：`infra/cache.py`

#### Q1：为什么 Redis client 要在初始化时就 `ping()`？

因为缓存是可选优化，不是核心功能。启动时先探测可用性，如果失败就直接降级为 no-cache，比把异常留到第一次业务请求时爆出来更友好。

#### Q2：为什么 `RedisCacheClient` 失败时是 warning + 降级，而不是抛错？

因为缓存不是主功能依赖。系统即使没有 Redis，也应该还能完成查询，只是性能退化。这是典型的“缓存可失效、主链路不可失效”的设计。

### 文件：`infra/json_encoder.py`

#### Q1：为什么 MCP 需要自定义 JSON encoder？

因为 MySQL 查询结果里常见 `datetime/date/timedelta/Decimal` 这些 JSON 默认不支持的类型。如果不统一编码，MCP 响应会频繁失败。

### 文件：`infra/persistent_checkpointer.py`

#### Q1：为什么不直接用 LangGraph 自带 `InMemorySaver`？

因为 `InMemorySaver` 进程重启就丢状态，而订单 HITL 场景需要本地可恢复能力。这个类在内存 saver 基础上加了一层文件持久化，是对单机场景的轻量增强。

#### Q2：为什么采用 `pickle`？

因为 checkpoint 数据结构复杂，直接 JSON 化成本高且易丢信息。对本地单机演示场景来说，pickle 实现成本最低。它的边界是：不适合跨版本、跨语言和不可信输入场景。

#### Q3：`_persist()` 为什么先写临时文件再 replace？

这是经典的原子写策略。先写 `.tmp`，成功后再替换正式文件，可以降低写到一半宕机导致 checkpoint 文件损坏的风险。

#### Q4：为什么这里要用 `RLock`？

因为 put、put_writes、delete、prune 等操作都可能涉及同一份内存和文件写入。加锁是为了避免并发下出现状态和文件不同步。

#### Q5：`copy_thread()` 的业务意义是什么？

它允许把某个 thread 的 checkpoint 状态复制到新 thread，用于测试、恢复或后续扩展。它不仅复制 storage，也复制 writes 和 blobs，保证恢复上下文完整。

---

## 八、`observability/` 目录

### 文件：`observability/metrics.py`

#### Q1：为什么 metrics 结构故意设计成简单 dict？

因为当前目标是低成本、跨层可透传。dict 足够轻、可 JSON 化、可直接合并，也适合放进 `LocalAgentRequest` / `LocalAgentResponse`。

#### Q2：`merge_metrics()` 为什么只做加法合并，不做更复杂统计？

因为当前系统更关心链路总耗时和调用次数，而不是在线精确分位数。复杂统计留给 LangSmith 或后续观测系统处理。

#### Q3：`track_phase()` 的意义是什么？

它是一个上下文管理器，让任何逻辑块都能低侵入地记 phase timing。相比手动 start/end 计算，它更统一，也更不容易漏写。

### 文件：`observability/request_context.py`

#### Q1：为什么 request id 用 `ContextVar` 而不是全局变量？

因为 Web 场景和异步调用里，全局变量会串请求。`ContextVar` 能让每条调用链有自己的 request id，上下文切换时不互相污染。

---

## 九、`mcp_server/` 目录

### 文件：`mcp_server/mcp_travel_read_server.py`

#### Q1：TravelRead MCP 的边界是什么？

它只负责只读工具：时间、天气、票务，以及 Redis 缓存和 MySQL 查询封装。它不做编排，不做意图识别，也不做自然语言总结。

#### Q2：为什么 `execute_select()` 用 SQL 哈希做缓存键？

因为对于只读查询来说，“同一条 SQL”本质上就代表同一查询语义。用 SQL 哈希做 key 简单直接，而且不用再设计一套额外 query canonicalization。

#### Q3：为什么缓存里存的是完整 payload，而不是只存 `data`？

因为缓存命中和未命中都希望返回同样结构，只是 `meta.cache_status` 不同。缓存完整 payload 可以减少命中路径和 miss 路径之间的结构差异。

#### Q4：为什么时间缓存用 `bucket = int(time.time() // ttl)`？

因为时间查询不是按 SQL 查的，但仍然适合短 TTL 缓存。用时间桶做 key，相当于把“同一个 TTL 窗口内的当前时间查询”映射到同一个缓存项。

#### Q5：为什么 `query_weather` 和 `query_tickets` 返回的 `meta.cache_status` 可能是 `hit / miss / bypass`？

因为缓存有三种状态：

- `hit`：命中 Redis
- `miss`：没命中，但已查库并写回
- `bypass`：缓存不可用或异常，直接走数据库

这比简单 true/false 更能反映链路行为。

### 文件：`mcp_server/mcp_order_server.py`

#### Q1：为什么 Order MCP 没有做缓存？

因为订单相关操作强依赖最新状态，而且大部分工具有副作用。缓存会直接引入一致性风险。

#### Q2：为什么 `order_train()` / `order_flight()` 最终都收敛到 `_create_order()`？

因为下单流程在两类票务之间高度相似：查票、查重、扣库存、写订单。把共性收敛可以减少重复逻辑，同时保留不同表名和字段名的参数化差异。

#### Q3：`_create_order()` 里为什么先查重，再查库存，再写订单？

更准确地说是：查票拿到目标记录后，先检查重复订单，再检查库存，再扣库存并插单。查重在前可以避免对库存做无意义修改。

#### Q4：为什么要用事务 + `FOR UPDATE`？

因为下单、退票、改签都涉及库存和订单状态，一定要防止并发下出现超卖或状态不一致。`FOR UPDATE` 用于锁定目标票务记录或订单记录，事务用于把多步操作包成原子单元。

#### Q5：为什么 `_find_single_booked_order()` 会在匹配多条时返回提示文本，而不是自动选第一条？

因为退票和改签是高风险副作用，不允许在歧义情况下擅自决策。多条匹配时必须要求用户进一步明确。

#### Q6：改签为什么先恢复原库存，再扣减新库存？

因为改签语义上是“释放旧票，预定新票”。先恢复旧库存、再扣新库存，更符合业务语义，也便于在事务里回滚。

#### Q7：为什么重复订单保护不仅下单有，改签也有？

因为改签到目标票务后，本质上也是在产生一个新的 booked 订单。如果用户本来就有同目标订单，就会产生重复业务状态，所以必须拦截。

#### Q8：`_phone_for_username()` 为什么存在？

因为如果用户不存在，系统会自动建用户记录，而用户表又需要手机号。默认用户有固定手机号，其他用户名就根据数字后缀构造一个演示手机号。这是为了让本地环境不依赖外部用户系统。

---

## 十、`skills/` 目录

### 文件：`skills/runtime.py`

#### Q1：Skill Runtime 的核心价值是什么？

它把 Prompt 管理从业务代码里抽离出来。业务代码只说“我要 role=order, capability=operation_extraction 的 Prompt”，Runtime 决定加载哪个 skill、哪个 asset、哪些 reference。

#### Q2：`SkillManifest` 里为什么要区分 `entry_assets`、`default_references` 和 `conditional_references`？

因为 Prompt 由三部分组成：

- 入口模板：主 Prompt 骨架
- 默认引用：无条件总是加载的规则
- 条件引用：基于 flags 动态附加的规则

这能让 Prompt 资产既结构化又可扩展。

#### Q3：`SkillRegistry.find()` 为什么在命中多个 skill 时直接报错？

因为 role + capability 映射必须唯一，否则运行时就会出现歧义。这里选择 fail fast，是为了防止 Prompt 资产冲突悄悄进入线上行为。

### 文件：`skills/builder.py`

#### Q1：`[[include:path]]` 机制的意义是什么？

它让 skill asset 可以模块化组合，而不是每个 Prompt 都写成一整块大文档。这样 common rule 可以复用，改动也更集中。

#### Q2：为什么 builder 要显式阻止 include 路径逃逸 skill root？

为了安全和边界清晰。Prompt include 只能引用当前 skill 目录里的文件，不应该读到项目其他任意路径。

#### Q3：为什么要检测 include cycle？

因为 Prompt 资产也是依赖图，循环 include 会导致无限递归。这个校验属于非常必要的运行时保护。

### 文件：`skills/intent-routing/SKILL.md`

#### Q1：这个 skill 的职责边界是什么？

只负责语义路由：意图识别、多意图识别、query rewrite 辅助、transport_decision 前置聚焦。它不负责状态迁移、工具调用和业务执行。

#### 相关 asset / reference 可能被问什么？

- `assets/intent_recognize.md`：主意图识别模板是什么。
- `references/intent_rules.md`：如何区分 weather/time/flight/train/order 等意图。
- `references/follow_up_rules.md`：什么情况下模型应返回 follow-up。
- `references/query_rewrite_context.md`：多轮时如何借助上下文改写 query。
- `references/relative_date_focus.md`：为什么相对日期会影响路由。
- `references/transport_decision_focus.md`：为什么“高铁还是飞机”类表述要优先识别成复合决策。
- `references/output_contract.md`：输出字段为什么要稳定。

### 文件：`skills/travel-read/SKILL.md`

#### Q1：为什么把 `read_kind`、`weather_plan`、`ticket_plan` 放进同一个 skill？

因为它们都服务于同一条只读语义链路，具有较强内聚性。拆太细会增加管理成本。

#### 相关 asset / reference 可能被问什么？

- `assets/read_kind.md`：如何区分 time / weather / ticket。
- `assets/weather_plan.md`：天气查询计划需要输出哪些字段。
- `assets/ticket_plan.md`：票务计划为什么必须输出 type、route/date 或 transport_no。
- `references/read_kind_rules.md`：模糊问句如何判类。
- `references/weather_plan_rules.md`：天气缺参时如何追问。
- `references/ticket_plan_rules.md`：票务缺参时如何追问。
- `references/relative_date_rules.md`：明天/后天如何映射成绝对日期。

### 文件：`skills/transport-decision/SKILL.md`

#### Q1：这个 skill 为什么不包含下单规则？

因为它只负责“建议规划”，不负责实际执行。是否下单以及怎么下单，是 Supervisor 编排和 OrderSubagent 的职责。

#### 相关 asset / reference 可能被问什么？

- `assets/plan.md`：为什么要输出 `transport_mode`、`recommendation_reason`、`ticket_plan`、`should_order`。
- `references/decision_rules.md`：高铁和飞机怎么比较。
- `references/relative_date_rules.md`：相对日期对 ticket plan 的影响。
- `references/weather_degradation_rules.md`：天气服务异常时为什么走保守策略。
- `references/weather_no_data_rules.md`：为什么缺天气数据不等于系统失败。

### 文件：`skills/order-operation/SKILL.md`

#### Q1：为什么订单 skill 要拆成四个 capability？

因为订单链路的语义任务本来就不同：动作分类、日期归一化、参数抽取、审批解析。把它们混成一个 Prompt 反而不稳定。

#### 相关 asset / reference 可能被问什么？

- `assets/action.md`：订单动作怎么分类。
- `assets/date_resolution.md`：为什么订单查询日期也要单独归一化。
- `assets/operation_extraction.md`：退票和改签需要抽哪些字段。
- `assets/review_decision.md`：审批回复如何解析 yes/no/模糊表达。
- `references/action_rules.md`：口语表达如何映射成 create/query/cancel/change。
- `references/date_resolution_rules.md`：相对日期如何转绝对日期。
- `references/extraction_rules.md`：抽取时为什么严格禁止编造。
- `references/pending_context_rules.md`：补槽时如何继承上一轮状态。
- `references/change_order_rules.md`：改签为什么必须有 new target。
- `references/review_rules.md`：审批提示和模糊回复处理原则。

---

## 十一、`langsmith_eval/` 目录

### 文件：`langsmith_eval/cases.json`

#### Q1：为什么回归样例要放在独立 JSON 里？

因为它本质上是“数据集”，不是代码。把 case 和 runner 分离后，可以单独扩样、替换数据集、做副作用断言，而不改执行逻辑。

#### Q2：这些 case 和普通单测最大的区别是什么？

它们更像产品级回归样例：

- 输入是多轮自然语言 turns
- 输出不仅看文本，还看意图、路由、pending context、DB 副作用
- 可以带 setup_profile 和 auto_approve_hitl

这已经不是简单的函数级测试，而是系统级回归。

### 文件：`langsmith_eval/run_langsmith_eval.py`

#### Q1：为什么 LangSmith runner 里还要自己 reset 数据库？

因为有副作用 case。如果不在每个 case 前重置数据库，一个 case 的下单或退票会污染下一个 case，回归就不可信。

#### Q2：为什么 runner 里要区分 `db_metrics_before` 和 `db_metrics_after`？

因为项目不仅关心回复文本，更关心副作用是否真的发生。例如退票成功不仅要返回“退票成功”，还要验证订单状态变化和库存回补。

#### Q3：为什么 `response_semantic_match` 还要加一层 precheck？

因为像 `transport_decision` 这类固定格式回复，如果已经包含“天气判断 / 出行建议 / 票务结果”三段，就可以确定性判通过，没必要每次都调用 judge 模型。

#### Q4：`run_case()` 里为什么会在 case 内自动插入 `yes`？

因为有些 case 设置了 `auto_approve_hitl=true`，这样 runner 可以自动完成审批链路，便于批量跑副作用回归。

---

## 十二、`test/` 目录

### 文件：`test/test_supervisor.py`

#### 可能被问什么？

- Supervisor 基础路由是否稳定。
- intent follow-up 和订单 pending context 会不会互相吞掉。
- 这类测试为什么用 mock 而不是完整 E2E。

### 文件：`test/test_supervisor_helpers.py`

#### 可能被问什么？

- `transport_decision` finalize 逻辑怎么单测。
- degraded prefix、pending context、out_of_scope normalization、home city follow-up 为什么值得单独测。

### 文件：`test/test_supervisor_e2e.py`

#### 可能被问什么？

- 为什么这个文件需要真实模型。
- 为什么它不适合成为每次本地快速回归的默认集。

### 文件：`test/test_travel_read_helpers.py`

#### 可能被问什么？

- deterministic formatter 为什么值得测。
- cache metrics 和 tool meta 为什么需要单测。

### 文件：`test/test_travel_read_cache.py`

#### 可能被问什么？

- 缓存 hit/miss 行为怎么验证。
- 为什么缓存测试不一定要真连 Redis。

### 文件：`test/test_travel_read_mcp_server.py`

#### 可能被问什么？

- MCP 层为什么要单独测。
- 查询工具和 JSON 返回结构怎么锁定。

### 文件：`test/test_order_helpers.py`

#### 可能被问什么？

- 日期快速归一化、pending context 识别、审批 pending 判断为什么要脱离工作流单测。

### 文件：`test/test_order_workflow_helpers.py`

#### 可能被问什么？

- route_action、route_after_review、review payload、create_order_node 为什么拆 helper 级测试。

### 文件：`test/test_order_agent_server.py`

#### 可能被问什么？

- OrderSubagent 在 HITL interrupt 时为什么返回 `input_required`。
- 为什么 agent 层协议比 MCP 层更值得测状态。

### 文件：`test/test_order_mcp_server.py`

#### 可能被问什么？

- 订单工具层的数据库事务行为如何验证。
- 为什么副作用应该在工具层和 LangSmith 层双重覆盖。

### 文件：`test/test_metrics_helpers.py`

#### 可能被问什么？

- metrics merge / clone / phase timing 为什么要做纯函数测试。

### 文件：`test/test_persistent_checkpointer.py`

#### 可能被问什么？

- checkpoint 持久化、delete、copy、prune 如何保证本地恢复可靠。

### 文件：`test/test_task_model_routing.py`

#### 可能被问什么？

- light / primary / fallback 的顺序怎么验证。
- phase 白名单为什么需要单测。

### 文件：`test/test_resilient_llm.py`

#### 可能被问什么？

- 结构化重试和 invalid structured result 怎么验证。

### 文件：`test/test_prompt_skill_registry.py`

#### 可能被问什么？

- role + capability + flags 是否真的加载到了正确 skill。
- 为什么 Skill Runtime 必须有 registry 级测试。

### 文件：`test/test_performance_optimizations.py`

#### 可能被问什么？

- 你说自己做了优化，怎么证明不是口头说说。
- 哪些优化点已经被测试锁定，比如显式车次清除幻觉日期、summary deterministic 化、transport_decision 只调用一次 decision planner。

---

## 十三、面试官最爱细抠的配置项和函数

### 配置项追问模板

#### Q1：`SMARTVOYAGE_NOW_OVERRIDE` 有什么价值？

它是测试稳定性和回归可重复性的关键配置。没有它，“明天”“后天”这类 query 的结果会随时间变化，E2E 和 LangSmith case 都会变得不稳定。

#### Q2：`SMARTVOYAGE_LIGHT_MODEL_PHASES` 为什么默认只放 `intent_recognition,weather_plan,ticket_plan,order_date_resolution`？

因为这些阶段输入短、输出结构固定、风险较低、易校验；而 `decision_plan`、订单抽取、审批决策失败成本更高，不适合轻易下沉。

#### Q3：`SMARTVOYAGE_CACHE_TIME_TTL_SECONDS=10` 为什么比天气和票务短很多？

因为“当前时间”天然变化快，缓存太久会显得不真实；天气和票务是读取类业务数据，允许更长 TTL。

### 单行代码级追问模板

#### Q1：为什么 `supervisor.py` 里 `_build_agent_chat_history()` 对 TravelRead 和 Order 用不同历史策略？

因为 TravelRead 更接近单轮只读任务，过多历史会增加噪声；Order 更依赖最近几轮上下文，所以保留最近几行对话更合理。

#### Q2：为什么 `order.py` 里审批拒绝时文案是“未执行实际下单、退票或改签”？

因为这个文案强调了系统的安全性边界：用户拒绝审批后，系统不会产生任何副作用。它不仅是文案，也是产品语义声明。

#### Q3：为什么 `mcp_order_server.py` 在查不到票时直接返回文本，而不是抛异常？

因为“查不到”是业务结果，不是系统错误。返回业务语义文本更利于上层 agent 决策是否继续追问或终止。

---

## 十四、如果面试官问“你有哪些地方做得还不够”

推荐诚实回答：

- Web 会话当前在内存里，不支持多实例共享。
- TravelRead 的 SQL 仍然是字符串拼接，生产化应进一步参数化。
- logger 名字有个小拼写问题 `SmartVoage`。
- `run_all.py` 和 Web API 目前还缺少更系统的自动化测试。
- checkpoint 目前用本地 pickle 文件，适合单机，不适合分布式生产环境。

这种回答的关键不是自黑，而是说明你知道边界、知道为什么当前这么做、也知道怎么演进。

---

## 十五、最终压轴总结句

### 总结句 1

这个项目不是“会聊天的票务助手”，而是一个把查询、决策、执行、审批、恢复、优化和回归验证串起来的窄领域事务型 Agent 系统。

### 总结句 2

我在这个项目里最强调的是显式边界：Supervisor 负责编排，子代理负责域内语义处理，MCP 负责工具边界，LangGraph 负责事务状态，Skill Runtime 负责 Prompt 资产，LangSmith 负责回归验证。

### 总结句 3

如果面试官继续深挖，我不仅能讲架构，还能讲到具体文件、具体函数、具体配置项和具体边界条件，这也是这个题库存在的意义。
