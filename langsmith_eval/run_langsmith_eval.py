from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import lru_cache
import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import mysql.connector
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from langsmith.evaluation import evaluate
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).with_name("cases.json")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
sys.path.insert(0, str(ROOT / "mcp_server"))
from mcp_order_server import OrderService  # noqa: E402
from agents.supervisor import SmartVoyageSupervisor  # noqa: E402
from utils.resilient_llm import ResilientModelInvoker  # noqa: E402


DEFAULT_DATASET_NAME = "SmartVoyage Regression"
MCP_ENDPOINTS = [
    ("TravelReadTools", "127.0.0.1", 8001),
    ("OrderTools", "127.0.0.1", 8003),
]


def assert_mcp_services_available() -> None:
    unavailable: list[str] = []
    for name, host, port in MCP_ENDPOINTS:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                pass
        except OSError as exc:
            unavailable.append(f"{name}({host}:{port}): {exc}")
    if unavailable:
        detail = " | ".join(unavailable)
        raise RuntimeError(
            "未检测到必需的 MCP 服务，请先启动 `run_all.py`。"
            f" 连接检查失败: {detail}"
        )


RESPONSE_SEMANTIC_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是 SmartVoyage 回归测试的严格评审。"
            "你的任务是判断 assistant 的最终回复在语义上是否满足测试目标，而不是要求字面完全一致。"
            "请重点判断："
            "1. 是否回答了用户问题，或在信息不足时给出了正确的追问；"
            "2. 是否覆盖了关键业务事实；"
            "3. 是否没有明显编造与任务无关的事实；"
            "4. 日期、时间、措辞、语序的等价变化应视为正确；"
            "5. 不要重新评估路由、数据库副作用或 pending context，这些由其他 evaluator 单独负责。"
            "6. 如果案例是 transport_decision，只要回复完整给出‘天气判断/出行建议/票务结果’三部分，就应视为满足主目标；"
            "7. transport_decision 允许出现‘协作降级’或‘天气数据提醒’前缀，只要后续建议仍然基于保守策略且没有违背用户“不要下单”的要求，就应判定为通过；"
            "8. 如果回复只是服务不可用或失败提示，而案例本应完成查询/建议，则应判定为失败。"
            "如果输出只是轻微措辞不同，但任务目标已经满足，应判定为通过。"
            "请返回结构化结果。",
        ),
        (
            "human",
            "案例 ID: {case_id}\n"
            "案例描述: {description}\n"
            "用户输入轮次: {turns}\n"
            "参考意图: {expected_intents}\n"
            "参考路由: {expected_routes}\n"
            "参考必含关键词: {keywords_all}\n"
            "参考任选关键词: {keywords_any}\n"
            "assistant 实际回复: {actual_response}\n"
            "请判断该回复是否在语义上满足案例目标，并给出简洁理由。",
        ),
    ]
)


class SemanticJudgeResult(BaseModel):
    passed: bool = Field(description="语义上是否通过")
    confidence: float = Field(description="0 到 1 之间的置信度")
    rationale: str = Field(description="简洁说明判断依据")


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
        "now_override": case.get("now_override", ""),
        "auto_approve_hitl": bool(case.get("auto_approve_hitl", case.get("side_effect", False))),
    }


def build_reference_output(case: dict[str, Any]) -> dict[str, Any]:
    return case["reference_output"]


@lru_cache(maxsize=1)
def get_semantic_judge() -> ResilientModelInvoker:
    return ResilientModelInvoker(Config(), temperature=0.0)


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


@contextmanager
def case_now_override(case_inputs: dict[str, Any]):
    key = "SMARTVOYAGE_NOW_OVERRIDE"
    override = str(case_inputs.get("now_override", "")).strip()
    previous = os.getenv(key)
    if override:
        os.environ[key] = override
    else:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


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
    case_inputs = case_inputs or {}
    with case_now_override(case_inputs):
        config = Config()
        if case_inputs.get("side_effect"):
            reset_database(config)
            seed_case_setup(case_inputs, config)

        db_metrics_before = capture_db_metrics(case_inputs, config) if case_inputs.get("side_effect") else {}

        orchestrator = SmartVoyageSupervisor(config)
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

            if case_inputs.get("auto_approve_hitl") and pending_order_context.get("action") == "hitl_review":
                approval_turn = "yes"
                result = orchestrator.process_user_input(
                    approval_turn,
                    conversation_history,
                    pending_order_context,
                )
                assistant_response = result["response"]
                conversation_history += f"\nUser: {approval_turn}\nAssistant: {assistant_response}"
                pending_order_context = result.get("pending_order_context", {}) or {}

        assert result is not None

        db_metrics_after = capture_db_metrics(case_inputs, config) if case_inputs.get("side_effect") else {}

        return {
            "response": result["response"],
            "intents": result.get("intents", []),
            "routed_agents": result.get("routed_agents", []),
            "pending_empty": not bool(result.get("pending_order_context")),
            "pending_order_context": result.get("pending_order_context", {}) or {},
            "metrics": result.get("metrics", {}) or {},
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


def _response_semantic_precheck(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
) -> dict[str, Any] | None:
    intents = inputs.get("case_id", "")
    response = str(outputs.get("response", ""))

    if response.startswith("交通读取服务当前不可用") or response.startswith("订单服务当前不可用"):
        return {
            "key": "response_semantic_match",
            "score": 0,
            "comment": "explicit service unavailable response",
        }

    if intents in {"base_009_transport_decision_read_only", "base_013_transport_decision_tomorrow_read_only"}:
        required_sections = ["天气判断：", "出行建议：", "票务结果："]
        if all(section in response for section in required_sections):
            return {
                "key": "response_semantic_match",
                "score": 1,
                "comment": "deterministic pass: transport_decision response contains all required sections",
            }

    return None


def response_semantic_match(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    precheck = _response_semantic_precheck(inputs, outputs)
    if precheck is not None:
        return precheck

    payload = {
        "case_id": inputs.get("case_id", ""),
        "description": inputs.get("description", ""),
        "turns": json.dumps(inputs.get("turns", []), ensure_ascii=False),
        "expected_intents": json.dumps(reference_outputs.get("intents", []), ensure_ascii=False),
        "expected_routes": json.dumps(reference_outputs.get("routed_agents_any", []), ensure_ascii=False),
        "keywords_all": json.dumps(reference_outputs.get("response_keywords_all", []), ensure_ascii=False),
        "keywords_any": json.dumps(reference_outputs.get("response_keywords_any", []), ensure_ascii=False),
        "actual_response": outputs.get("response", ""),
    }

    try:
        judge_result = get_semantic_judge().invoke_structured(
            RESPONSE_SEMANTIC_JUDGE_PROMPT,
            SemanticJudgeResult,
            payload,
            description="LangSmith response semantic judge",
        )
    except Exception as exc:
        return {
            "key": "response_semantic_match",
            "score": 0,
            "comment": f"judge_error={exc}",
        }

    score = 1 if judge_result.passed else 0
    return {
        "key": "response_semantic_match",
        "score": score,
        "comment": f"confidence={judge_result.confidence:.2f}; {judge_result.rationale}",
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

    evaluators = [
        intent_match,
        route_match,
        response_semantic_match,
        pending_context_match,
        db_state_match,
    ]

    results = evaluate(
        target,
        data=dataset_name,
        evaluators=evaluators,
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
        assert_mcp_services_available()
        run_dataset(args.dataset_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
