# Metrics Baseline

## Snapshot

- Date: `2026-03-23`
- Source experiment: `smartvoyage-regression-20260323-105202-d5cd15cd`
- Source CSV: `C:\Users\eastboat\Downloads\smartvoyage-regression-20260323-105202-d5cd15cd.csv`
- Cases: `19`
- Result: `19/19 passed`

## Regression Metrics

| Metric | Passed | Total | Pass Rate |
| --- | ---: | ---: | ---: |
| `intent_match` | 19 | 19 | 100% |
| `route_match` | 19 | 19 | 100% |
| `response_semantic_match` | 19 | 19 | 100% |
| `pending_context_match` | 19 | 19 | 100% |
| `db_state_match` | 19 | 19 | 100% |

## Latency

| Metric | Value |
| --- | ---: |
| Min | 1.24s |
| P50 | 5.29s |
| P95 | 17.36s |
| P99 | 25.37s |
| Max | 27.37s |
| Mean | 6.61s |

## Token Usage

| Metric | Value |
| --- | ---: |
| Total Tokens | 58,362 |
| Average Tokens / Case | 3071.7 |
| Max Tokens / Case | 8,094 |

`total_cost` is empty in this export, so cost metrics are not available in the current baseline.

## By Intent

| Intent | Cases | Avg Latency | P50 Latency | Avg Tokens |
| --- | ---: | ---: | ---: | ---: |
| `cancel_order` | 1 | 5.29s | 5.29s | 2962.0 |
| `change_order` | 1 | 5.90s | 5.90s | 3069.0 |
| `flight` | 3 | 6.68s | 7.24s | 3424.7 |
| `my_orders` | 4 | 2.72s | 2.70s | 2064.0 |
| `time` | 2 | 1.26s | 1.26s | 1220.5 |
| `train` | 3 | 6.29s | 7.19s | 3412.0 |
| `transport_decision` | 2 | 21.81s | 21.81s | 7053.0 |
| `weather` | 3 | 6.14s | 5.94s | 2339.3 |

## Slowest Cases

| Case | Intent | Latency | Tokens |
| --- | --- | ---: | ---: |
| `base_009_transport_decision_read_only` | `transport_decision` | 27.37s | 6,012 |
| `base_013_transport_decision_tomorrow_read_only` | `transport_decision` | 16.25s | 8,094 |
| `base_007_flight_ticket_query_route` | `flight` | 7.57s | 3,563 |
| `base_005_train_ticket_query_route` | `train` | 7.51s | 3,572 |
| `base_004_weather_query_date_range` | `weather` | 7.31s | 2,441 |

## Takeaways

- `transport_decision` is the dominant latency bottleneck, with two cases at `27.37s` and `16.25s`.
- `time` and `my_orders` are the fastest flows and already close to an acceptable interactive baseline.
- `weather` / `train` / `flight` are clustered around `6-7s`, which suggests the read path is relatively stable but still LLM-heavy.
- The next optimization target should be reducing `transport_decision` latency and token usage before doing large structural refactors.
