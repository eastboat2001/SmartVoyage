---
name: transport-decision
description: SmartVoyage 的交通决策规划技能。当运行时需要把用户出行诉求与天气上下文转成明确的高铁或飞机建议、生成下游票务查询计划时使用。
owner_roles:
  - supervisor
capabilities:
  - decision_plan
entry_assets:
  decision_plan: assets/plan.md
default_references: {}
conditional_references:
  decision_plan:
    has_relative_date:
      - references/relative_date_rules.md
    weather_degraded:
      - references/weather_degradation_rules.md
    weather_no_data:
      - references/weather_no_data_rules.md
---

# 交通决策

用于构建这些 Prompt：

- 出行建议规划

这个 skill 只负责语义规划和建议表达。

不要把编排流转或实际下单执行逻辑放进这个 skill。
