"""
功能：实现 SmartVoyage 的主编排器 Supervisor。
作用：负责意图识别、跨域路由、transport_decision 复合流程和 metrics 聚合。
实现方式：结合结构化 LLM 调用、本地子代理调用和显式流程控制完成编排。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agents.order import OrderSubagent
from agents.travel_read import TravelReadSubagent
from core.config import Config
from core.logging import logger
from core.prompts import SmartVoyagePrompts
from contracts.agent_protocol import LocalAgentRequest
from infra.db import get_db_connection
from observability.metrics import clone_metrics, create_metrics, merge_metrics
from observability.request_context import clear_request_id, ensure_request_id
from llm.resilient_llm import ResilientModelInvoker
from contracts.order_action_tag import with_order_action
from contracts.structured_outputs import (
    IntentRecognitionResult,
    TransportDecisionPlanResult,
)
from core.clock import get_current_date_str
from contracts.travel_read_tag import (
    extract_travel_read_kind,
    strip_travel_read_kind,
    with_travel_read_kind,
)

EXPLICIT_TRANSPORT_NO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[GDCZTKYLSP]\d{1,4}|[A-Z]{2}\d{3,4})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


DEFAULT_AGENT_METADATA = {
    "TravelReadSubagent": {
        "name": "TravelReadSubagent",
        "description": "统一处理天气、时间、票务读取的只读专家子代理",
        "skills": [
            {
                "name": "travel-read",
                "description": "执行天气查询、当前时间查询、火车票与机票查询，支持自然语言输入",
            }
        ],
    },
    "OrderSubagent": {
        "name": "OrderSubagent",
        "description": "负责交通订单创建、查询、退票与改签的订单生命周期子代理",
        "skills": [
            {
                "name": "transport-order",
                "description": "执行交通订单创建、查询、退票和改签",
            }
        ],
    },
}


@dataclass
class AgentExecutionResult:
    agent_name: str
    state: str
    text: str
    degraded: bool = False
    no_data: bool = False
    pending_order_context: dict | None = None
    data: dict | None = None
    meta: dict | None = None


@dataclass
class UserPreferenceProfile:
    username: str
    home_city: str = ""
    transport_preference: str = "balanced"
    seat_preference: str = ""
    cabin_preference: str = ""
    budget_level: str = "medium"
    prefer_direct: bool = True
    prefer_morning_departure: bool = False

    def summary_text(self) -> str:
        parts: list[str] = []
        if self.home_city:
            parts.append(f"常住地：{self.home_city}")
        transport_map = {
            "train": "偏好高铁",
            "flight": "偏好飞机",
            "balanced": "交通方式平衡",
        }
        parts.append(transport_map.get(self.transport_preference, "交通方式平衡"))
        if self.seat_preference:
            parts.append(f"高铁席位偏好：{self.seat_preference}")
        if self.cabin_preference:
            parts.append(f"机票舱位偏好：{self.cabin_preference}")
        budget_map = {"low": "预算敏感", "medium": "预算中等", "high": "预算充裕"}
        parts.append(budget_map.get(self.budget_level, "预算中等"))
        parts.append("偏好直达" if self.prefer_direct else "可接受中转")
        parts.append(
            "偏好上午出发" if self.prefer_morning_departure else "出发时间无明显偏好"
        )
        return "；".join(parts)


class TransportDecisionWorkflowState(TypedDict, total=False):
    prompt: str
    conversation_history: str
    intents: list[str]
    user_queries: dict[str, str]
    user_profile_summary: str
    metrics: dict[str, Any]
    decision_query: str
    weather_query: str
    routed_agents: list[str]
    weather_result_text: str
    weather_result_state: str
    weather_result_data: dict[str, Any]
    weather_degraded: bool
    weather_no_data: bool
    plan: dict[str, Any]
    ticket_result_text: str
    ticket_result_state: str
    ticket_result_data: dict[str, Any]
    order_result_text: str
    order_result_state: str
    order_result_pending_context: dict[str, Any]
    final_response: str


class SmartVoyageSupervisor:
    def __init__(self, config: Config):
        self.config = config
        self.invoker = ResilientModelInvoker(config)
        self.travel_read_agent = TravelReadSubagent(config)
        self.order_agent = OrderSubagent(config, self.travel_read_agent)
        self.agent_metadata = dict(DEFAULT_AGENT_METADATA)
        self.current_username = config.default_username
        self.transport_decision_workflow = self._build_transport_decision_workflow()

    def _build_transport_decision_workflow(self):
        workflow = StateGraph(TransportDecisionWorkflowState)
        workflow.add_node("prepare", self._prepare_transport_decision_state)
        workflow.add_node("weather", self._transport_decision_weather_node)
        workflow.add_node("plan", self._transport_decision_plan_node)
        workflow.add_node("ticket", self._transport_decision_ticket_node)
        workflow.add_node("order", self._transport_decision_order_node)
        workflow.add_node("finalize", self._transport_decision_finalize_node)

        workflow.add_edge(START, "prepare")
        workflow.add_edge("prepare", "weather")
        workflow.add_edge("weather", "plan")
        workflow.add_edge("plan", "ticket")
        workflow.add_conditional_edges(
            "ticket",
            self._route_transport_decision_after_ticket,
            {
                "order": "order",
                "finalize": "finalize",
            },
        )
        workflow.add_edge("order", "finalize")
        workflow.add_edge("finalize", END)
        return workflow.compile()

    def _load_user_preferences(self) -> UserPreferenceProfile:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT
                    u.username,
                    p.home_city,
                    p.transport_preference,
                    p.seat_preference,
                    p.cabin_preference,
                    p.budget_level,
                    p.prefer_direct,
                    p.prefer_morning_departure
                FROM users u
                LEFT JOIN user_preferences p ON p.user_id = u.id
                WHERE u.username = %s
                LIMIT 1
                """,
                (self.current_username,),
            )
            row = cursor.fetchone()
            if not row:
                return UserPreferenceProfile(username=self.current_username)
            return UserPreferenceProfile(
                username=row.get("username") or self.current_username,
                home_city=row.get("home_city") or "",
                transport_preference=row.get("transport_preference") or "balanced",
                seat_preference=row.get("seat_preference") or "",
                cabin_preference=row.get("cabin_preference") or "",
                budget_level=row.get("budget_level") or "medium",
                prefer_direct=bool(row.get("prefer_direct"))
                if row.get("prefer_direct") is not None
                else True,
                prefer_morning_departure=bool(row.get("prefer_morning_departure"))
                if row.get("prefer_morning_departure") is not None
                else False,
            )
        except Exception as exc:
            logger.warning(f"读取用户偏好失败: {exc}")
            return UserPreferenceProfile(username=self.current_username)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    def recognize_intent(
        self,
        user_input: str,
        conversation_history: str,
        metrics: dict[str, Any] | None = None,
    ) -> IntentRecognitionResult:
        compact_history = "\n".join(conversation_history.split("\n")[-6:])
        current_date = get_current_date_str(self.config)
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.intent_prompt(
                conversation_history=compact_history, query=user_input
            ),
            IntentRecognitionResult,
            {
                "conversation_history": compact_history,
                "query": user_input,
                "current_date": current_date,
            },
            description="意图识别",
            metrics=metrics,
            phase_name="intent_recognition",
        )
        self._normalize_intent_result(result, user_input)
        logger.info(f"意图识别结构化响应: {result.model_dump()}")
        return result

    @staticmethod
    def _has_explicit_transport_no(text: str) -> bool:
        return bool(EXPLICIT_TRANSPORT_NO_PATTERN.search(text or ""))

    def _normalize_intent_result(
        self, result: IntentRecognitionResult, user_input: str
    ) -> None:
        if not any(
            intent in {"flight", "train", "transport_decision"}
            for intent in result.intents
        ):
            return
        candidate_texts = [user_input]
        candidate_texts.extend(result.user_queries.values())
        if any(self._has_explicit_transport_no(text) for text in candidate_texts):
            result.needs_home_city_follow_up = False

    def process_user_input(
        self,
        prompt: str,
        conversation_history: str,
        pending_order_context: dict | None = None,
    ) -> dict:
        request_id = ensure_request_id()
        logger.info(f"[{request_id}] supervisor received prompt")
        user_profile = self._load_user_preferences()
        metrics = create_metrics()
        try:
            if (
                pending_order_context
                and pending_order_context.get("action") == "hitl_review"
            ):
                return self._handle_hitl_review(
                    prompt, conversation_history, pending_order_context, metrics
                )

            intent_result = self.recognize_intent(
                prompt,
                conversation_history,
                metrics,
            )
            pending_order_context = pending_order_context or {}
            intent_result, pending_order_context = self._merge_pending_order_context(
                prompt,
                conversation_history,
                intent_result,
                pending_order_context,
                metrics,
            )
            self._normalize_intent_result(intent_result, prompt)

            intents = intent_result.intents
            user_queries = intent_result.user_queries
            follow_up_message = intent_result.follow_up_message

            if "out_of_scope" in intents:
                return {
                    "response": self._out_of_scope_response(follow_up_message),
                    "intents": intents,
                    "routed_agents": [],
                    "pending_order_context": {},
                    "metrics": clone_metrics(metrics),
                }

            if follow_up_message:
                if self._has_order_intent(intents):
                    logger.info(
                        "订单意图已识别，忽略 intent 层追问，继续交给 OrderSubagent 生成带 pending context 的补问。"
                    )
                    follow_up_message = ""
                else:
                    return {
                        "response": follow_up_message,
                        "intents": intents,
                        "routed_agents": [],
                        "pending_order_context": pending_order_context,
                        "metrics": clone_metrics(metrics),
                    }

            home_city_follow_up = self._maybe_follow_up_with_home_city(
                intents,
                user_profile,
                intent_result.needs_home_city_follow_up,
            )
            if home_city_follow_up:
                return {
                    "response": home_city_follow_up,
                    "intents": intents,
                    "routed_agents": [],
                    "pending_order_context": pending_order_context,
                    "metrics": clone_metrics(metrics),
                }

            if "transport_decision" in intents:
                return self._handle_transport_decision(
                    prompt,
                    conversation_history,
                    user_queries,
                    intents,
                    user_profile,
                    metrics,
                )

            responses: list[str] = []
            routed_agents: list[str] = []
            next_pending_order_context: dict = (
                pending_order_context if self._has_order_intent(intents) else {}
            )
            for intent in intents:
                agent_name = self._agent_name_for_intent(intent)
                if not agent_name:
                    responses.append("暂不支持此意图。")
                    continue

                query_str = user_queries.get(intent, prompt)
                if intent == "weather":
                    query_str = with_travel_read_kind(query_str, "weather")
                elif intent == "time":
                    query_str = with_travel_read_kind(query_str, "time")
                elif intent in {"flight", "train"}:
                    query_str = with_travel_read_kind(query_str, "ticket")
                query_str = self._with_user_context(intent, query_str)
                if intent in {"cancel_order", "change_order"} and pending_order_context:
                    query_str = self._with_pending_order_context(
                        query_str, pending_order_context
                    )
                result = self._call_agent(
                    agent_name, query_str, conversation_history, metrics=metrics
                )
                routed_agents.append(agent_name)
                responses.append(
                    self._finalize_agent_response(agent_name, query_str, result)
                )
                if intent in {"order", "my_orders", "cancel_order", "change_order"}:
                    if (
                        result.state == "input_required"
                        and result.pending_order_context
                    ):
                        next_pending_order_context = result.pending_order_context
                    elif result.state in {"completed", "failed"}:
                        next_pending_order_context = {}

            return {
                "response": "\n\n".join(responses),
                "intents": intents,
                "routed_agents": routed_agents,
                "pending_order_context": next_pending_order_context,
                "metrics": clone_metrics(metrics),
            }
        finally:
            clear_request_id()

    def _handle_hitl_review(
        self,
        prompt: str,
        conversation_history: str,
        pending_order_context: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> dict:
        review_query = self._with_pending_order_context(prompt, pending_order_context)
        result = self._call_agent(
            "OrderSubagent", review_query, conversation_history, metrics=metrics
        )
        next_pending = result.pending_order_context or {}
        review_payload = (
            next_pending.get("review_payload", {})
            if isinstance(next_pending, dict)
            else {}
        )
        if not review_payload and isinstance(pending_order_context, dict):
            review_payload = pending_order_context.get("review_payload", {}) or {}
        resume_intent = str(next_pending.get("resume_intent", "")).strip()
        if not resume_intent and isinstance(pending_order_context, dict):
            resume_intent = str(pending_order_context.get("resume_intent", "")).strip()
        if not resume_intent:
            review_action = str(review_payload.get("action", "")).strip()
            resume_intent = (
                "order" if review_action == "create_order" else review_action or "order"
            )
        elif resume_intent == "create_order":
            resume_intent = "order"

        source_intent = ""
        source_routed_agents: list[str] = ["OrderSubagent"]
        response_prefix = ""
        if isinstance(pending_order_context, dict):
            source_intent = str(pending_order_context.get("source_intent", "")).strip()
            source_routed_agents = list(
                pending_order_context.get("source_routed_agents", source_routed_agents)
            )
            response_prefix = str(
                pending_order_context.get("response_prefix", "")
            ).strip()

        response_text = self._finalize_agent_response(
            "OrderSubagent", review_query, result
        )
        if source_intent == "transport_decision":
            if response_prefix:
                response_text = f"{response_prefix}\n\n预订结果：{response_text}"
            return {
                "response": response_text,
                "intents": ["transport_decision"],
                "routed_agents": source_routed_agents,
                "pending_order_context": next_pending,
                "metrics": clone_metrics(metrics),
            }

        return {
            "response": response_text,
            "intents": [resume_intent],
            "routed_agents": ["OrderSubagent"],
            "pending_order_context": next_pending,
            "metrics": clone_metrics(metrics),
        }

    def _handle_transport_decision(
        self,
        prompt: str,
        conversation_history: str,
        user_queries: dict[str, str],
        intents: list[str],
        user_profile: UserPreferenceProfile,
        metrics: dict[str, Any],
    ) -> dict:
        result = self.transport_decision_workflow.invoke(
            {
                "prompt": prompt,
                "conversation_history": conversation_history,
                "intents": intents,
                "user_queries": user_queries,
                "user_profile_summary": user_profile.summary_text(),
                "metrics": metrics,
            }
        )
        return {
            "response": result["final_response"],
            "intents": intents,
            "routed_agents": result.get("routed_agents", []),
            "pending_order_context": result.get("order_result_pending_context", {}),
            "metrics": clone_metrics(result.get("metrics", metrics)),
        }

    def _prepare_transport_decision_state(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        prompt = state["prompt"]
        user_queries = state["user_queries"]
        return {
            "decision_query": user_queries.get("transport_decision", prompt),
            "weather_query": user_queries.get("weather", prompt),
            "routed_agents": [],
            "metrics": state.get("metrics", create_metrics()),
        }

    def _transport_decision_weather_node(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        weather_query = state["weather_query"]
        weather_result = self._call_agent(
            "TravelReadSubagent",
            with_travel_read_kind(weather_query, "weather"),
            state["conversation_history"],
            metrics=state.get("metrics"),
        )
        return {
            "weather_result_text": weather_result.text,
            "weather_result_state": weather_result.state,
            "weather_result_data": weather_result.data or {},
            "weather_degraded": weather_result.degraded,
            "weather_no_data": weather_result.no_data,
            "routed_agents": ["TravelReadSubagent"],
            "metrics": state.get("metrics"),
        }

    def _transport_decision_plan_node(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        plan = self.invoker.invoke_structured(
            SmartVoyagePrompts.transport_decision_prompt(
                query=state["decision_query"],
                weather_result=state["weather_result_text"],
            ),
            TransportDecisionPlanResult,
            {
                "query": state["decision_query"],
                "weather_result": state["weather_result_text"],
                "user_preferences": state["user_profile_summary"],
                "current_date": get_current_date_str(
                    self.config, override=self.config.now_override
                ),
            },
            description="交通决策规划",
            metrics=state.get("metrics"),
            phase_name="decision_plan",
        )
        return {"plan": plan.model_dump(), "metrics": state.get("metrics")}

    def _transport_decision_ticket_node(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        ticket_metrics = create_metrics()
        ticket_result = self.travel_read_agent.execute_ticket_plan(
            state["plan"]["ticket_plan"],
            request_id=ensure_request_id(),
            metrics=ticket_metrics,
        )
        merge_metrics(state.get("metrics"), (ticket_result.meta or {}).get("metrics"))
        return {
            "ticket_result_text": ticket_result.text,
            "ticket_result_state": ticket_result.state,
            "ticket_result_data": ticket_result.data or {},
            "routed_agents": list(
                dict.fromkeys(state.get("routed_agents", []) + ["TravelReadSubagent"])
            ),
            "metrics": state.get("metrics"),
        }

    @staticmethod
    def _route_transport_decision_after_ticket(
        state: TransportDecisionWorkflowState,
    ) -> str:
        if (
            state["plan"].get("should_order")
            and state.get("ticket_result_state") == "completed"
        ):
            return "order"
        return "finalize"

    def _transport_decision_order_node(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        plan = state["plan"]
        order_query = self._build_order_query(
            travel_query=state["decision_query"],
            transport_mode=plan["transport_mode"],
            ticket_result_text=state.get("ticket_result_text", ""),
            ticket_result_data=state.get("ticket_result_data", {}),
        )
        order_query = self._with_user_context("order", order_query)
        order_result = self._call_agent(
            "OrderSubagent",
            order_query,
            state["conversation_history"],
            metrics=state.get("metrics"),
        )
        return {
            "order_result_text": order_result.text,
            "order_result_state": order_result.state,
            "order_result_pending_context": order_result.pending_order_context or {},
            "routed_agents": list(
                dict.fromkeys(state.get("routed_agents", []) + ["OrderSubagent"])
            ),
            "metrics": state.get("metrics"),
        }

    def _transport_decision_finalize_node(
        self, state: TransportDecisionWorkflowState
    ) -> dict[str, Any]:
        plan = state["plan"]
        ticket_result = AgentExecutionResult(
            agent_name="TravelReadSubagent",
            state=state.get("ticket_result_state", "failed"),
            text=state.get("ticket_result_text", ""),
            data=state.get("ticket_result_data", {}),
        )
        sections = [
            f"天气判断：{plan.get('weather_brief') or state.get('weather_result_text', '')}",
            f"出行建议：建议优先选择{self._transport_label(plan['transport_mode'])}。{plan['recommendation_reason']}",
            f"票务结果：{self._finalize_agent_response('TravelReadSubagent', with_travel_read_kind(self._ticket_plan_to_query(plan['ticket_plan']), 'ticket'), ticket_result)}",
        ]
        response_prefix = "\n\n".join(sections)

        if plan.get("should_order"):
            if state.get("ticket_result_state") == "completed" and state.get(
                "order_result_text"
            ):
                order_result = AgentExecutionResult(
                    agent_name="OrderSubagent",
                    state=state.get("order_result_state", "failed"),
                    text=state.get("order_result_text", ""),
                )
                order_query = self._with_user_context(
                    "order",
                    self._build_order_query(
                        travel_query=state["decision_query"],
                        transport_mode=plan["transport_mode"],
                        ticket_result_text=state.get("ticket_result_text", ""),
                        ticket_result_data=state.get("ticket_result_data", {}),
                    ),
                )
                sections.append(
                    f"预订结果：{self._finalize_agent_response('OrderSubagent', order_query, order_result)}"
                )
            else:
                sections.append(
                    "预订结果：由于前一步没有查到可用票务数据，本次没有继续提交订票请求。"
                )

        if state.get("weather_degraded"):
            sections.insert(
                0,
                "协作降级：天气服务暂时不可用，当前建议基于保守策略继续完成票务协作。",
            )
        elif state.get("weather_no_data"):
            sections.insert(
                0,
                "天气数据提醒：当前数据库里没有命中对应日期的天气数据，以下建议基于缺失天气数据时的保守策略。",
            )

        pending_context = dict(state.get("order_result_pending_context", {}) or {})
        if pending_context:
            pending_context.setdefault("source_intent", "transport_decision")
            pending_context.setdefault(
                "source_routed_agents",
                list(dict.fromkeys(state.get("routed_agents", []) + ["OrderSubagent"])),
            )
            pending_context.setdefault("response_prefix", response_prefix)

        return {
            "final_response": "\n\n".join(sections),
            "order_result_pending_context": pending_context,
            "metrics": state.get("metrics"),
        }

    def _maybe_follow_up_with_home_city(
        self,
        intents: list[str],
        user_profile: UserPreferenceProfile,
        needs_home_city_follow_up: bool,
    ) -> str:
        if not user_profile.home_city:
            return ""

        target_intent = next(
            (
                intent
                for intent in intents
                if intent in {"flight", "train", "order", "transport_decision"}
            ),
            "",
        )
        if not target_intent:
            return ""
        if not needs_home_city_follow_up:
            return ""
        return (
            f"你这次是从{user_profile.home_city}出发吗？"
            f"如果按你的常住地{user_profile.home_city}出发，我可以继续帮你查票或做出行建议。"
        )

    def _finalize_agent_response(
        self,
        agent_name: str,
        query_str: str,
        result: AgentExecutionResult,
    ) -> str:
        if result.state != "completed":
            return result.text

        read_kind = extract_travel_read_kind(query_str)

        if agent_name == "TravelReadSubagent" and read_kind == "weather":
            return result.text

        if agent_name == "TravelReadSubagent" and read_kind == "ticket":
            direct_response = self._build_ticket_fact_response(
                strip_travel_read_kind(query_str), result.data or {}
            )
            if direct_response:
                return direct_response
            return result.text

        return result.text

    def _call_agent(
        self,
        agent_name: str,
        query_str: str,
        conversation_history: str,
        metrics: dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        chat_history = self._build_agent_chat_history(
            agent_name, query_str, conversation_history
        )
        request_id = ensure_request_id()
        child_metrics = create_metrics()
        try:
            if agent_name == "TravelReadSubagent":
                payload = self.travel_read_agent.invoke(
                    LocalAgentRequest(
                        text=chat_history,
                        conversation_history=chat_history,
                        request_id=request_id,
                        now_override=self.config.now_override,
                        metrics=child_metrics,
                    )
                )
            else:
                payload = self.order_agent.invoke(
                    LocalAgentRequest(
                        text=chat_history,
                        conversation_history=conversation_history,
                        request_id=request_id,
                        now_override=self.config.now_override,
                        metrics=child_metrics,
                    )
                )
            payload_meta = payload.meta or {}
            merge_metrics(metrics, payload_meta.get("metrics"))
            return AgentExecutionResult(
                agent_name=agent_name,
                state=payload.state,
                text=payload.text,
                degraded=payload.state == "failed",
                no_data="未找到" in payload.text,
                pending_order_context=payload.pending_order_context or None,
                data=payload.data or None,
                meta=payload_meta or None,
            )
        except Exception as exc:
            logger.error(f"{agent_name} 调用失败: {exc}")
            return AgentExecutionResult(
                agent_name=agent_name,
                state="failed",
                text=self._agent_error_message(
                    agent_name, "服务暂时不可用，请稍后重试。"
                ),
                degraded=True,
            )

    @staticmethod
    def _agent_name_for_intent(intent: str) -> str | None:
        if intent in {"weather", "time", "flight", "train"}:
            return "TravelReadSubagent"
        if intent in {"order", "my_orders", "cancel_order", "change_order"}:
            return "OrderSubagent"
        return None

    @staticmethod
    def _has_order_intent(intents: list[str]) -> bool:
        return any(
            intent in {"order", "my_orders", "cancel_order", "change_order"}
            for intent in intents
        )

    def _merge_pending_order_context(
        self,
        prompt: str,
        conversation_history: str,
        intent_result: IntentRecognitionResult,
        pending_order_context: dict,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[IntentRecognitionResult, dict]:
        if not pending_order_context:
            return intent_result, {}

        if any(
            intent in {"weather", "time", "flight", "train", "transport_decision"}
            for intent in intent_result.intents
        ):
            return intent_result, {}

        if self._has_order_intent(intent_result.intents):
            return intent_result, pending_order_context

        combined_prompt = self._pending_context_user_prompt(
            prompt, pending_order_context
        )
        combined_result = self.recognize_intent(
            combined_prompt,
            conversation_history,
            metrics,
        )
        if self._has_order_intent(combined_result.intents):
            return combined_result, pending_order_context
        return intent_result, {}

    def _with_user_context(self, intent: str, query: str) -> str:
        if intent not in {"order", "my_orders", "cancel_order", "change_order"}:
            return query

        action_map = {
            "order": "create_order",
            "my_orders": "query_orders",
            "cancel_order": "cancel_order",
            "change_order": "change_order",
        }
        query_with_action = with_order_action(query, action_map[intent])
        if "当前用户" in query_with_action:
            return query_with_action
        return f"当前用户：{self.current_username}\n{query_with_action}"

    @staticmethod
    def _with_pending_order_context(query: str, pending_order_context: dict) -> str:
        if not pending_order_context:
            return query
        payload = json.dumps(pending_order_context, ensure_ascii=False)
        return f"[PENDING_ORDER_CONTEXT]{payload}[/PENDING_ORDER_CONTEXT]\n{query}"

    @staticmethod
    def _pending_context_user_prompt(prompt: str, pending_order_context: dict) -> str:
        payload = json.dumps(pending_order_context, ensure_ascii=False)
        return f"继续处理之前的订单操作。待补上下文：{payload}。本轮补充：{prompt}"

    def _build_ticket_fact_response(
        self, query_str: str, ticket_data: dict[str, Any]
    ) -> str:
        query_plan = (
            ticket_data.get("query_plan", {}) if isinstance(ticket_data, dict) else {}
        )
        tickets = (
            ticket_data.get("tickets", []) if isinstance(ticket_data, dict) else []
        )
        if (
            not isinstance(query_plan, dict)
            or not isinstance(tickets, list)
            or not tickets
        ):
            return ""
        if not str(query_plan.get("transport_no", "")).strip():
            return ""

        info = tickets[0]
        response = (
            f"{info.get('departure_time', '')} {info.get('departure_city', '')}到{info.get('arrival_city', '')} "
            f"{info.get('transport_no', '')} {info.get('ticket_type', '')}当前剩余 {info.get('remaining_seats', '')} 张，"
            f"票价 {info.get('price', '')} 元。"
        )
        order_context = self._related_order_context(
            departure_time=str(info.get("departure_time", "")),
            transport_no=str(info.get("transport_no", "")),
            ticket_type=str(info.get("ticket_type", "")),
        )
        if order_context:
            response += order_context
        return response

    def _related_order_context(
        self, *, departure_time: str, transport_no: str, ticket_type: str
    ) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT quantity
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE u.username = %s
                  AND o.status = 'booked'
                  AND o.departure_time = %s
                  AND o.transport_no = %s
                  AND o.ticket_or_room_type = %s
                LIMIT 1
                """,
                (self.current_username, departure_time, transport_no, ticket_type),
            )
            row = cursor.fetchone()
            if not row:
                return ""
            return f" 你当前已预订该车次/航班 {row['quantity']} 张。"
        except Exception as exc:
            logger.warning(f"读取用户订单上下文失败: {exc}")
            return ""
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    @staticmethod
    def _build_agent_chat_history(
        agent_name: str, query_str: str, conversation_history: str
    ) -> str:
        if agent_name == "TravelReadSubagent":
            return f"User: {query_str}"
        lines = [line for line in conversation_history.splitlines() if line.strip()]
        trimmed = "\n".join(lines[-4:])
        if trimmed:
            return f"{trimmed}\nUser: {query_str}"
        return f"User: {query_str}"

    @staticmethod
    def _transport_label(transport_mode: str) -> str:
        return "高铁/火车" if transport_mode == "train" else "飞机"

    @staticmethod
    def _ticket_plan_to_query(ticket_plan: dict[str, Any]) -> str:
        if not isinstance(ticket_plan, dict):
            return ""
        transport_label = "高铁票" if ticket_plan.get("type") == "train" else "机票"
        transport_no = str(ticket_plan.get("transport_no", "")).strip()
        ticket_type = str(ticket_plan.get("ticket_type", "")).strip()
        if transport_no:
            return (
                f"查询{ticket_plan.get('date_from', '')}{ticket_plan.get('departure_city', '')}到"
                f"{ticket_plan.get('arrival_city', '')}的{transport_label}，车次/航班 {transport_no}"
            )
        suffix = ticket_type if ticket_type else ""
        return (
            f"查询{ticket_plan.get('date_from', '')}{ticket_plan.get('departure_city', '')}到"
            f"{ticket_plan.get('arrival_city', '')}的{transport_label}{suffix}"
        )

    @staticmethod
    def _build_order_query(
        *,
        travel_query: str,
        transport_mode: str,
        ticket_result_text: str,
        ticket_result_data: dict[str, Any] | None = None,
    ) -> str:
        tickets = (ticket_result_data or {}).get("tickets", [])
        if tickets:
            ticket = tickets[0]
            transport_no = ticket.get("transport_no", "")
            ticket_type = ticket.get("ticket_type", "")
            departure_date = str(ticket.get("departure_time", ""))[:10]
            departure_city = ticket.get("departure_city", "")
            arrival_city = ticket.get("arrival_city", "")
            transport_label = "高铁票" if transport_mode == "train" else "机票"
            return (
                f"请直接预订{departure_date}{departure_city}到{arrival_city}的{transport_label}，"
                f"{'车次' if transport_mode == 'train' else '航班'}{transport_no}，"
                f"{ticket_type}1张。"
            )

        transport_label = "高铁票" if transport_mode == "train" else "机票"
        first_ticket_line = ticket_result_text.splitlines()[0].strip()
        return (
            f"{travel_query}\n"
            f"请基于以下已查询到的真实票务结果，选择最合适的一张{transport_label}完成预订；"
            "如果信息不足再追问，不要虚构车次或航班信息。\n"
            f"可用票务：{first_ticket_line}"
        )

    @staticmethod
    def _out_of_scope_response(follow_up_message: str) -> str:
        normalized = str(follow_up_message or "").strip()
        domain_boundary = "当前我主要支持交通出行相关问题，例如时间、天气、票务查询和订单处理。请围绕交通、出行、票务或订单继续提问。"
        if not normalized:
            return domain_boundary
        if any(keyword in normalized for keyword in ("交通", "出行", "票务", "订单")):
            return normalized
        return domain_boundary

    @staticmethod
    def _agent_timeout_message(agent_name: str) -> str:
        messages = {
            "TravelReadSubagent": "交通读取服务响应超时，当前无法完成天气、时间或票务查询。请稍后重试。",
            "OrderSubagent": "订单服务响应超时，当前没有完成实际下单或订单变更。请稍后重试，避免重复提交。",
        }
        return messages.get(agent_name, "服务响应超时，请稍后重试。")

    @staticmethod
    def _agent_error_message(agent_name: str, default_message: str) -> str:
        messages = {
            "TravelReadSubagent": "交通读取服务当前不可用，请稍后重试天气、时间或票务查询。",
            "OrderSubagent": "订单服务当前不可用，当前没有完成实际下单或订单变更。",
        }
        return messages.get(agent_name, default_message)
