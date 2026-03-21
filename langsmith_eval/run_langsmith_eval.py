import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from langsmith import Client, schemas
from langsmith.evaluation import evaluate

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import Config
from utils.db import get_db_connection
from utils.orchestrator import SmartVoyageOrchestrator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT / "langsmith_eval" / "cases.json"
DEFAULT_SIDE_EFFECT_CASES_PATH = ROOT / "langsmith_eval" / "side_effect_cases.json"
DEFAULT_DATASET_NAME = "SmartVoyage Regression"
DEFAULT_SIDE_EFFECT_DATASET_NAME = "SmartVoyage Side Effect Regression"
EVAL_OPTIONS: dict[str, Any] = {}
RESET_DB_SCRIPT_NAME = "reset_database.py"
RESET_DB_SKIP_STOP_FLAG = "--skip-stop-services"
ALLOWED_SNAPSHOT_TABLES = {"orders", "train_tickets", "flight_tickets", "hotel_room_inventory"}


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path.name} must be a list")
    return data


def build_dataset_examples(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for case in cases:
        examples.append(
            {
                "inputs": {
                    "case_id": case["id"],
                    "description": case.get("description", ""),
                    "turns": case["turns"],
                    "side_effect": case.get("side_effect", False),
                    "preconditions": case.get("preconditions", {}),
                    "setup_profile": case.get("setup_profile", "baseline"),
                    "db_assertions": case.get("db_assertions", {}),
                    "expected_db_changes": case.get("expected_db_changes", {}),
                    "reset_strategy": case.get("reset_strategy", ""),
                },
                "outputs": case.get("expected", {}),
                "metadata": {
                    "case_id": case["id"],
                    "description": case.get("description", ""),
                    "side_effect": case.get("side_effect", False),
                    "setup_profile": case.get("setup_profile", "baseline"),
                    "reset_strategy": case.get("reset_strategy", ""),
                },
            }
        )
    return examples


def sync_dataset(
    client: Client,
    dataset_name: str,
    cases: list[dict[str, Any]],
    *,
    replace: bool = True,
    description: str,
) -> None:
    existing = next(client.list_datasets(dataset_name=dataset_name, limit=1), None)
    if existing and replace:
        client.delete_dataset(dataset_id=existing.id)
        existing = None
    elif existing and not replace:
        print(
            f"[langsmith] dataset '{dataset_name}' already exists; new examples will be appended. "
            "Use --replace-dataset to avoid duplicate cases."
        )

    if not existing:
        client.create_dataset(
            dataset_name,
            description=description,
            data_type=schemas.DataType.kv,
        )

    client.create_examples(
        dataset_name=dataset_name,
        examples=build_dataset_examples(cases),
    )


def maybe_run_reset_hook(case_inputs: dict[str, Any]) -> None:
    reset_command = str(EVAL_OPTIONS.get("db_reset_command", "") or "").strip()
    if not reset_command:
        return
    if not case_inputs.get("side_effect"):
        return
    if not EVAL_OPTIONS.get("reset_before_case"):
        return

    case_id = case_inputs.get("case_id", "unknown")
    print(f"[side-effect reset] running before case: {case_id}")
    normalized_command = _normalize_reset_command(reset_command)
    subprocess.run(normalized_command, shell=True, check=True, cwd=str(ROOT))


def _normalize_reset_command(reset_command: str) -> str:
    normalized = reset_command.strip()
    lower = normalized.lower()
    if RESET_DB_SCRIPT_NAME in lower and RESET_DB_SKIP_STOP_FLAG not in lower:
        print(
            "[side-effect reset] detected bundled reset script; "
            "auto-appending --skip-stop-services to keep backend services available during evaluation."
        )
        return f"{normalized} {RESET_DB_SKIP_STOP_FLAG}"
    return normalized


def _should_capture_db_state(case_inputs: dict[str, Any]) -> bool:
    return bool(case_inputs.get("side_effect") and (case_inputs.get("db_assertions") or {}))


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in row.items()}


