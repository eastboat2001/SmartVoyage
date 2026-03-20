"""
mcp_order_server.py：交通票务订单 MCP 服务器，负责火车票和机票预定，以及订单查询。

核心功能：
    火车票预定、飞机票预定。
    查询用户订单。
    防重复下单与库存扣减。
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
    instructions="交通票务订单工具，支持火车票、机票预定与订单查询。",
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
            filters = [
                "u.username = %s",
                "o.status = 'booked'",
            ]
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

            update_sql = f"""
                UPDATE {table_name}
                SET remaining_seats = remaining_seats - %s
                WHERE id = %s
            """
            cursor.execute(update_sql, (quantity, ticket["id"]))

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

    @staticmethod
    def _find_duplicate_order(
        *,
        cursor,
        user_id: int,
        order_type: str,
        departure_time,
        transport_no: str,
        ticket_type: str,
    ):
        cursor.execute(
            """
            SELECT id, quantity
            FROM orders
            WHERE user_id = %s
              AND order_type = %s
              AND status = 'booked'
              AND departure_time = %s
              AND transport_no = %s
              AND ticket_or_room_type = %s
            LIMIT 1
            """,
            (user_id, order_type, departure_time, transport_no, ticket_type),
        )
        return cursor.fetchone()

    def _get_user_id(self, cursor, username: str) -> int:
        cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
        row = cursor.fetchone()
        if row:
            return int(row["id"])
        cursor.execute(
            """
            INSERT INTO users (username, phone, default_departure_city)
            VALUES (%s, %s, %s)
            """,
            (
                username,
                self._phone_for_username(username),
                self.config.default_departure_city,
            ),
        )
        return int(cursor.lastrowid)

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
