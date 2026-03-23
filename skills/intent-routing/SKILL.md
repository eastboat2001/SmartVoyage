---
name: intent-routing
description: SmartVoyage 的意图识别与交通查询上下文技能。当运行时需要识别用户意图、为下游 agent 改写用户查询，或判断交通请求是否缺少明确的出发城市和车次/航班号信息时使用。
owner_roles:
  - supervisor
capabilities:
  - intent_recognition
  - travel_query_context
entry_assets:
  intent_recognition: assets/intent_recognize.md
  travel_query_context: assets/travel_query_context.md
default_references: {}
conditional_references:
  intent_recognition:
    has_query_rewrite_context:
      - references/query_rewrite_context.md
    has_transport_decision_request:
      - references/transport_decision_focus.md
    has_relative_date:
      - references/relative_date_focus.md
  travel_query_context: {}
---

# 意图路由

用于构建这些 Prompt：

- 意图识别
- 多意图路由
- `transport_decision` 前置路由
- 交通查询上下文分析

这个 skill 只负责语义层路由规则。

不要把确定性编排、状态迁移或服务调用规则放进这里。

运行时模板位于 `assets/`。

更详细的路由约束位于 `references/`。
