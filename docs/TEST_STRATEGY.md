# SmartVoyage 测试设计

## 1. 当前基线

截至当前仓库状态，测试基线已经不是旧版本文档里写的规模，而是：

- `18` 个 Python 测试文件
- `71` 个 Python 测试函数
- `30` 条 LangSmith 回归 case
- 覆盖范围已经从“只读查询主链路”扩展到“订单副作用 + HITL + pending context + 越界请求”

换句话说，这个仓库现在已经不是“有几条 smoke test 的 demo”，而是有分层测试骨架的 Agent 项目。

## 2. 当前测试分层

从仓库现状看，测试大致可以分成四层。

| 层级 | 代表文件 | 当前作用 |
| --- | --- | --- |
| 纯逻辑/帮助函数 | `test_travel_read_helpers.py`、`test_order_helpers.py`、`test_supervisor_helpers.py`、`test_metrics_helpers.py` | 锁定 deterministic formatting、标签处理、日期归一化、pending context、metrics 合并等纯逻辑 |
| 组件/工作流 | `test_order_workflow_helpers.py`、`test_supervisor.py`、`test_order_agent_server.py`、`test_travel_decision_agent_server.py` | 验证子代理协议、Supervisor 路由、review payload、工作流分支和 transport_decision 收尾 |
| 工具/MCP/基础设施 | `test_travel_read_mcp_server.py`、`test_order_mcp_server.py`、`test_travel_read_cache.py`、`test_persistent_checkpointer.py` | 验证 MCP 边界、缓存行为、持久化恢复 |
| 端到端与回归 | `test_supervisor_e2e.py`、`langsmith_eval/cases.json` | 验证真实对话主链路、订单副作用、审批恢复和越界请求 |

## 3. 当前已经覆盖到什么程度

### 3.1 代码级自动化测试已覆盖的重点

从现有测试文件可以明确看出，下面这些内容已经被代码级测试覆盖：

- TravelRead 的 weather / ticket deterministic summary
- 显式车次或航班号触发时的 query plan 归一化
- TravelRead cache hit / miss 计数
- Supervisor 的基础路由和响应拼装
- `transport_decision` 收尾阶段的降级前缀、pending context 保留
- `out_of_scope` 响应归一化
- home city follow-up 的触发与跳过
- Order action classify、日期归一化、pending context、审批识别
- Order 工作流里的路由函数、review payload、直接下单节点
- Persistent checkpointer 的持久化与恢复
- 按 phase 的 light model 路由与 fallback
- `transport_decision` 去除冗余 LLM 调用后的关键优化点

### 3.2 LangSmith 回归当前已覆盖的重点

`langsmith_eval/cases.json` 当前已经有 `30` 条样例，覆盖范围包括：

- 时间查询
- 天气单天与范围查询
- 高铁票 / 机票查询
- 相对日期查询
- `transport_decision` 只读建议链路
- 空订单查询
- 退票 / 改签缺参补问
- unknown city 天气无数据
- 已有订单查询
- 下单成功与重复订单保护
- 退票成功
- 改签成功
- 补问后恢复成功
- HITL 模糊回复后再次确认
- `out_of_scope` 越界请求
- 审批拒绝且无数据库副作用

这一点很重要，因为它意味着现在的 E2E 已经覆盖到了真正会被面试官追问的事务链路，而不只是“查天气、查票”这种轻量场景。

## 4. 面试时可以怎么讲当前测试体系

比较稳的说法不是“我写了很多测试”，而是：

`这个项目的测试是分层设计的。底层用单测锁定 deterministic formatter、字段抽取、pending context、模型路由和 metrics；中间层用组件测试验证 Supervisor、子代理和 LangGraph 工作流分支；上层再用 LangSmith 回归集覆盖查询、决策、审批恢复和订单副作用主链路。`

这个说法和当前代码是一致的，不会被细问时戳穿。

## 5. 当前测试体系的强项

### 5.1 已经具备“面试可讲”的几个点

- 不是只有 happy path，已经覆盖了无数据、重复订单、审批拒绝、模糊审批回复等分支。
- 不是只做文本断言，LangSmith case 已经包含数据库副作用断言。
- 不只是测 Agent 最终回复，也测了 helper、workflow route、cache 和 checkpointer。
- 性能优化不是裸改，`test_performance_optimizations.py` 会锁定几个关键优化行为。

### 5.2 这套测试最有说服力的地方

最有说服力的不是数量本身，而是“测试对象和风险点是对应的”：

- 只读链路：测 summary、cache、query plan
- 编排链路：测 route、pending context、response finalize
- 事务链路：测 review、resume、副作用断言
- 模型策略：测 phase routing 和 fallback

这说明测试不是堆样例，而是围绕架构风险点设计的。

## 6. 当前仍然存在的缺口

虽然测试基础已经明显比旧文档描述的更完整，但仍然有几块值得继续补：

- `web_app.py` 目前缺少专门的接口测试和会话状态测试
- `run_all.py` 缺少启动器级别的进程管理测试
- LangSmith 回归里目前没有覆盖 `transport_decision + 自动下单 + 审批通过` 的整链路 case
- 天气服务真正异常时的 Supervisor 降级路径，当前更多是 helper 级和组件级覆盖
- 多用户并发或跨会话隔离，目前没有专门测试
- Redis 不可用时的全链路降级，只在基础设施层有隐含覆盖，缺少更高层验证

这些缺口不会推翻当前测试体系，但如果你后面要继续为面试做准备，优先级可以放在这里。

## 7. 下一步建议补哪些测试

如果目标是“继续增强面试说服力”，建议优先补这几类：

1. `transport_decision` 自动下单成功链路的 LangSmith case。
2. Web API 的 `/api/chat`、`/api/reset`、HITL pending 状态测试。
3. Redis 不可用时 TravelRead 仍能正常查询的组件测试。
4. 天气服务报错时 Supervisor 保守降级的端到端测试。
5. 多轮会话里 pending context 被新意图清空或继承的边界测试。

## 8. 面试时推荐强调的测试表达

推荐说法：

`我没有把测试只做成几条自然语言样例，而是按风险分层。纯逻辑和格式化用单测锁定，Supervisor 和 LangGraph 分支用组件测试覆盖，MCP 和订单副作用用集成断言验证，最后再用 30 条 LangSmith 回归样例做端到端兜底。这样我在做缓存、轻模型路由和 transport_decision 优化时，能比较放心地重构。`

## 9. 一句话总结

当前 SmartVoyage 的测试体系已经从“有一些测试”升级成“围绕架构风险点设计的分层测试体系”，这套说法和仓库里的真实测试文件、真实回归 case、真实副作用断言是对得上的。
