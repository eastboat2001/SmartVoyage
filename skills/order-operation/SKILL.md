---
name: order-operation
description: SmartVoyage 的订单操作技能。当运行时需要识别订单侧动作、为订单查询归一化日期、解析 HITL 审批回复，或在严格禁止编造的约束下抽取退票与改签参数时使用。
owner_roles:
  - order
capabilities:
  - action_classify
  - review_decision
  - date_resolution
  - operation_extraction
entry_assets:
  action_classify: assets/action.md
  review_decision: assets/review_decision.md
  date_resolution: assets/date_resolution.md
  operation_extraction: assets/operation_extraction.md
default_references: {}
conditional_references:
  action_classify:
    has_pending_context:
      - references/pending_context_rules.md
  review_decision: {}
  date_resolution:
    has_relative_date:
      - references/relative_date_rules.md
  operation_extraction:
    has_pending_context:
      - references/pending_context_rules.md
    is_change_order:
      - references/change_order_rules.md
---

# 订单操作

用于构建这些 Prompt：

- 订单动作分类
- HITL 审批决策解析
- 订单查询日期归一化
- 退票 / 改签参数抽取

这个 skill 只负责语义抽取和决策映射。

不要把订单工作流状态、checkpoint 或库存逻辑放进这个 skill。
