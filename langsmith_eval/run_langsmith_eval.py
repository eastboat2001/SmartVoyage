from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import mysql.connector
from langsmith import Client
from langsmith.evaluation import evaluate


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).with_name("cases.json")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
sys.path.insert(0, str(ROOT / "mcp_server"))
from mcp_order_server import OrderService  # noqa: E402
from utils.orchestrator import SmartVoyageOrchestrator  # noqa: E402


DEFAULT_DATASET_NAME = "SmartVoyage Regression"


def load_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_inputs(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "description": case.get("description", ""),
        "turns": case["turns"],
        "side_effect": bool(case.get("side_effect", False)),
        "setup_profile": case.get("setup_profile", "baseline"),
        "db_assertions": case.get("db_assertions", {}),
    }


def build_reference_output(case: dict[str, Any]) -> dict[str, Any]:
    return case["reference_output"]


def find_dataset(client: Client, dataset_name: str):
    datasets = list(client.list_datasets(dataset_name=dataset_name))
    return datasets[0] if datasets else None


def sync_dataset(dataset_name: str, replace_dataset: bool) -> None:
    client = Client()
    existing = find_dataset(client, dataset_name)
    if existing and replace_dataset:
        client.delete_dataset(dataset_id=existing.id)
        existing = None

    if existing:
        print(f"[langsmith] dataset 已存在：{dataset_name}")
        print("[langsmith] 若需要重新同步，请加 --replace-dataset。")
        return

    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="SmartVoyage 混合基础回归集（无副作用 + 副作用）",
    )
    cases = load_cases()
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": build_inputs(case),
                "outputs": build_reference_output(case),
            }
            for case in cases
        ],
    )
    print(f"[langsmith] dataset 已同步：{dataset_name}，共 {len(cases)} 条样例。")


