---
name: order-operation
description: SmartVoyage 的订单操作技能。当运行时需要识别订单侧动作、为订单查询归一化日期、解析 HITL 审批回复，或在严格禁止编造的约束下抽取退票与改签参数时使用。
---

# 订单操作

用于构建这些 Prompt：

- 订单动作分类
- HITL 审批决策解析
- 订单查询日期归一化
- 退票 / 改签参数抽取

这个 skill 只负责语义抽取和决策映射。

不要把订单工作流状态、checkpoint 或库存逻辑放进这个 skill。
