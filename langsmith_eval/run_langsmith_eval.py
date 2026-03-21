import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from langsmith import Client, schemas
from langsmith.evaluation import evaluate

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import Config
from utils.orchestrator import SmartVoyageOrchestrator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT / "langsmith_eval" / "cases.json"
DEFAULT_DATASET_NAME = "SmartVoyage Regression"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("cases.json must be a list")
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
                },
                "outputs": case.get("expected", {}),
                "metadata": {
                    "case_id": case["id"],
                    "description": case.get("description", ""),
                },
            }
        )
    return examples


def sync_dataset(client: Client, dataset_name: str, cases: list[dict[str, Any]], replace: bool = True) -> None:
    existing = next(client.list_datasets(dataset_name=dataset_name, limit=1), None)
    if existing and replace:
        client.delete_dataset(dataset_id=existing.id)
        existing = None

    if not existing:
        client.create_dataset(
            dataset_name,
            description="SmartVoyage 手工回归与 travel_plan 联动评测集",
            data_type=schemas.DataType.kv,
        )

    client.create_examples(
        dataset_name=dataset_name,
        examples=build_dataset_examples(cases),
    )


def run_case(turns: list[str]) -> dict[str, Any]:
    conf = Config()
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

    return {
        "response": last_result.get("response", ""),
        "intents": last_result.get("intents", []),
        "routed_agents": last_result.get("routed_agents", []),
        "pending_context": pending_context,
        "conversation_history": conversation_history.strip(),
    }


def target(inputs: dict[str, Any]) -> dict[str, Any]:
    return run_case(inputs["turns"])


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


def main():
    parser = argparse.ArgumentParser(description="Run SmartVoyage LangSmith evaluations without pytest.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to cases.json")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME, help="LangSmith dataset name")
    parser.add_argument("--sync-dataset", action="store_true", help="Create/replace dataset in LangSmith")
    parser.add_argument("--run", action="store_true", help="Run evaluation experiment on LangSmith")
    parser.add_argument("--replace-dataset", action="store_true", help="Replace dataset if it already exists")
    parser.add_argument("--max-concurrency", type=int, default=0, help="Max LangSmith evaluation concurrency")
    args = parser.parse_args()

    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is not set.")

    cases = load_cases(Path(args.cases))
    client = Client()

    if args.sync_dataset:
        sync_dataset(client, args.dataset_name, cases, replace=True)
        print(f"Dataset synced: {args.dataset_name}")

    if args.run:
        results = evaluate(
            target,
            data=args.dataset_name,
            evaluators=[
                intent_match,
                route_match,
                response_keywords_match,
                pending_domain_match,
            ],
            experiment_prefix="smartvoyage-regression",
            description="SmartVoyage regression suite driven by LangSmith dataset.",
            max_concurrency=args.max_concurrency,
            client=client,
            blocking=True,
            upload_results=True,
        )
        print(results)

    if not args.sync_dataset and not args.run:
        print("Nothing to do. Use --sync-dataset and/or --run.")


if __name__ == "__main__":
    main()