def parse_sql_statements(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    statements: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        current.append(raw_line)
        if line.endswith(";"):
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    if current:
        statement = "\n".join(current).strip()
        if statement:
            statements.append(statement)
    return statements


def reset_database(config: Config) -> None:
    create_sql = ROOT / "sql" / "create_table.sql"
    insert_sql = ROOT / "sql" / "insert_data.sql"
    conn = mysql.connector.connect(
        host=config.host,
        user=config.user,
        password=config.password,
        charset="utf8mb4",
    )
    try:
        cursor = conn.cursor()
        for statement in parse_sql_statements(create_sql):
            cursor.execute(statement)
        for statement in parse_sql_statements(insert_sql):
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


def seed_case_setup(case_inputs: dict[str, Any], config: Config) -> None:
    profile = case_inputs.get("setup_profile", "baseline")
    if profile == "baseline":
        return

    service = OrderService(config)
    username = config.default_username

    if profile == "seed_train_order":
        result = service.order_train(
            username=username,
            departure_date="2026-03-21",
            train_number="G5",
            seat_type="二等座",
            number=1,
        )
        if "订单号" not in result and "预订成功" not in result:
            raise RuntimeError(f"seed_train_order 失败: {result}")
        return

    raise ValueError(f"不支持的 setup_profile: {profile}")


def _build_where_clause(assertion_filter: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for field, value in assertion_filter.items():
        if isinstance(value, dict):
            op = value.get("op")
            raw_value = value.get("value")
            if op == "startswith":
                clauses.append(f"{field} LIKE %s")
                params.append(f"{raw_value}%")
            else:
                raise ValueError(f"不支持的过滤操作: {op}")
        else:
            clauses.append(f"{field} = %s")
            params.append(value)
    where_clause = " AND ".join(clauses) if clauses else "1=1"
    return where_clause, params


def _query_scalar(config: Config, sql: str, params: list[Any]) -> int | float:
    conn = mysql.connector.connect(
        host=config.host,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        value = cursor.fetchone()[0]
        return 0 if value is None else value
    finally:
        conn.close()


def capture_db_metrics(case_inputs: dict[str, Any], config: Config) -> dict[str, float]:
    assertions = case_inputs.get("db_assertions", {})
    metrics: dict[str, float] = {}

    for item in assertions.get("row_count_deltas", []):
        where_clause, params = _build_where_clause(item.get("filter", {}))
        sql = f"SELECT COUNT(*) FROM {item['table']} WHERE {where_clause}"
        metrics[item["label"]] = float(_query_scalar(config, sql, params))

    for item in assertions.get("field_deltas", []):
        where_clause, params = _build_where_clause(item.get("filter", {}))
        sql = f"SELECT COALESCE(SUM({item['field']}), 0) FROM {item['table']} WHERE {where_clause}"
        metrics[item["label"]] = float(_query_scalar(config, sql, params))

    return metrics


def run_case(turns: list[str], case_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    config = Config()
    case_inputs = case_inputs or {}
    if case_inputs.get("side_effect"):
        reset_database(config)
        seed_case_setup(case_inputs, config)

    db_metrics_before = capture_db_metrics(case_inputs, config) if case_inputs.get("side_effect") else {}

    orchestrator = SmartVoyageOrchestrator(config)
    conversation_history = ""
    pending_order_context: dict[str, Any] = {}
    result: dict[str, Any] | None = None

    for turn in turns:
        result = orchestrator.process_user_input(
            turn,
            conversation_history,
            pending_order_context,
        )
        assistant_response = result["response"]
        conversation_history += f"\nUser: {turn}\nAssistant: {assistant_response}"
        pending_order_context = result.get("pending_order_context", {}) or {}

    assert result is not None

    db_metrics_after = capture_db_metrics(case_inputs, config) if case_inputs.get("side_effect") else {}

    return {
        "response": result["response"],
        "intents": result.get("intents", []),
        "routed_agents": result.get("routed_agents", []),
        "pending_empty": not bool(result.get("pending_order_context")),
        "pending_order_context": result.get("pending_order_context", {}) or {},
        "db_metrics_before": db_metrics_before,
        "db_metrics_after": db_metrics_after,
    }


def intent_match(inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    expected = reference_outputs.get("intents", [])
    actual = outputs.get("intents", [])
    score = 1 if actual == expected else 0
    return {
        "key": "intent_match",
        "score": score,
        "comment": f"expected={expected}, actual={actual}",
    }


def route_match(inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    expected_any = reference_outputs.get("routed_agents_any", [])
    actual = outputs.get("routed_agents", [])
    score = 1 if all(agent in actual for agent in expected_any) else 0
    return {
        "key": "route_match",
        "score": score,
        "comment": f"expected_any={expected_any}, actual={actual}",
    }


def response_keywords_match(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    response = outputs.get("response", "")
    keywords_all = reference_outputs.get("response_keywords_all", [])
    keywords_any = reference_outputs.get("response_keywords_any", [])
    all_ok = all(keyword in response for keyword in keywords_all)
    any_ok = True if not keywords_any else any(keyword in response for keyword in keywords_any)
    score = 1 if all_ok and any_ok else 0
    return {
        "key": "response_keywords_match",
        "score": score,
        "comment": f"all_ok={all_ok}, any_ok={any_ok}",
    }


def pending_context_match(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    expected = bool(reference_outputs.get("pending_empty", True))
    actual = bool(outputs.get("pending_empty", False))
    score = 1 if actual == expected else 0
    return {
        "key": "pending_context_match",
        "score": score,
        "comment": f"expected_pending_empty={expected}, actual_pending_empty={actual}",
    }


def db_state_match(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    if not inputs.get("side_effect"):
        return {
            "key": "db_state_match",
            "score": 1,
            "comment": "non-side-effect case",
        }

    assertions = inputs.get("db_assertions", {})
    before = outputs.get("db_metrics_before", {})
    after = outputs.get("db_metrics_after", {})
    mismatches: list[str] = []

    for item in assertions.get("row_count_deltas", []):
        label = item["label"]
        actual_delta = after.get(label, 0) - before.get(label, 0)
        if actual_delta != item["delta"]:
            mismatches.append(f"{label}: expected {item['delta']}, actual {actual_delta}")

    for item in assertions.get("field_deltas", []):
        label = item["label"]
        actual_delta = after.get(label, 0) - before.get(label, 0)
        if actual_delta != item["delta"]:
            mismatches.append(f"{label}: expected {item['delta']}, actual {actual_delta}")

    return {
        "key": "db_state_match",
        "score": 0 if mismatches else 1,
        "comment": "ok" if not mismatches else "; ".join(mismatches),
    }


def run_dataset(dataset_name: str) -> None:
    experiment_prefix = f"smartvoyage-regression-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return run_case(inputs["turns"], case_inputs=inputs)

    results = evaluate(
        target,
        data=dataset_name,
        evaluators=[
            intent_match,
            route_match,
            response_keywords_match,
            pending_context_match,
            db_state_match,
        ],
        experiment_prefix=experiment_prefix,
    )
    print(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SmartVoyage LangSmith evaluation.")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--sync-dataset", action="store_true")
    parser.add_argument("--replace-dataset", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    if not args.sync_dataset and not args.run:
        parser.error("请至少指定 --sync-dataset 或 --run")

    if args.sync_dataset:
        sync_dataset(args.dataset_name, args.replace_dataset)

    if args.run:
        if not os.getenv("LANGSMITH_API_KEY"):
            raise RuntimeError("未检测到 LANGSMITH_API_KEY，无法运行 LangSmith 评测。")
        run_dataset(args.dataset_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
