# 指标基线

## 快照

- 日期：`2026-03-24`
- 实验名称：`smartvoyage-regression-20260324-164757-8c7095cb`
- 数据源 CSV：`C:\Users\eastboat\Downloads\smartvoyage-regression-20260324-164757-8c7095cb.csv`
- 用例数：`19`
- 结果：`19/19 passed`

## 回归通过率

| 指标 | 通过数 | 总数 | 通过率 |
| --- | ---: | ---: | ---: |
| `intent_match` | 19 | 19 | 100% |
| `route_match` | 19 | 19 | 100% |
| `response_semantic_match` | 19 | 19 | 100% |
| `pending_context_match` | 19 | 19 | 100% |
| `db_state_match` | 19 | 19 | 100% |

## 时延

| 指标 | 当前值 | 相比 `2026-03-23` |
| --- | ---: | ---: |
| 最小值 | 1.68s | +0.44s |
| P50 | 3.57s | -1.72s |
| P95 | 8.33s | -9.03s |
| P99 | 11.04s | -14.33s |
| 最大值 | 11.72s | -15.65s |
| 平均值 | 3.90s | -2.71s |

## Token 使用

| 指标 | 当前值 | 相比 `2026-03-23` |
| --- | ---: | ---: |
| 总 Tokens | 47,165 | -11,197 |
| 平均 Tokens / Case | 2482.4 | -589.3 |
| 单 Case 最大 Tokens | 5,931 | -2,163 |

当前导出的 `total_cost` 仍为空，因此暂无成本统计。

## 分意图表现

| 意图 | 用例数 | 平均时延 | P50 时延 | 平均 Tokens | 平均 LLM 调用数 | 平均工具调用数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel_order` | 1 | 4.68s | 4.68s | 2817.0 | 2.00 | 0.00 |
| `change_order` | 1 | 5.62s | 5.62s | 2945.0 | 2.00 | 0.00 |
| `flight` | 3 | 3.86s | 3.90s | 2670.3 | 2.00 | 1.00 |
| `my_orders` | 4 | 1.78s | 1.78s | 1515.0 | 1.00 | 1.00 |
| `time` | 2 | 1.69s | 1.69s | 1526.5 | 1.00 | 1.00 |
| `train` | 3 | 3.80s | 3.96s | 2673.3 | 2.00 | 1.00 |
| `transport_decision` | 2 | 9.83s | 9.83s | 4702.5 | 2.50 | 1.50 |
| `weather` | 3 | 3.55s | 3.44s | 2284.7 | 2.00 | 1.00 |

## 最慢用例

| 用例 | 意图 | 时延 | Tokens | 备注 |
| --- | --- | ---: | ---: | --- |
| `base_013_transport_decision_tomorrow_read_only` | `transport_decision` | 11.72s | 5,931 | 主要耗时在 `decision_plan` |
| `base_009_transport_decision_read_only` | `transport_decision` | 7.95s | 3,474 | 同时走天气与票务查询 |
| `base_018_change_order_follow_up` | `change_order` | 5.62s | 2,945 | 主要耗时在改签信息抽取 |
| `base_017_cancel_order_follow_up` | `cancel_order` | 4.68s | 2,817 | 主要耗时在退票信息抽取 |
| `base_007_flight_ticket_query_route` | `flight` | 4.11s | 2,653 | 查询链路已较稳定 |

## 阶段级指标

| 意图 | 主要慢阶段 |
| --- | --- |
| `transport_decision` | `decision_plan` ~3145.1ms，`intent_recognition` ~2202.9ms，`weather_plan` ~1522.7ms |
| `weather` | `intent_recognition` ~1791.7ms，`weather_plan` ~1475.1ms |
| `train` | `ticket_plan` ~1970.9ms，`intent_recognition` ~1564.1ms |
| `flight` | `ticket_plan` ~2023.5ms，`intent_recognition` ~1573.2ms |
| `change_order` | `order_operation_extract_change_order` ~3610.3ms |
| `cancel_order` | `order_operation_extract_cancel_order` ~3157.3ms |
| `my_orders` / `time` | 工具调用很快，主要耗时仍在 `intent_recognition` |

## 目标达成情况

| 目标 | 阈值 | 当前结果 | 状态 |
| --- | ---: | ---: | --- |
| 总体 P50 | <= 4.5s | 3.57s | 达成 |
| 总体 P95 | <= 12s | 8.33s | 达成 |
| `transport_decision` 平均时延 | <= 10s | 9.83s | 达成 |
| `transport_decision` 平均 Tokens | < 4500 | 4702.5 | 接近目标 |
| 功能回归 | 19/19 passed | 19/19 passed | 达成 |

## 结论

- 这轮修复后，`base_006_train_ticket_query_by_train_no` 已恢复，整套基线重新达到 `19/19 passed`。
- 第一轮性能优化是有效的。和 `2026-03-23` 基线相比，总体 `P50` 从 `5.29s` 降到 `3.57s`，`P95` 从 `17.36s` 降到 `8.33s`，平均时延从 `6.61s` 降到 `3.90s`。
- `transport_decision` 仍然是最慢链路，但已经从上一轮的 `21.81s` 显著下降到 `9.83s`，说明“减少冗余 LLM 调用 + deterministic summary + 只读缓存”这一轮方向是对的。
- 目前 summary 阶段几乎不再构成瓶颈，剩余主要耗时已经集中到 `intent_recognition`、`decision_plan`、`weather_plan`、`ticket_plan`，以及订单链路里的操作信息抽取。
- 下一轮如果还要继续压时延，优先级应放在“规划类调用进一步瘦身或降级到更轻模型”，而不是继续优化数据库或 MCP 层。
