# SmartVoyage 副作用测试方案

这份文档用于规划第二批 LangSmith / 手工回归测试，重点覆盖会修改数据库状态的用例，例如预订、退票、改签、酒店取消、酒店改期，以及 `travel_plan` 触发联动订票。

## 1. 目标

当前 `langsmith_eval/cases.json` 主要是无副作用回归集，适合频繁批量运行。

第二批测试的目标是：

- 覆盖真实业务状态流转
- 验证库存回补 / 库存扣减是否正确
- 验证订单状态是否符合预期
- 沉淀可执行的 LangSmith 副作用实验集，并对数据库状态变化做自动断言

## 2. 设计原则

副作用 case 不能直接与当前无副作用基线混跑，原因是：

- 会修改 `orders`
- 会修改 `train_tickets.remaining_seats`
- 会修改 `flight_tickets.remaining_seats`
- 会修改 `hotel_room_inventory.remaining_rooms`
- 不同 case 之间会互相污染前置条件

因此第二批测试必须满足下面任一条件：

1. 每次测试前都重置数据库到基线数据
2. 每条 case 使用独立测试用户
3. 每条 case 使用独立测试库

当前项目最简单可落地的方式：

- 先采用“每次测试前重置数据库到基线数据”
- 后续如果要频繁跑副作用实验，再补“独立测试用户”方案

## 3. 基线数据定义

默认基线：

- 表结构来自 `sql/create_table.sql`
- 种子数据来自 `sql/insert_data.sql`
- 默认用户为 `demo_user`

基线重点表：

- `users`
- `user_preferences`
- `train_tickets`
- `flight_tickets`
- `hotels`
- `hotel_room_inventory`
- `orders`
- `weather_data`

## 4. 数据库重置方式

### 方案 A：完全重建数据库

适合：

- 每次开始一轮副作用回归前
- 跑完整副作用实验集前

步骤：

1. 清空目标测试库，或重新创建空库
2. 执行 `sql/create_table.sql`
3. 执行 `sql/insert_data.sql`

也可以直接使用项目脚本：

```powershell
.\.venv\Scripts\python.exe scripts\reset_database.py
```

如果是在 LangSmith 副作用评测里通过 `--db-reset-command` 逐条调用该脚本，评测 runner 会自动补上 `--skip-stop-services`，避免把已启动的后端服务停掉。

优点：

- 最稳定
- 不依赖手工回滚订单和库存

缺点：

- 每轮测试前都要重建

### 方案 B：只回滚业务表

适合：

- 单条 case 调试时快速复位

建议回滚范围：

- 清空 `orders`
- 重新导入 `train_tickets`
- 重新导入 `flight_tickets`
- 重新导入 `hotel_room_inventory`

风险：

- 如果脚本不完整，容易遗漏状态
- 不建议作为正式 LangSmith 批跑方案

## 5. 副作用 case

### SE-001 交通订票

前置数据：

- 使用基线数据库
- `demo_user` 存在
- `2026-03-21 北京 -> 上海` 的高铁票存在可订库存
- `orders` 中不存在同一用户、同一日期、同一车次、同一席别的重复订单

输入：

- `帮我预订2026-03-21北京到上海的高铁票，二等座1张`

预期回复：

- 返回订票成功
- 返回订单号

预期数据库变化：

- `orders` 新增 1 条 `order_type='train'`
- 新订单 `status='booked'`
- 对应 `train_tickets.remaining_seats` 减 1

重置方式：

- 推荐完整重建数据库

### SE-002 交通退票

前置数据：

- 已先执行 `SE-001` 或手工准备一笔已预订交通订单
- 对应订单 `status='booked'`

输入：

- `帮我退掉2026-03-21北京到上海的高铁票`

预期回复：

- 返回退票成功或取消成功

预期数据库变化：

- 原订单 `status='cancelled'`
- 对应 `train_tickets.remaining_seats` 加 1

重置方式：

- 推荐完整重建数据库

### SE-003 交通改签

前置数据：

- 已存在一笔 `2026-03-21 北京 -> 上海` 已预订高铁订单
- `2026-03-22` 目标车次仍有库存

