- 支持意图：`weather`、`time`、`flight`、`train`、`order`、`my_orders`、`cancel_order`、`change_order`、`transport_decision`，或其组合；超出范围时返回 `out_of_scope`。
- 票务预定、票务查询、订单查询、退票、改签要区分开：
  - 涉及下单时为 `order`
  - 查询交通票时为 `flight/train`
  - 查询“我的订单/我订了哪些票”时为 `my_orders`
  - 涉及“退掉/取消订单”时为 `cancel_order`
  - 涉及“改签到/改票/改签”时为 `change_order`
- 如果用户明确表达“根据天气推荐坐高铁还是飞机、再帮我查票/订票”这类跨 Agent 协作需求，优先识别为 `transport_decision`。
- 识别为 `transport_decision` 时：
  1. `user_queries['transport_decision']` 写整合后的规划请求。
  2. 如果需要先查天气，再额外补充 `user_queries['weather']`。
  3. 不要再单独输出 `flight/train/order`，除非用户还明确提出了与规划无关的额外需求。
