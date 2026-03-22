---
name: travel-read
description: SmartVoyage 的交通只读技能。当运行时需要识别只读交通请求、生成天气或票务 Query Plan，或在不编造业务事实的前提下总结天气与票务结果时使用。
---

# 交通只读

用于构建这些 Prompt：

- 只读类型分类
- 天气查询计划生成
- 票务查询计划生成
- 天气结果总结
- 票务结果总结

这个 skill 只负责只读链路的语义规则。

不要把 SQL 编译或数据库真相放进这个 skill。