输入：

- `把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座`

预期回复：

- 返回改签成功
- 返回原订单与新订单信息

预期数据库变化：

- 原订单 `status='changed'`
- 新增 1 条新订单，`status='booked'`
- 原车次库存回补
- 新车次库存扣减

重置方式：

- 推荐完整重建数据库

### SE-004 酒店预订

前置数据：

- 使用基线数据库
- `上海外滩云际酒店` 对应房型在 `2026-03-21`、`2026-03-22` 仍有库存

输入：

- `帮我订2026-03-21上海外滩云际酒店的高级大床房，住2晚1间`

预期回复：

- 返回预订成功
- 正确展示逐晚价格与总价

预期数据库变化：

- `orders` 新增 1 条 `order_type='hotel'`
- 新订单 `status='booked'`
- `hotel_room_inventory.remaining_rooms` 在涉及的每晚各减 1

重置方式：

- 推荐完整重建数据库

### SE-005 酒店取消

前置数据：

- 已存在一笔已预订酒店订单
- 订单 `status='booked'`

输入：

- `取消我订的2026-03-21上海外滩云际酒店`

预期回复：

- 返回酒店取消成功

预期数据库变化：

- 原订单 `status='cancelled'`
- 涉及每晚的 `hotel_room_inventory.remaining_rooms` 各加 1

重置方式：

- 推荐完整重建数据库

### SE-006 酒店改期

前置数据：

- 已存在一笔已预订酒店订单
- 新日期对应房型仍有库存

输入：

- `把我2026-03-21上海外滩云际酒店改到2026-03-22`

预期回复：

- 返回酒店改期成功
- 返回原订单与新订单信息

预期数据库变化：

- 原订单 `status='changed'`
- 新增 1 条酒店订单，`status='booked'`
- 原入住区间库存回补
- 新入住区间库存扣减

重置方式：

- 推荐完整重建数据库

### SE-007 travel_plan 触发联动订票

前置数据：

- 使用基线数据库
- 当前行程存在明确交通缺口
- 用户请求中明确表达“有合适票就直接订”

输入：

- `从北京出发，结合2026-03-21上海开始两天的天气、交通和酒店，帮我做方案；如果高铁合适就直接帮我订票`

预期回复：

- `travel_plan` 识别明确的联动下单意图
- 在给出方案后继续进入订票执行

预期数据库变化：

- `orders` 新增交通订单
- 对应票务库存扣减
- 如果当前交通已齐备，则不应重复下单

重置方式：

- 推荐完整重建数据库

## 6. LangSmith 落地建议

建议把第二批副作用测试单独做成新的 dataset，例如：

- `SmartVoyage Side Effect Regression`

不要和当前核心无副作用基线集混用。

建议字段：

- `case_id`
- `description`
- `turns`
- `setup_profile`
- `expected_db_changes`
- `db_assertions`
- `reset_strategy`

## 7. 执行顺序建议

如果要先做手工验证，再转 LangSmith，推荐顺序：

1. 先验证 `SE-004 酒店预订`
2. 再验证 `SE-005 酒店取消`
3. 再验证 `SE-006 酒店改期`
4. 再验证 `SE-001 / SE-002 / SE-003` 交通订单生命周期
5. 最后验证 `SE-007 travel_plan 触发联动订票`

原因：

- 酒店链路目前 state 设计和业务闭环最完整
- `travel_plan` 条件式下单意图触发联动订票，依赖前面票务链路稳定

## 8. 当前结论

当前副作用测试已接入独立 LangSmith dataset，并支持逐条数据库断言。

当前最小可落地方案已经明确：

- 第一批：继续保留 `langsmith_eval/cases.json` 作为无副作用基线集
- 第二批：基于本方案维护独立副作用测试集
- 数据重置方式：优先采用“完整重建数据库”
- side-effect runner 已支持 `setup_profile` 注入前置订单，并对 `orders / train_tickets / flight_tickets / hotel_room_inventory` 做数据库前后断言
- 当前已提供可执行脚本：`scripts/reset_database.py`
