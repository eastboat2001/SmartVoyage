# 指标基线

## 快照

- 日期：`2026-03-30`
- 实验名称：`smartvoyage-regression-20260330-183006-9c1ce2fd`
- 数据源 CSV：`langsmith_eval/log/smartvoyage-regression-20260330-183006-9c1ce2fd.csv`
- 用例数：`30`
- 结果：`30/30 passed`

## 回归通过率

| 指标 | 通过数 | 总数 | 通过率 |
| --- | ---: | ---: | ---: |
| `intent_match` | 30 | 30 | 100% |
| `route_match` | 30 | 30 | 100% |
| `response_semantic_match` | 30 | 30 | 100% |
| `pending_context_match` | 30 | 30 | 100% |
| `db_state_match` | 30 | 30 | 100% |

## 时延

| 指标 | 当前值 | 备注 |
| --- | ---: | --- |
| 最小值 | 1.39s | `out_of_scope` 单轮拒答 |
| P50 | 4.30s | 查询主链路整体较稳定 |
| P95 | 17.39s | 主要被订单补问 / 改签链路拉高 |
| P99 | 18.40s | 最慢 case 为改签副作用链路 |
| 最大值 | 18.40s | `base_025_order_change_train_with_side_effect` |
| 平均值 | 6.89s | 全量 30 条样例平均 |

## Token 使用

| 指标 | 当前值 | 备注 |
| --- | ---: | --- |
| 总 Tokens | 84,927 | 30 条回归总消耗 |
| 平均 Tokens / Case | 2830.9 | 全量平均 |

当前导出的 `total_cost` 仍为空，因此暂无成本统计。

## 分意图表现

| 意图 | 用例数 | 平均时延 | 平均 Tokens | 用例范围 |
| --- | ---: | ---: | ---: | --- |
| `cancel_order` | 4 | 12.59s | 4203.0 | `base_017` / `024` / `026` / `030` |
| `change_order` | 3 | 14.95s | 4588.0 | `base_018` / `025` / `027` |
| `flight` | 3 | 4.05s | 2641.3 | `base_007` / `008` / `012` |
| `my_orders` | 5 | 2.42s | 1497.8 | `base_014` / `015` / `016` / `019` / `021` |
| `order` | 3 | 9.39s | 3398.0 | `base_022` / `023` / `028` |
| `out_of_scope` | 1 | 1.39s | 1496.0 | `base_029` |
| `time` | 2 | 2.58s | 1512.5 | `base_001` / `002` |
| `train` | 3 | 4.41s | 2652.7 | `base_005` / `006` / `011` |
| `transport_decision` | 2 | 11.64s | 3646.5 | `base_009` / `013` |
| `weather` | 4 | 3.96s | 2243.0 | `base_003` / `004` / `010` / `020` |

## 最慢用例

| 用例 | 意图 | 时延 | Tokens | 备注 |
| --- | --- | ---: | ---: | --- |
| `base_025_order_change_train_with_side_effect` | `change_order` | 18.40s | 3,671 | 改签信息抽取最重，随后执行真实改签工具 |
| `base_026_cancel_order_follow_up_resume_success` | `cancel_order` | 17.39s | 6,813 | 两轮补问恢复，文本上下文较长 |
| `base_027_change_order_follow_up_resume_success` | `change_order` | 17.37s | 7,059 | 改签补问恢复，Token 消耗最高 |
| `base_030_cancel_order_review_reject_no_side_effect` | `cancel_order` | 14.60s | 3,527 | 含审批解析，但最终不落库 |
| `base_009_transport_decision_read_only` | `transport_decision` | 12.39s | 3,570 | 同时走天气、决策与票务查询 |

## 阶段级指标

| 典型链路 | 主要慢阶段 |
| --- | --- |
| `transport_decision` | `decision_plan` ~6.2-8.0s，随后是 `intent_recognition`、`query_weather`、`query_tickets` |
| `change_order` | `order_operation_extract_change_order` ~9.2s，是当前最主要瓶颈 |
| `cancel_order` | `order_operation_extract_cancel_order` ~5.8s，工具执行本身很快 |
| `create_order` | `ticket_plan` ~1.0-1.3s，`query_tickets` ~0.9-1.1s，真实下单工具约 46-48ms |
| `my_orders` / `time` / `weather` / `train` / `flight` | 只读链路整体较稳，主要耗时仍集中在意图识别或查询计划生成 |

## 目标达成情况

| 目标 | 阈值 | 当前结果 | 状态 |
| --- | ---: | ---: | --- |
| 功能回归 | 30/30 passed | 30/30 passed | 达成 |
| 所有 evaluator 全绿 | 100% | 100% | 达成 |
| 总体 P50 | <= 5s | 4.30s | 达成 |
| `transport_decision` 平均时延 | <= 12s | 11.64s | 达成 |
| `my_orders` 平均时延 | <= 3s | 2.42s | 达成 |
| `create_order` 主链路打通 | 必须打通 | 已打通，并通过副作用断言 | 达成 |

## 结论

- 当前回归集已经从 `19` 条扩展到 `30` 条，并全部通过，覆盖查询、决策、审批恢复、订单副作用与越界请求等高价值场景。
- 这一轮修复不仅解决了功能问题，也修复了评测基础设施问题：包括 `create_order` 链路中的 event loop 冲突、`out_of_scope` 回复文案不清晰，以及 LangSmith case 之间的数据库状态污染。
- 只读链路已经比较稳定，`time` / `my_orders` / `weather` / `ticket query` 的延迟处于可接受范围；当前主要性能瓶颈集中在 `transport_decision` 的规划阶段，以及 `cancel_order` / `change_order` 的结构化信息抽取阶段。
- 从工程证明角度，这一版已经具备较强的简历说服力：不仅有多 Agent 架构和事务状态机，还有分层测试、数据库副作用断言、LangSmith 回归与问题闭环修复记录。
