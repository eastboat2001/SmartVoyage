"""
mcp_hotel_server.py：酒店 MCP 服务器，负责酒店查询、酒店预订和酒店订单查询。
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.db import get_db_connection
from utils.format import DateEncoder, default_encoder

conf = Config()

hotel_mcp = FastMCP(
    name="HotelTools",
    instructions="酒店工具，支持酒店查询、酒店预订和酒店订单查询。",
    log_level="ERROR",
    host="127.0.0.1",
    port=8004,
)


class HotelService:
    def __init__(self, config: Config):
        self.config = config

    def query_hotels(self, sql: str) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            for result in results:
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):
                        result[key] = default_encoder(value)
            payload = {"status": "success", "data": results} if results else {
                "status": "no_data",
                "message": "未找到可预订酒店，请确认城市、日期或房型条件。",
            }
            return json.dumps(payload, cls=DateEncoder, ensure_ascii=False)
        except Exception as exc:
            logger.error(f"酒店查询错误: {exc}")
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    def query_user_hotel_orders(self, username: str, check_in_date: str = "") -> str:
        conn = get_db_connection(self.config)
        try:
            cursor = conn.cursor(dictionary=True)
            filters = ["u.username = %s", "o.status = 'booked'", "o.order_type = 'hotel'"]
            params: list[object] = [username]
            if check_in_date:
                filters.append("DATE(o.departure_time) = %s")
                params.append(check_in_date)
            sql = f"""
                SELECT
                    o.id,
                    o.departure_city,
                    o.hotel_name,
                    o.departure_time,
                    o.ticket_or_room_type,
                    o.quantity,
                    o.stay_nights,
                    o.unit_price,
                    o.total_price
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE {' AND '.join(filters)}
                ORDER BY o.departure_time ASC, o.id ASC
            """
            cursor.execute(sql, params)
            orders = cursor.fetchall()
            cursor.close()
            if not orders:
                if check_in_date:
                    return f"{username} 在 {check_in_date} 没有已预订酒店订单。"
                return f"{username} 当前没有已预订酒店订单。"

            lines = [f"{username} 的已预订酒店订单如下："]
            for order in orders:
                lines.append(
                    f"订单#{order['id']}：{order['departure_city']} {order['hotel_name']}，"
                    f"{order['departure_time']} 入住，{order['ticket_or_room_type']}，"
                    f"{order['quantity']}间，{order['stay_nights']}晚，总价 {order['total_price']} 元。"
                )
            return "\n".join(lines)
        finally:
            conn.close()

    def order_hotel_room(
        self,
        username: str,
        city: str,
        hotel_name: str,
        room_type: str,
        check_in_date: str,
        nights: int,
        rooms: int,
    ) -> str:
        if nights <= 0:
            return "入住晚数必须大于 0。"
        if rooms <= 0:
            return "预订房间数必须大于 0。"

        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            hotel = self._find_hotel(cursor, city=city, hotel_name=hotel_name)
            if isinstance(hotel, str):
                conn.rollback()
                return hotel

            stay_dates = [
                (datetime.strptime(check_in_date, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
                for offset in range(nights)
            ]
            inventory_rows = self._find_inventory_rows(
                cursor,
                hotel_id=hotel["id"],
                room_type=room_type,
                stay_dates=stay_dates,
                for_update=True,
            )
            if isinstance(inventory_rows, str):
                conn.rollback()
                return inventory_rows

            duplicate = self._find_duplicate_order(
                cursor=cursor,
                user_id=user_id,
                city=city,
                hotel_name=hotel_name,
                room_type=room_type,
                check_in_date=check_in_date,
            )
            if duplicate:
                conn.rollback()
                return (
                    f"检测到重复酒店订单：您已预订 {check_in_date} 入住的 {hotel_name} "
                    f"{room_type} {duplicate['quantity']}间，{duplicate['stay_nights']}晚。"
                )

            for row in inventory_rows:
                if row["remaining_rooms"] < rooms:
                    conn.rollback()
                    return (
                        f"余房不足：{hotel_name} {room_type} 在 {row['stay_date']} "
                        f"仅剩 {row['remaining_rooms']} 间。"
                    )

            for row in inventory_rows:
                cursor.execute(
                    """
                    UPDATE hotel_room_inventory
                    SET remaining_rooms = remaining_rooms - %s
                    WHERE id = %s
                    """,
                    (rooms, row["id"]),
                )

            unit_price = Decimal(str(inventory_rows[0]["price_per_night"]))
            total_price = unit_price * nights * rooms
            payload = {
                "hotel_id": hotel["id"],
                "hotel_name": hotel_name,
                "room_type": room_type,
                "check_in_date": check_in_date,
                "nights": nights,
                "rooms": rooms,
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
                    hotel_name,
                    stay_nights,
                    quantity,
                    unit_price,
                    total_price,
                    raw_order_payload
                ) VALUES (%s, 'hotel', 'booked', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    city,
                    city,
                    f"{check_in_date} 14:00:00",
                    room_type,
                    hotel_name,
                    hotel_name,
                    nights,
                    rooms,
                    unit_price,
                    total_price,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            order_id = cursor.lastrowid
            conn.commit()
            return (
                f"酒店预订成功，订单号 {order_id}。"
                f"{check_in_date} 入住 {city} {hotel_name}，{room_type} {rooms}间，{nights}晚，"
                f"每晚 {unit_price} 元，总价 {total_price} 元。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"酒店预订失败: {exc}")
            return f"酒店预订失败：{exc}"
        finally:
            conn.close()

    @staticmethod
    def _find_hotel(cursor, *, city: str, hotel_name: str):
        cursor.execute(
            """
            SELECT *
            FROM hotels
            WHERE city = %s AND name = %s
            LIMIT 1
            """,
            (city, hotel_name),
        )
        row = cursor.fetchone()
        return row or f"未找到酒店：{city} {hotel_name}。"

    @staticmethod
    def _find_inventory_rows(cursor, *, hotel_id: int, room_type: str, stay_dates: list[str], for_update: bool = False):
        placeholders = ", ".join(["%s"] * len(stay_dates))
        lock_clause = " FOR UPDATE" if for_update else ""
        cursor.execute(
            f"""
            SELECT *
            FROM hotel_room_inventory
            WHERE hotel_id = %s
              AND room_type = %s
              AND stay_date IN ({placeholders})
            ORDER BY stay_date ASC
            {lock_clause}
            """,
            [hotel_id, room_type, *stay_dates],
        )
        rows = cursor.fetchall()
        if len(rows) != len(stay_dates):
            return "未找到完整入住周期内的酒店房型库存，请修改日期或房型。"
        return rows

    @staticmethod
    def _find_duplicate_order(
        *,
        cursor,
        user_id: int,
        city: str,
        hotel_name: str,
        room_type: str,
        check_in_date: str,
    ):
        cursor.execute(
            """
            SELECT quantity, stay_nights
            FROM orders
            WHERE user_id = %s
              AND order_type = 'hotel'
              AND status = 'booked'
              AND departure_city = %s
              AND hotel_name = %s
              AND ticket_or_room_type = %s
              AND DATE(departure_time) = %s
            LIMIT 1
            """,
            (user_id, city, hotel_name, room_type, check_in_date),
        )
        return cursor.fetchone()

    def _get_user_id(self, cursor, username: str) -> int:
        cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
        row = cursor.fetchone()
        if row:
            return int(row["id"])
        cursor.execute(
            "INSERT INTO users (username, phone) VALUES (%s, %s)",
            (username, self._phone_for_username(username)),
        )
        return int(cursor.lastrowid)

    def _phone_for_username(self, username: str) -> str:
        if username == self.config.default_username:
            return self.config.default_user_phone
        digits = "".join(ch for ch in username if ch.isdigit())
        suffix = (digits or "1")[-8:].rjust(8, "0")
        return f"139{suffix}"


service = HotelService(conf)


@hotel_mcp.tool(
    name="query_hotels",
    description="查询酒店数据，输入 SQL，返回可预订酒店与房型信息"
)
def query_hotels(sql: str) -> str:
    logger.info(f"执行酒店查询: {sql}")
    return service.query_hotels(sql)


@hotel_mcp.tool(
    name="order_hotel_room",
    description="根据用户名、城市、酒店名、房型、入住日期、入住晚数和房间数预订酒店"
)
def order_hotel_room(
    username: str,
    city: str,
    hotel_name: str,
    room_type: str,
    check_in_date: str,
    nights: int,
    rooms: int,
) -> str:
    logger.info(
        "正在预订酒店: "
        f"{username}, {city}, {hotel_name}, {room_type}, {check_in_date}, {nights}, {rooms}"
    )
    return service.order_hotel_room(username, city, hotel_name, room_type, check_in_date, nights, rooms)


@hotel_mcp.tool(
    name="query_user_hotel_orders",
    description="根据用户名查询当前已预订酒店订单，可选按入住日期过滤"
)
def query_user_hotel_orders(username: str, check_in_date: str = "") -> str:
    logger.info(f"正在查询酒店订单: {username}, check_in_date={check_in_date}")
    return service.query_user_hotel_orders(username, check_in_date)


def create_hotel_mcp_server():
    logger.info("=== 酒店 MCP 服务器信息 ===")
    logger.info(f"名称: {hotel_mcp.name}")
    logger.info(f"描述: {hotel_mcp.instructions}")

    try:
        print("服务器已启动，请访问 http://127.0.0.1:8004/mcp")
        hotel_mcp.run(transport="streamable-http")
    except Exception as exc:
        print(f"服务器启动失败: {exc}")


if __name__ == "__main__":
    create_hotel_mcp_server()
