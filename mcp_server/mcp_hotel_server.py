"""
mcp_hotel_server.py：酒店 MCP 服务器，负责酒店查询、酒店预订、酒店订单查询、取消与改期。
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
    instructions="酒店工具，支持酒店查询、酒店预订、酒店订单查询、酒店取消与酒店改期。",
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
            resolved_city = str(hotel.get("city") or city).strip()
            resolved_hotel_name = str(hotel.get("name") or hotel_name).strip()

            stay_dates = self._build_stay_dates(check_in_date, nights)
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
                city=resolved_city,
                hotel_name=resolved_hotel_name,
                room_type=room_type,
                check_in_date=check_in_date,
                stay_nights=nights,
            )
            if duplicate:
                conn.rollback()
                return (
                    f"检测到重复酒店订单：您已预订 {check_in_date} 入住的 {resolved_hotel_name} "
                    f"{room_type} {duplicate['quantity']}间，{duplicate['stay_nights']}晚。"
                )

            inventory_error = self._ensure_inventory_enough(inventory_rows, rooms, resolved_hotel_name, room_type)
            if inventory_error:
                conn.rollback()
                return inventory_error

            self._deduct_inventory_rows(cursor, inventory_rows, rooms)
            unit_price = Decimal(str(inventory_rows[0]["price_per_night"]))
            total_price = self._total_price_for_inventory(inventory_rows, rooms)
            payload = {
                "hotel_id": hotel["id"],
                "hotel_name": resolved_hotel_name,
                "room_type": room_type,
                "check_in_date": check_in_date,
                "nights": nights,
                "rooms": rooms,
            }
            self._insert_hotel_order(
                cursor=cursor,
                user_id=user_id,
                city=resolved_city,
                hotel_name=resolved_hotel_name,
                room_type=room_type,
                check_in_date=check_in_date,
                nights=nights,
                rooms=rooms,
                unit_price=unit_price,
                total_price=total_price,
                raw_payload=payload,
            )
            order_id = cursor.lastrowid
            conn.commit()
            price_summary = self._format_price_summary(inventory_rows, rooms)
            return (
                f"酒店预订成功，订单号 {order_id}。"
                f"{check_in_date} 入住 {resolved_city} {resolved_hotel_name}，{room_type} {rooms}间，{nights}晚，"
                f"{price_summary}，总价 {total_price} 元。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"酒店预订失败: {exc}")
            return f"酒店预订失败：{exc}"
        finally:
            conn.close()

    def cancel_hotel_order(
        self,
        username: str,
        city: str = "",
        hotel_name: str = "",
        room_type: str = "",
        check_in_date: str = "",
    ) -> str:
        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            order = self._find_single_booked_hotel_order(
                cursor=cursor,
                user_id=user_id,
                city=city,
                hotel_name=hotel_name,
                room_type=room_type,
                check_in_date=check_in_date,
                for_update=True,
            )
            if isinstance(order, str):
                conn.rollback()
                return order

            self._restore_hotel_inventory(cursor, order)
            cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s", (order["id"],))
            conn.commit()
            return (
                f"酒店取消成功：已取消订单#{order['id']}，"
                f"{str(order['departure_time'])[:10]} 入住 {order['departure_city']} {order['hotel_name']}，"
                f"{order['ticket_or_room_type']} {order['quantity']}间，{order['stay_nights']}晚。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"酒店取消失败: {exc}")
            return f"酒店取消失败：{exc}"
        finally:
            conn.close()

    def change_hotel_order(
        self,
        username: str,
        current_city: str = "",
        current_hotel_name: str = "",
        current_room_type: str = "",
        current_check_in_date: str = "",
        new_city: str = "",
        new_hotel_name: str = "",
        new_room_type: str = "",
        new_check_in_date: str = "",
        new_nights: int = 0,
    ) -> str:
        has_new_target = any([new_city, new_hotel_name, new_room_type, new_check_in_date, new_nights > 0])
        if not has_new_target:
            return "酒店改期/改订至少需要提供新的入住日期、酒店、城市、房型或晚数。"

        conn = get_db_connection(self.config)
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            user_id = self._get_user_id(cursor, username)
            current_order = self._find_single_booked_hotel_order(
                cursor=cursor,
                user_id=user_id,
                city=current_city,
                hotel_name=current_hotel_name,
                room_type=current_room_type,
                check_in_date=current_check_in_date,
                for_update=True,
            )
            if isinstance(current_order, str):
                conn.rollback()
                return current_order

            target_city = new_city or str(current_order["departure_city"])
            target_hotel_name = new_hotel_name or str(current_order["hotel_name"])
            target_room_type = new_room_type or str(current_order["ticket_or_room_type"])
            target_check_in_date = new_check_in_date or str(current_order["departure_time"])[:10]
            target_nights = new_nights if new_nights > 0 else int(current_order["stay_nights"])

            if (
                target_city == str(current_order["departure_city"])
                and target_hotel_name == str(current_order["hotel_name"])
                and target_room_type == str(current_order["ticket_or_room_type"])
                and target_check_in_date == str(current_order["departure_time"])[:10]
                and target_nights == int(current_order["stay_nights"])
            ):
                conn.rollback()
                return "改期目标和当前酒店订单完全一致，无需改期。"

            hotel = self._find_hotel(cursor, city=target_city, hotel_name=target_hotel_name)
            if isinstance(hotel, str):
                conn.rollback()
                return hotel

            stay_dates = self._build_stay_dates(target_check_in_date, target_nights)
            inventory_rows = self._find_inventory_rows(
                cursor,
                hotel_id=int(hotel["id"]),
                room_type=target_room_type,
                stay_dates=stay_dates,
                for_update=True,
            )
            if isinstance(inventory_rows, str):
                conn.rollback()
                return inventory_rows

            duplicate = self._find_duplicate_order(
                cursor=cursor,
                user_id=user_id,
                city=target_city,
                hotel_name=target_hotel_name,
                room_type=target_room_type,
                check_in_date=target_check_in_date,
                stay_nights=target_nights,
                exclude_order_id=int(current_order["id"]),
            )
            if duplicate:
                conn.rollback()
                return (
                    f"酒店改期失败：您已存在相同目标订单，"
                    f"{target_check_in_date} 入住 {target_hotel_name} {target_room_type} "
                    f"{duplicate['quantity']}间，{duplicate['stay_nights']}晚。"
                )

            inventory_error = self._ensure_inventory_enough(
                inventory_rows,
                int(current_order["quantity"]),
                target_hotel_name,
                target_room_type,
            )
            if inventory_error:
                conn.rollback()
                return inventory_error

            self._restore_hotel_inventory(cursor, current_order)
            self._deduct_inventory_rows(cursor, inventory_rows, int(current_order["quantity"]))
            cursor.execute("UPDATE orders SET status = 'changed' WHERE id = %s", (current_order["id"],))

            unit_price = Decimal(str(inventory_rows[0]["price_per_night"]))
            total_price = self._total_price_for_inventory(inventory_rows, int(current_order["quantity"]))
            payload = {
                "previous_order_id": current_order["id"],
                "action": "change",
                "username": username,
                "city": target_city,
                "hotel_name": target_hotel_name,
                "room_type": target_room_type,
                "check_in_date": target_check_in_date,
                "nights": target_nights,
                "rooms": int(current_order["quantity"]),
            }
            self._insert_hotel_order(
                cursor=cursor,
                user_id=user_id,
                city=target_city,
                hotel_name=target_hotel_name,
                room_type=target_room_type,
                check_in_date=target_check_in_date,
                nights=target_nights,
                rooms=int(current_order["quantity"]),
                unit_price=unit_price,
                total_price=total_price,
                raw_payload=payload,
            )
            new_order_id = cursor.lastrowid
            conn.commit()
            price_summary = self._format_price_summary(inventory_rows, int(current_order["quantity"]))
            return (
                f"酒店改期成功：原订单#{current_order['id']} 已变更，"
                f"新订单#{new_order_id} 为 {target_check_in_date} 入住 {target_city} {target_hotel_name}，"
                f"{target_room_type} {current_order['quantity']}间，{target_nights}晚，"
                f"{price_summary}，总价 {total_price} 元。"
            )
        except Exception as exc:
            conn.rollback()
            logger.error(f"酒店改期失败: {exc}")
            return f"酒店改期失败：{exc}"
        finally:
            conn.close()

    @staticmethod
    def _find_hotel(cursor, *, city: str, hotel_name: str):
        normalized_city = city.strip()
        normalized_hotel_name = hotel_name.strip()
        if not normalized_hotel_name:
            return "请补充酒店名称。"

        if normalized_city:
            cursor.execute(
                """
                SELECT *
                FROM hotels
                WHERE city = %s AND name = %s
                LIMIT 1
                """,
                (normalized_city, normalized_hotel_name),
            )
            row = cursor.fetchone()
            if row:
                return row

        cursor.execute(
            """
            SELECT *
            FROM hotels
            WHERE name = %s
            ORDER BY id ASC
            """,
            (normalized_hotel_name,),
        )
        exact_rows = cursor.fetchall()
        if len(exact_rows) == 1:
            return exact_rows[0]
        if len(exact_rows) > 1:
            cities = "、".join(str(row["city"]) for row in exact_rows)
            return f"匹配到多家酒店：{normalized_hotel_name}，请补充城市，例如 {cities}。"

        cursor.execute(
            """
            SELECT *
            FROM hotels
            WHERE name LIKE %s
            ORDER BY id ASC
            """,
            (f"%{normalized_hotel_name}%",),
        )
        fuzzy_rows = cursor.fetchall()
        if len(fuzzy_rows) == 1:
            return fuzzy_rows[0]
        if len(fuzzy_rows) > 1:
            options = "；".join(f"{row['city']} {row['name']}" for row in fuzzy_rows[:3])
            return f"匹配到多家酒店，请进一步确认酒店名或城市：{options}。"

        city_text = f"{normalized_city} " if normalized_city else ""
        return f"未找到酒店：{city_text}{normalized_hotel_name}。"

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
        stay_nights: int,
        exclude_order_id: int | None = None,
    ):
        sql = """
            SELECT id, quantity, stay_nights
            FROM orders
            WHERE user_id = %s
              AND order_type = 'hotel'
              AND status = 'booked'
              AND departure_city = %s
              AND hotel_name = %s
              AND ticket_or_room_type = %s
              AND DATE(departure_time) = %s
              AND stay_nights = %s
        """
        params: list[object] = [user_id, city, hotel_name, room_type, check_in_date, stay_nights]
        if exclude_order_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_order_id)
        sql += " LIMIT 1"
        cursor.execute(sql, params)
        return cursor.fetchone()

    def _find_single_booked_hotel_order(
        self,
        *,
        cursor,
        user_id: int,
        city: str = "",
        hotel_name: str = "",
        room_type: str = "",
        check_in_date: str = "",
        for_update: bool = False,
    ):
        filters = ["user_id = %s", "order_type = 'hotel'", "status = 'booked'"]
        params: list[object] = [user_id]
        if city:
            filters.append("departure_city = %s")
            params.append(city)
        if hotel_name:
            filters.append("hotel_name = %s")
            params.append(hotel_name)
        if room_type:
            filters.append("ticket_or_room_type = %s")
            params.append(room_type)
        if check_in_date:
            filters.append("DATE(departure_time) = %s")
            params.append(check_in_date)

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
            return "未找到符合条件的已预订酒店订单，请补充更具体的酒店名、入住日期或房型。"
        if len(orders) > 1:
            return "匹配到多条酒店订单，请补充更具体的酒店名、入住日期或房型信息。"
        return orders[0]

    def _restore_hotel_inventory(self, cursor, order: dict) -> None:
        stay_dates = self._build_stay_dates(str(order["departure_time"])[:10], int(order["stay_nights"]))
        placeholders = ", ".join(["%s"] * len(stay_dates))
        cursor.execute(
            f"""
            UPDATE hotel_room_inventory i
            JOIN hotels h ON h.id = i.hotel_id
            SET i.remaining_rooms = i.remaining_rooms + %s
            WHERE h.city = %s
              AND h.name = %s
              AND i.room_type = %s
              AND i.stay_date IN ({placeholders})
            """,
            [
                int(order["quantity"]),
                order["departure_city"],
                order["hotel_name"],
                order["ticket_or_room_type"],
                *stay_dates,
            ],
        )

    @staticmethod
    def _deduct_inventory_rows(cursor, inventory_rows: list[dict], rooms: int) -> None:
        for row in inventory_rows:
            cursor.execute(
                """
                UPDATE hotel_room_inventory
                SET remaining_rooms = remaining_rooms - %s
                WHERE id = %s
                """,
                (rooms, row["id"]),
            )

    @staticmethod
    def _format_price_summary(inventory_rows: list[dict], rooms: int) -> str:
        nightly_prices = [Decimal(str(row["price_per_night"])) * rooms for row in inventory_rows]
        if not nightly_prices:
            return "价格待确认"
        if len(nightly_prices) == 1:
            return f"每晚 {nightly_prices[0]} 元"
        joined = " + ".join(str(price) for price in nightly_prices)
        return f"分晚房费 {joined}"

    @staticmethod
    def _ensure_inventory_enough(inventory_rows: list[dict], rooms: int, hotel_name: str, room_type: str) -> str:
        for row in inventory_rows:
            if row["remaining_rooms"] < rooms:
                return (
                    f"余房不足：{hotel_name} {room_type} 在 {row['stay_date']} "
                    f"仅剩 {row['remaining_rooms']} 间。"
                )
        return ""

    @staticmethod
    def _total_price_for_inventory(inventory_rows: list[dict], rooms: int) -> Decimal:
        total = Decimal("0")
        for row in inventory_rows:
            total += Decimal(str(row["price_per_night"]))
        return total * rooms

    @staticmethod
    def _build_stay_dates(check_in_date: str, nights: int) -> list[str]:
        return [
            (datetime.strptime(check_in_date, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(nights)
        ]

    @staticmethod
    def _insert_hotel_order(
        *,
        cursor,
        user_id: int,
        city: str,
        hotel_name: str,
        room_type: str,
        check_in_date: str,
        nights: int,
        rooms: int,
        unit_price: Decimal,
        total_price: Decimal,
        raw_payload: dict,
    ) -> None:
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
                json.dumps(raw_payload, ensure_ascii=False),
            ),
        )

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


@hotel_mcp.tool(
    name="cancel_hotel_order",
    description="根据用户名和订单特征取消已预订酒店订单，并自动回补库存"
)
def cancel_hotel_order(
    username: str,
    city: str = "",
    hotel_name: str = "",
    room_type: str = "",
    check_in_date: str = "",
) -> str:
    logger.info(f"正在取消酒店订单: {username}, {city}, {hotel_name}, {room_type}, {check_in_date}")
    return service.cancel_hotel_order(username, city, hotel_name, room_type, check_in_date)


@hotel_mcp.tool(
    name="change_hotel_order",
    description="根据用户名和当前订单信息执行酒店改期/改订，会回补原库存并扣减目标库存"
)
def change_hotel_order(
    username: str,
    current_city: str = "",
    current_hotel_name: str = "",
    current_room_type: str = "",
    current_check_in_date: str = "",
    new_city: str = "",
    new_hotel_name: str = "",
    new_room_type: str = "",
    new_check_in_date: str = "",
    new_nights: int = 0,
) -> str:
    logger.info(
        "正在酒店改期: "
        f"{username}, {current_city}, {current_hotel_name}, {current_room_type}, {current_check_in_date}, "
        f"{new_city}, {new_hotel_name}, {new_room_type}, {new_check_in_date}, {new_nights}"
    )
    return service.change_hotel_order(
        username=username,
        current_city=current_city,
        current_hotel_name=current_hotel_name,
        current_room_type=current_room_type,
        current_check_in_date=current_check_in_date,
        new_city=new_city,
        new_hotel_name=new_hotel_name,
        new_room_type=new_room_type,
        new_check_in_date=new_check_in_date,
        new_nights=new_nights,
    )


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
