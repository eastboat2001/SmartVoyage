---
name: travel-read
description: SmartVoyage 的交通只读技能。当运行时需要识别只读交通请求、生成天气或票务 Query Plan 时使用。
owner_roles:
  - travel_read
capabilities:
  - read_kind
  - weather_plan
  - ticket_plan
entry_assets:
  read_kind: assets/read_kind.md
  weather_plan: assets/weather_plan.md
  ticket_plan: assets/ticket_plan.md
default_references: {}
conditional_references:
  read_kind: {}
  weather_plan:
    has_relative_date:
      - references/relative_date_rules.md
  ticket_plan:
    has_relative_date:
      - references/relative_date_rules.md
---

# 交通只读

用于构建这些 Prompt：

- 只读类型分类
- 天气查询计划生成
- 票务查询计划生成

这个 skill 只负责只读链路的语义规则。

不要把 SQL 编译或数据库真相放进这个 skill。