def _match_filter_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        op = str(expected.get("op", "equals"))
        value = expected.get("value")
        if op == "equals":
            return actual == value
        if op == "startswith":
            return str(actual).startswith(str(value))
        if op == "in":
            return actual in list(value or [])
        raise ValueError(f"Unsupported filter op: {op}")
    return actual == expected


def _row_matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, expected in (filters or {}).items():
        if not _match_filter_value(row.get(key), expected):
            return False
    return True


def _count_matching_rows(rows: list[dict[str, Any]], filters: dict[str, Any]) -> int:
    return sum(1 for row in rows if _row_matches(row, filters))


def _sum_matching_field(rows: list[dict[str, Any]], filters: dict[str, Any], field: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        if _row_matches(row, filters):
            total += Decimal(str(row.get(field, 0) or 0))
    return total


def _safe_table_name(table: str) -> str:
    if table not in ALLOWED_SNAPSHOT_TABLES:
        raise ValueError(f"Unsupported snapshot table: {table}")
    return table


def capture_db_snapshot(conf: Config, case_inputs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if not _should_capture_db_state(case_inputs):
        return {}

    db_assertions = case_inputs.get("db_assertions") or {}
    tables: set[str] = set()
    for assertion in db_assertions.get("row_count_deltas", []):
        tables.add(_safe_table_name(str(assertion["table"])))
    for assertion in db_assertions.get("field_deltas", []):
        tables.add(_safe_table_name(str(assertion["table"])))

    snapshot: dict[str, list[dict[str, Any]]] = {}
    conn = get_db_connection(conf)
    cursor = conn.cursor(dictionary=True)
    try:
        for table in sorted(tables):
            cursor.execute(f"SELECT * FROM {table}")
            snapshot[table] = [_serialize_row(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()
    return snapshot


def _get_or_create_user_id(cursor, conf: Config, username: str) -> int:
    cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
    row = cursor.fetchone()
    if row:
        return int(row["id"])
    cursor.execute(
        "INSERT INTO users (username, phone) VALUES (%s, %s)",
        (username, conf.default_user_phone),
    )
    return int(cursor.lastrowid)


def _seed_train_order(conf: Config, username: str) -> None:
    conn = get_db_connection(conf)
    cursor = conn.cursor(dictionary=True)
    try:
        conn.start_transaction()
        user_id = _get_or_create_user_id(cursor, conf, username)
        cursor.execute(
            """
            SELECT id, departure_city, arrival_city, departure_time, train_number, seat_type, price, remaining_seats
            FROM train_tickets
            WHERE departure_time = %s AND train_number = %s AND seat_type = %s
            FOR UPDATE
            """,
            ("2026-03-21 07:00:00", "G5", "二等座"),
        )
        ticket = cursor.fetchone()
        if not ticket:
            raise ValueError("seed_train_order requires train ticket G5 2026-03-21 二等座")

        cursor.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id = %s
              AND order_type = 'train'
              AND status = 'booked'
              AND departure_time = %s
              AND transport_no = %s
              AND ticket_or_room_type = %s
            LIMIT 1
            """,
            (user_id, ticket["departure_time"], ticket["train_number"], ticket["seat_type"]),
        )
        if cursor.fetchone():
            conn.commit()
            return

        if int(ticket["remaining_seats"]) < 1:
            raise ValueError("seed_train_order requires remaining seats >= 1")

        cursor.execute(
            "UPDATE train_tickets SET remaining_seats = remaining_seats - 1 WHERE id = %s",
            (ticket["id"],),
        )
        payload = {
            "seed_profile": "seed_train_order",
            "username": username,
            "transport_no": ticket["train_number"],
            "ticket_type": ticket["seat_type"],
            "quantity": 1,
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
            ) VALUES (%s, 'train', 'booked', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                ticket["departure_city"],
                ticket["arrival_city"],
                ticket["departure_time"],
                ticket["seat_type"],
                ticket["train_number"],
                1,
                Decimal(str(ticket["price"])),
                Decimal(str(ticket["price"])),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _seed_hotel_order(conf: Config, username: str) -> None:
    conn = get_db_connection(conf)
    cursor = conn.cursor(dictionary=True)
    try:
        conn.start_transaction()
        user_id = _get_or_create_user_id(cursor, conf, username)
        cursor.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id = %s
              AND order_type = 'hotel'
              AND status = 'booked'
              AND hotel_name = %s
              AND ticket_or_room_type = %s
              AND DATE(departure_time) = %s
              AND stay_nights = %s
            LIMIT 1
            """,
            (user_id, "上海外滩云际酒店", "高级大床房", "2026-03-21", 2),
        )
        if cursor.fetchone():
            conn.commit()
            return

        cursor.execute(
            "SELECT id, city, name FROM hotels WHERE city = %s AND name = %s LIMIT 1",
            ("上海", "上海外滩云际酒店"),
        )
        hotel = cursor.fetchone()
        if not hotel:
            raise ValueError("seed_hotel_order requires 上海外滩云际酒店 in baseline data")

        cursor.execute(
            """
            SELECT id, stay_date, price_per_night, remaining_rooms
            FROM hotel_room_inventory
            WHERE hotel_id = %s
              AND room_type = %s
              AND stay_date IN (%s, %s)
            ORDER BY stay_date ASC
            FOR UPDATE
            """,
            (hotel["id"], "高级大床房", "2026-03-21", "2026-03-22"),
        )
        inventory_rows = cursor.fetchall()
        if len(inventory_rows) != 2:
            raise ValueError("seed_hotel_order requires two nightly inventory rows")
        if any(int(row["remaining_rooms"]) < 1 for row in inventory_rows):
            raise ValueError("seed_hotel_order requires remaining rooms >= 1 for all nights")

        for row in inventory_rows:
            cursor.execute(
                "UPDATE hotel_room_inventory SET remaining_rooms = remaining_rooms - 1 WHERE id = %s",
                (row["id"],),
            )

        unit_price = Decimal(str(inventory_rows[0]["price_per_night"]))
        total_price = sum(Decimal(str(row["price_per_night"])) for row in inventory_rows)
        payload = {
            "seed_profile": "seed_hotel_order",
            "username": username,
            "hotel_name": hotel["name"],
            "room_type": "高级大床房",
            "check_in_date": "2026-03-21",
            "nights": 2,
            "rooms": 1,
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
                hotel["city"],
                hotel["city"],
                "2026-03-21 14:00:00",
                "高级大床房",
                hotel["name"],
                hotel["name"],
                2,
                1,
                unit_price,
                total_price,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def maybe_apply_setup_profile(conf: Config, case_inputs: dict[str, Any]) -> None:
    if not case_inputs.get("side_effect"):
        return

    profile = str(case_inputs.get("setup_profile", "baseline") or "baseline").strip()
    if profile in {"", "baseline"}:
        return

    preconditions = case_inputs.get("preconditions") or {}
    username = str(preconditions.get("user") or conf.default_username)
    print(f"[side-effect setup] applying setup_profile={profile} for user={username}")

    if profile == "seed_train_order":
        _seed_train_order(conf, username)
        return
    if profile == "seed_hotel_order":
        _seed_hotel_order(conf, username)
        return
    raise ValueError(f"Unsupported setup_profile: {profile}")


def run_case(turns: list[str], case_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    case_inputs = case_inputs or {}
    maybe_run_reset_hook(case_inputs)

    conf = Config()
    maybe_apply_setup_profile(conf, case_inputs)
    db_before = capture_db_snapshot(conf, case_inputs)

    orchestrator = SmartVoyageOrchestrator(conf)
    conversation_history = ""
    pending_context: dict[str, Any] = {}
    last_result: dict[str, Any] = {}

    for prompt in turns:
        conversation_history += f"\nUser: {prompt}"
        result = orchestrator.process_user_input(prompt, conversation_history, pending_context)
        response = result["response"]
        pending_context = result.get("pending_context", {}) or {}
        conversation_history += f"\nAssistant: {response}"
        last_result = result

    db_after = capture_db_snapshot(conf, case_inputs)
    return {
        "response": last_result.get("response", ""),
        "intents": last_result.get("intents", []),
        "routed_agents": last_result.get("routed_agents", []),
        "pending_context": pending_context,
        "conversation_history": conversation_history.strip(),
        "db_before": db_before,
        "db_after": db_after,
    }


def target(inputs: dict[str, Any]) -> dict[str, Any]:
    return run_case(inputs["turns"], case_inputs=inputs)


def _score_bool(name: str, passed: bool, comment: str) -> dict[str, Any]:
    return {
        "key": name,
        "score": 1 if passed else 0,
        "comment": comment,
    }


def intent_match(inputs=None, outputs=None, reference_outputs=None, **kwargs):
    expected = (reference_outputs or {}).get("intents")
    if not expected:
        return _score_bool("intent_match", True, "No expected intents configured.")
    actual = outputs.get("intents", []) if outputs else []
    passed = actual == expected
    return _score_bool("intent_match", passed, f"expected={expected}, actual={actual}")


def route_match(inputs=None, outputs=None, reference_outputs=None, **kwargs):
    expected_all = (reference_outputs or {}).get("routed_agents")
    expected_any = (reference_outputs or {}).get("routed_agents_any")
    actual = outputs.get("routed_agents", []) if outputs else []
    if expected_all:
        passed = actual == expected_all
        return _score_bool("route_match", passed, f"expected_all={expected_all}, actual={actual}")
    if expected_any:
        passed = all(item in actual for item in expected_any)
        return _score_bool("route_match", passed, f"expected_any={expected_any}, actual={actual}")
    return _score_bool("route_match", True, "No expected routes configured.")


def response_keywords_match(inputs=None, outputs=None, reference_outputs=None, **kwargs):
    actual_text = (outputs or {}).get("response", "")
    expected_all = (reference_outputs or {}).get("response_keywords_all", [])
    expected_any = (reference_outputs or {}).get("response_keywords_any", [])
    expected_none = (reference_outputs or {}).get("response_keywords_none", [])

    all_ok = all(keyword in actual_text for keyword in expected_all)
    any_ok = True if not expected_any else any(keyword in actual_text for keyword in expected_any)
    none_ok = all(keyword not in actual_text for keyword in expected_none)
    passed = all_ok and any_ok and none_ok
    return _score_bool(
        "response_keywords_match",
        passed,
        f"all={expected_all}, any={expected_any}, none={expected_none}, actual={actual_text}",
    )


def pending_domain_match(inputs=None, outputs=None, reference_outputs=None, **kwargs):
    expected_domain = (reference_outputs or {}).get("pending_domain", "")
    expected_empty = (reference_outputs or {}).get("pending_empty")
    pending = (outputs or {}).get("pending_context", {}) or {}
    actual_domain = pending.get("domain", "")
    is_empty = not bool(pending)

    passed = True
    reasons: list[str] = []
    if expected_domain != "":
        passed = passed and actual_domain == expected_domain
        reasons.append(f"expected_domain={expected_domain}, actual_domain={actual_domain}")
    if expected_empty is not None:
        passed = passed and (is_empty == expected_empty)
        reasons.append(f"expected_empty={expected_empty}, actual_empty={is_empty}")
    if not reasons:
        reasons.append("No pending expectations configured.")
    return _score_bool("pending_domain_match", passed, "; ".join(reasons))


def db_state_match(inputs=None, outputs=None, reference_outputs=None, **kwargs):
    db_assertions = (inputs or {}).get("db_assertions") or {}
    if not db_assertions:
        return _score_bool("db_state_match", True, "No DB assertions configured.")

    before = (outputs or {}).get("db_before") or {}
    after = (outputs or {}).get("db_after") or {}
    if not before and not after:
        return _score_bool("db_state_match", False, "DB assertions exist but no DB snapshots were captured.")

    passed = True
    comments: list[str] = []

    for assertion in db_assertions.get("row_count_deltas", []):
        table = str(assertion["table"])
        filters = assertion.get("filter", {})
        expected_delta = int(assertion["delta"])
        label = assertion.get("label") or f"{table} row_count"
        before_count = _count_matching_rows(before.get(table, []), filters)
        after_count = _count_matching_rows(after.get(table, []), filters)
        actual_delta = after_count - before_count
        comments.append(
            f"{label}: before={before_count}, after={after_count}, expected_delta={expected_delta}, actual_delta={actual_delta}"
        )
        if actual_delta != expected_delta:
            passed = False

    for assertion in db_assertions.get("field_deltas", []):
        table = str(assertion["table"])
        filters = assertion.get("filter", {})
        field = str(assertion["field"])
        expected_delta = Decimal(str(assertion["delta"]))
        label = assertion.get("label") or f"{table}.{field}"
        before_value = _sum_matching_field(before.get(table, []), filters, field)
        after_value = _sum_matching_field(after.get(table, []), filters, field)
        actual_delta = after_value - before_value
        comments.append(
            f"{label}: before={before_value}, after={after_value}, expected_delta={expected_delta}, actual_delta={actual_delta}"
        )
        if actual_delta != expected_delta:
            passed = False

    if not comments:
        comments.append("DB assertions configured but empty.")
    return _score_bool("db_state_match", passed, " | ".join(comments))


def main():
    parser = argparse.ArgumentParser(description="Run SmartVoyage LangSmith evaluations without pytest.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to baseline cases.json")
    parser.add_argument(
        "--side-effect-cases",
        default=str(DEFAULT_SIDE_EFFECT_CASES_PATH),
        help="Path to side_effect_cases.json",
    )
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME, help="LangSmith baseline dataset name")
    parser.add_argument(
        "--side-effect-dataset-name",
        default=DEFAULT_SIDE_EFFECT_DATASET_NAME,
        help="LangSmith side effect dataset name",
    )
    parser.add_argument("--sync-dataset", action="store_true", help="Create/replace baseline dataset in LangSmith")
    parser.add_argument(
        "--sync-side-effect-dataset",
        action="store_true",
        help="Create/replace side effect dataset in LangSmith",
    )
    parser.add_argument("--run", action="store_true", help="Run evaluation experiment on the selected dataset name")
    parser.add_argument("--replace-dataset", action="store_true", help="Replace dataset if it already exists")
    parser.add_argument("--max-concurrency", type=int, default=0, help="Max LangSmith evaluation concurrency")
    parser.add_argument(
        "--db-reset-command",
        default="",
        help="Optional shell command used as a future DB reset hook for side-effect cases.",
    )
    parser.add_argument(
        "--reset-before-case",
        action="store_true",
        help="If set with --db-reset-command, run the reset hook before each side-effect case.",
    )
    args = parser.parse_args()

    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is not set.")

    global EVAL_OPTIONS
    EVAL_OPTIONS = {
        "db_reset_command": args.db_reset_command,
        "reset_before_case": args.reset_before_case,
    }

    client = Client()

    if args.sync_dataset:
        baseline_cases = load_cases(Path(args.cases))
        sync_dataset(
            client,
            args.dataset_name,
            baseline_cases,
            replace=args.replace_dataset,
            description="SmartVoyage core regression dataset without side effects.",
        )
        print(f"Dataset synced: {args.dataset_name}")

    if args.sync_side_effect_dataset:
        side_effect_cases = load_cases(Path(args.side_effect_cases))
        sync_dataset(
            client,
            args.side_effect_dataset_name,
            side_effect_cases,
            replace=args.replace_dataset,
            description="SmartVoyage side effect regression dataset with DB assertions.",
        )
        print(f"Dataset synced: {args.side_effect_dataset_name}")

    if args.run:
        results = evaluate(
            target,
            data=args.dataset_name,
            evaluators=[
                intent_match,
                route_match,
                response_keywords_match,
                pending_domain_match,
                db_state_match,
            ],
            experiment_prefix="smartvoyage-regression",
            description="SmartVoyage regression suite driven by LangSmith dataset.",
            max_concurrency=args.max_concurrency,
            client=client,
            blocking=True,
            upload_results=True,
        )
        print(results)

    if not args.sync_dataset and not args.sync_side_effect_dataset and not args.run:
        print("Nothing to do. Use --sync-dataset, --sync-side-effect-dataset and/or --run.")


if __name__ == "__main__":
    main()
