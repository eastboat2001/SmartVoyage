"""
mcp_order_server.py：交通票务订单 MCP 服务器，负责火车票和机票预定，以及订单生命周期管理。

核心功能：
    火车票预定、飞机票预定。
    查询用户订单。
    防重复下单与库存扣减。
    退票、改签、订单状态流转。
"""
import json
import os
import sys
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.db import get_db_connection

conf = Config()

order_mcp = FastMCP(
    name="OrderTools",
    instructions="交通票务订单工具，支持火车票、机票预定、订单查询、退票与改签。",
    log_level="ERROR",
    host="127.0.0.1",
    port=8003,
)


class OrderService:
    def __init__(self, config: Config):
        self.config = config

    def order_train(self, username: str, departure_date: str, train_number: str, seat_type: str, number: int) -> str:
        return self._create_order(
            username=username,
            order_type="train",
            departure_date=departure_date,
            transport_no=train_number,
            ticket_type=seat_type,
            quantity=number,
            table_name="train_tickets",
            transport_column="train_number",
            ticket_type_column="seat_type",
        )

    def order_flight(self, username: str, departure_date: str, flight_number: str, seat_type: str, number: int) -> str:
        return self._create_order(
            username=username,
            order_type="flight",
            departure_date=departure_date,
            transport_no=flight_number,
            ticket_type=seat_type,
            quantity=number,
            table_name="flight_tickets",
            transport_column="flight_number",
            ticket_type_column="cabin_type",
        )

    def query_user_orders(self, username: str, departure_date: str = "") -> str:
        conn = get_db_connection(self.config)
        try:
            cursor = conn.cursor(dictionary=True)
            filters = ["u.username = %s", "o.status = 'booked'"]
            params: list[object] = [username]
            if departure_date:
                filters.append("DATE(o.departure_time) = %s")
                params.append(departure_date)
            sql = f"""
                SELECT
                    o.id,
                    o.order_type,
                    o.departure_city,
                    o.arrival_city,
                    o.departure_time,
                    o.transport_no,
                    o.ticket_or_room_type,
                    o.quantity,
                    o.unit_price,
                    o.total_price,
                    o.status,
                    o.created_at
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE {' AND '.join(filters)}
                ORDER BY o.departure_time ASC, o.id ASC
            """
            cursor.execute(sql, params)
            orders = cursor.fetchall()
            cursor.close()
            if not orders:
                if departure_date:
                    return f"{username} 在 {departure_date} 没有已预订订单。"
                return f"{username} 当前没有已预订订单。"

            lines = [f"{username} 的已预订订单如下："]
            for order in orders:
                lines.append(
                    f"订单#{order['id']}：{self._order_type_label(order['order_type'])}，"
                    f"{order['departure_city']} -> {order['arrival_city']}，"
                    f"{order['departure_time']}，"
                    f"{order['transport_no']}，"
                    f"{order['ticket_or_room_type']}，"
                    f"{order['quantity']}张，"
                    f"总价 {order['total_price']} 元。"
                )
            return "\n".join(lines)
        finally:
            conn.close()

    def cancel_ticket_order(
        self,
        username: str,
        departure_date: str = "",
        departure_city: str = "",
        arrival_city: str = "",
        transport_no: str = "",
        ticket_type: str = "",
        order_type: str = "",
    ) -> str:
        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            order = self._find_single_booked_order(
                cursor=cursor,
                user_id=user_id,
                order_type=order_type,
                departure_date=departure_date,
                departure_city=departure_city,
                arrival_city=arrival_city,
                transport_no=transport_no,
                ticket_type=ticket_type,
                for_update=True,
            )
            if isinstance(order, str):
                conn.rollback()
                return order

            self._restore_ticket_inventory(cursor, order)
            cursor.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = %s",
                (order["id"],),
            )
            conn.commit()
            return (
                f"退票成功：已取消订单#{order['id']}，"
                f"{order['departure_time']} {order['departure_city']}到{order['arrival_city']} "
                f"{order['transport_no']} {order['ticket_or_room_type']} {order['quantity']}张。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"退票失败: {exc}")
            return f"退票失败：{exc}"
        finally:
            conn.close()

    def change_ticket_order(
        self,
        username: str,
        current_departure_date: str = "",
        departure_city: str = "",
        arrival_city: str = "",
        current_transport_no: str = "",
        current_ticket_type: str = "",
        new_departure_date: str = "",
        new_transport_no: str = "",
        new_ticket_type: str = "",
        order_type: str = "",
    ) -> str:
        if not new_departure_date and not new_transport_no and not new_ticket_type:
            return "改签至少需要提供新的日期、车次/航班号或席位/舱位信息。"

        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            current_order = self._find_single_booked_order(
                cursor=cursor,
                user_id=user_id,
                order_type=order_type,
                departure_date=current_departure_date,
                departure_city=departure_city,
                arrival_city=arrival_city,
                transport_no=current_transport_no,
                ticket_type=current_ticket_type,
                for_update=True,
            )
            if isinstance(current_order, str):
                conn.rollback()
                return current_order

            resolved_type = current_order["order_type"]
            target_date = new_departure_date or str(current_order["departure_time"])[:10]
            target_ticket_type = new_ticket_type or current_order["ticket_or_room_type"]
            target_transport_no = new_transport_no or ""

            target_ticket = self._find_target_ticket(
                cursor=cursor,
                order=current_order,
                target_date=target_date,
                target_transport_no=target_transport_no,
                target_ticket_type=target_ticket_type,
            )
            if isinstance(target_ticket, str):
                conn.rollback()
                return target_ticket

            if (
                current_order["departure_time"] == target_ticket["departure_time"]
                and current_order["transport_no"] == self._ticket_transport_no(resolved_type, target_ticket)
                and current_order["ticket_or_room_type"] == self._ticket_type_value(resolved_type, target_ticket)
            ):
                conn.rollback()
                return "改签目标和当前订单完全一致，无需改签。"

            duplicate = self._find_duplicate_order(
                cursor=cursor,
                user_id=user_id,
                order_type=resolved_type,
                departure_time=target_ticket["departure_time"],
                transport_no=self._ticket_transport_no(resolved_type, target_ticket),
                ticket_type=self._ticket_type_value(resolved_type, target_ticket),
                exclude_order_id=current_order["id"],
            )
            if duplicate:
                conn.rollback()
                return (
                    f"改签失败：您已存在相同目标订单，"
                    f"{target_ticket['departure_time']} {self._ticket_transport_no(resolved_type, target_ticket)} "
                    f"{self._ticket_type_value(resolved_type, target_ticket)} {duplicate['quantity']}张。"
                )

            if target_ticket["remaining_seats"] < current_order["quantity"]:
                conn.rollback()
                return (
                    f"改签失败：目标票务余票不足，"
                    f"{self._ticket_transport_no(resolved_type, target_ticket)} "
                    f"{self._ticket_type_value(resolved_type, target_ticket)} 当前仅剩 {target_ticket['remaining_seats']} 张。"
                )

            self._restore_ticket_inventory(cursor, current_order)
            self._deduct_ticket_inventory(cursor, resolved_type, target_ticket["id"], current_order["quantity"])

            cursor.execute(
                "UPDATE orders SET status = 'changed' WHERE id = %s",
                (current_order["id"],),
            )

            payload = {
                "previous_order_id": current_order["id"],
                "action": "change",
                "username": username,
                "order_type": resolved_type,
            }
            unit_price = Decimal(str(target_ticket["price"]))
            total_price = unit_price * current_order["quantity"]
            cursor.execute(
                """
                INSERT INTO orders (
                    user_id,
                    order_type,
                    status,
                    departure_city,
                    arrival_city,
                    departure_time,
                    ticket_or_room_type,
                    transport_no,
                    quantity,
                    unit_price,
                    total_price,
                    raw_order_payload
                ) VALUES (%s, %s, 'booked', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    resolved_type,
                    target_ticket["departure_city"],
                    target_ticket["arrival_city"],
                    target_ticket["departure_time"],
                    self._ticket_type_value(resolved_type, target_ticket),
                    self._ticket_transport_no(resolved_type, target_ticket),
                    current_order["quantity"],
                    unit_price,
                    total_price,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            new_order_id = cursor.lastrowid
            conn.commit()
            return (
                f"改签成功：原订单#{current_order['id']} 已变更，"
                f"新订单#{new_order_id} 为 {target_ticket['departure_time']} "
                f"{target_ticket['departure_city']}到{target_ticket['arrival_city']} "
                f"{self._ticket_transport_no(resolved_type, target_ticket)} "
                f"{self._ticket_type_value(resolved_type, target_ticket)} "
                f"{current_order['quantity']}张，总价 {total_price} 元。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"改签失败: {exc}")
            return f"改签失败：{exc}"
        finally:
            conn.close()

    def _create_order(
        self,
        *,
        username: str,
        order_type: str,
        departure_date: str,
        transport_no: str,
        ticket_type: str,
        quantity: int,
        table_name: str,
        transport_column: str,
        ticket_type_column: str,
    ) -> str:
        if quantity <= 0:
            return "下单数量必须大于 0。"

        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            ticket_sql = f"""
                SELECT *
                FROM {table_name}
                WHERE DATE(departure_time) = %s
                  AND {transport_column} = %s
                  AND {ticket_type_column} = %s
                FOR UPDATE
            """
            cursor.execute(ticket_sql, (departure_date, transport_no, ticket_type))
            ticket = cursor.fetchone()
            if not ticket:
                conn.rollback()
                return f"未找到可预订票务：{departure_date} {transport_no} {ticket_type}。"

            duplicate_order = self._find_duplicate_order(
                cursor=cursor,
                user_id=user_id,
                order_type=order_type,
                departure_time=ticket["departure_time"],
                transport_no=transport_no,
                ticket_type=ticket_type,
            )
            if duplicate_order:
                conn.rollback()
                return (
                    f"检测到重复订单：您已预订 "
                    f"{ticket['departure_time']} {ticket['departure_city']}到{ticket['arrival_city']} "
                    f"{transport_no} {ticket_type} {duplicate_order['quantity']}张。"
                    "如需新增，请修改时间/席位后再下单。"
                )

            if ticket["remaining_seats"] < quantity:
                conn.rollback()
                return (
                    f"余票不足：{transport_no} {ticket_type} 当前仅剩 {ticket['remaining_seats']} 张，"
                    f"无法预订 {quantity} 张。"
                )

            self._deduct_ticket_inventory(cursor, order_type, ticket["id"], quantity)
            unit_price = Decimal(str(ticket["price"]))
            total_price = unit_price * quantity
            payload = {
                "ticket_id": ticket["id"],
                "order_type": order_type,
                "username": username,
                "transport_no": transport_no,
                "ticket_type": ticket_type,
                "quantity": quantity,
            }
            cursor.execute(
                """
                INSERT INTO orders (
                    user_id,
                    order_type,
                    status,
                    departure_city,
                    arrival_city,
                    departure_time,
                    ticket_or_room_type,
                    transport_no,
                    quantity,
                    unit_price,
                    total_price,
                    raw_order_payload
                ) VALUES (%s, %s, 'booked', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    order_type,
                    ticket["departure_city"],
                    ticket["arrival_city"],
                    ticket["departure_time"],
                    ticket_type,
                    transport_no,
                    quantity,
                    unit_price,
                    total_price,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            order_id = cursor.lastrowid
            conn.commit()
            logger.info(f"订单创建成功: order_id={order_id}, username={username}, order_type={order_type}")
            return (
                f"预订成功，订单号 {order_id}。"
                f"{ticket['departure_time']} {ticket['departure_city']}到{ticket['arrival_city']} "
                f"{transport_no} {ticket_type} {quantity}张，"
                f"总价 {total_price} 元。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"订单创建失败: {exc}")
            return f"订单创建失败：{exc}"
        finally:
            conn.close()

    def _find_single_booked_order(
        self,
        *,
        cursor,
        user_id: int,
        order_type: str = "",
        departure_date: str = "",
        departure_city: str = "",
        arrival_city: str = "",
        transport_no: str = "",
        ticket_type: str = "",
        for_update: bool = False,
    ):
        filters = ["user_id = %s", "status = 'booked'"]
        params: list[object] = [user_id]
        if order_type:
            filters.append("order_type = %s")
            params.append(order_type)
        if departure_date:
            filters.append("DATE(departure_time) = %s")
            params.append(departure_date)
        if departure_city:
            filters.append("departure_city = %s")
            params.append(departure_city)
        if arrival_city:
            filters.append("arrival_city = %s")
            params.append(arrival_city)
        if transport_no:
            filters.append("transport_no = %s")
            params.append(transport_no)
        if ticket_type:
            filters.append("ticket_or_room_type = %s")
            params.append(ticket_type)

        lock_clause = " FOR UPDATE" if for_update else ""
        cursor.execute(
            f"""
            SELECT *
            FROM orders
            WHERE {' AND '.join(filters)}
            ORDER BY departure_time ASC, id ASC
            {lock_clause}
            """,
            params,
        )
        orders = cursor.fetchall()
        if not orders:
            if not transport_no and not (departure_date and departure_city and arrival_city):
                return "未找到符合条件的已预订订单，请补充车次/航班号，或补充完整的日期和出发到达城市。"
            suggestions = []
            if not departure_date:
                suggestions.append("日期")
            if not (departure_city and arrival_city):
                suggestions.append("出发和到达城市")
            if not transport_no:
                suggestions.append("车次/航班号")
            if not ticket_type:
                suggestions.append("席位/舱位")
            suffix = "、".join(suggestions[:3]) if suggestions else "日期、路线或车次/航班号"
            return f"未找到符合条件的已预订订单，请补充更具体的信息，例如{suffix}。"
        if len(orders) > 1:
            return "匹配到多条订单，请补充更具体的日期、车次/航班号或席位/舱位信息。"
        return orders[0]

    def _find_target_ticket(self, *, cursor, order: dict, target_date: str, target_transport_no: str, target_ticket_type: str):
        table_name, transport_column, ticket_type_column = self._ticket_meta(order["order_type"])
        filters = [
            "departure_city = %s",
            "arrival_city = %s",
            "DATE(departure_time) = %s",
            f"{ticket_type_column} = %s",
        ]
        params: list[object] = [
            order["departure_city"],
            order["arrival_city"],
            target_date,
            target_ticket_type,
        ]
        if target_transport_no:
            filters.append(f"{transport_column} = %s")
            params.append(target_transport_no)
        cursor.execute(
            f"""
            SELECT *
            FROM {table_name}
            WHERE {' AND '.join(filters)}
            ORDER BY departure_time ASC
            FOR UPDATE
            """,
            params,
        )
        tickets = cursor.fetchall()
        if not tickets:
            missing = []
            if not target_date:
                missing.append("新日期")
            if not target_transport_no:
                missing.append("新车次/航班号")
            if not target_ticket_type:
                missing.append("新席位/舱位")
            suffix = "、".join(missing) if missing else "新日期、新车次/航班号或新席位/舱位"
            return f"未找到符合改签条件的新票务，请补充更明确的{suffix}。"
        return tickets[0]

    def _restore_ticket_inventory(self, cursor, order: dict) -> None:
        table_name, transport_column, ticket_type_column = self._ticket_meta(order["order_type"])
        cursor.execute(
            f"""
            UPDATE {table_name}
            SET remaining_seats = remaining_seats + %s
            WHERE {transport_column} = %s
              AND {ticket_type_column} = %s
              AND departure_time = %s
            """,
            (
                order["quantity"],
                order["transport_no"],
                order["ticket_or_room_type"],
                order["departure_time"],
            ),
        )

    def _deduct_ticket_inventory(self, cursor, order_type: str, ticket_id: int, quantity: int) -> None:
        table_name, _, _ = self._ticket_meta(order_type)
        cursor.execute(
            f"""
            UPDATE {table_name}
            SET remaining_seats = remaining_seats - %s
            WHERE id = %s
            """,
            (quantity, ticket_id),
        )

    @staticmethod
    def _find_duplicate_order(
        *,
        cursor,
        user_id: int,
        order_type: str,
        departure_time,
        transport_no: str,
        ticket_type: str,
        exclude_order_id: int | None = None,
    ):
        sql = """
            SELECT id, quantity
            FROM orders
            WHERE user_id = %s
              AND order_type = %s
              AND status = 'booked'
              AND departure_time = %s
              AND transport_no = %s
              AND ticket_or_room_type = %s
        """
        params: list[object] = [user_id, order_type, departure_time, transport_no, ticket_type]
        if exclude_order_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_order_id)
        sql += " LIMIT 1"
        cursor.execute(sql, params)
        return cursor.fetchone()

    def _get_user_id(self, cursor, username: str) -> int:
        cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
        row = cursor.fetchone()
        if row:
            return int(row["id"])
        cursor.execute(
            """
            INSERT INTO users (username, phone)
            VALUES (%s, %s)
            """,
            (username, self._phone_for_username(username)),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _ticket_meta(order_type: str) -> tuple[str, str, str]:
        if order_type == "train":
            return "train_tickets", "train_number", "seat_type"
        return "flight_tickets", "flight_number", "cabin_type"

    @staticmethod
    def _ticket_transport_no(order_type: str, ticket: dict) -> str:
        return ticket["train_number"] if order_type == "train" else ticket["flight_number"]

    @staticmethod
    def _ticket_type_value(order_type: str, ticket: dict) -> str:
        return ticket["seat_type"] if order_type == "train" else ticket["cabin_type"]

    @staticmethod
    def _order_type_label(order_type: str) -> str:
        return "高铁票" if order_type == "train" else "机票"

    def _phone_for_username(self, username: str) -> str:
        if username == self.config.default_username:
            return self.config.default_user_phone
        digits = "".join(ch for ch in username if ch.isdigit())
        suffix = (digits or "1")[-8:].rjust(8, "0")
        return f"139{suffix}"


service = OrderService(conf)


@order_mcp.tool(
    name="order_train",
    description="根据用户名、日期、车次、座位类型、数量预定火车票，并自动落库"
)
def order_train(username: str, departure_date: str, train_number: str, seat_type: str, number: int) -> str:
    logger.info(f"正在订购火车票: {username}, {departure_date}, {train_number}, {seat_type}, {number}")
    return service.order_train(username, departure_date, train_number, seat_type, number)


@order_mcp.tool(
    name="order_flight",
    description="根据用户名、日期、航班号、舱位类型、数量预定机票，并自动落库"
)
def order_flight(username: str, departure_date: str, flight_number: str, seat_type: str, number: int) -> str:
    logger.info(f"正在订购飞机票: {username}, {departure_date}, {flight_number}, {seat_type}, {number}")
    return service.order_flight(username, departure_date, flight_number, seat_type, number)


@order_mcp.tool(
    name="query_user_orders",
    description="根据用户名查询当前已预订订单，可选按出发日期过滤"
)
def query_user_orders(username: str, departure_date: str = "") -> str:
    logger.info(f"正在查询用户订单: {username}, departure_date={departure_date}")
    return service.query_user_orders(username, departure_date)


@order_mcp.tool(
    name="cancel_ticket_order",
    description="根据用户名和订单特征退掉一张已预订的高铁票或机票，会自动恢复库存"
)
def cancel_ticket_order(
    username: str,
    departure_date: str = "",
    departure_city: str = "",
    arrival_city: str = "",
    transport_no: str = "",
    ticket_type: str = "",
    order_type: str = "",
) -> str:
    logger.info(f"正在退票: {username}, {departure_date}, {departure_city}, {arrival_city}, {transport_no}, {ticket_type}, {order_type}")
    return service.cancel_ticket_order(
        username=username,
        departure_date=departure_date,
        departure_city=departure_city,
        arrival_city=arrival_city,
        transport_no=transport_no,
        ticket_type=ticket_type,
        order_type=order_type,
    )


@order_mcp.tool(
    name="change_ticket_order",
    description="根据用户名和当前订单信息执行高铁票或机票改签，会回补原库存并扣减新库存"
)
def change_ticket_order(
    username: str,
    current_departure_date: str = "",
    departure_city: str = "",
    arrival_city: str = "",
    current_transport_no: str = "",
    current_ticket_type: str = "",
    new_departure_date: str = "",
    new_transport_no: str = "",
    new_ticket_type: str = "",
    order_type: str = "",
) -> str:
    logger.info(
        "正在改签: "
        f"{username}, {current_departure_date}, {departure_city}, {arrival_city}, "
        f"{current_transport_no}, {current_ticket_type}, {new_departure_date}, {new_transport_no}, {new_ticket_type}, {order_type}"
    )
    return service.change_ticket_order(
        username=username,
        current_departure_date=current_departure_date,
        departure_city=departure_city,
        arrival_city=arrival_city,
        current_transport_no=current_transport_no,
        current_ticket_type=current_ticket_type,
        new_departure_date=new_departure_date,
        new_transport_no=new_transport_no,
        new_ticket_type=new_ticket_type,
        order_type=order_type,
    )


def create_order_mcp_server():
    logger.info("=== 票务预定MCP服务器信息 ===")
    logger.info(f"名称: {order_mcp.name}")
    logger.info(f"描述: {order_mcp.instructions}")

    try:
        print("服务器已启动，请访问 http://127.0.0.1:8003/mcp")
        order_mcp.run(transport="streamable-http")
    except Exception as e:
        print(f"服务器启动失败: {e}")


if __name__ == "__main__":
    create_order_mcp_server()
