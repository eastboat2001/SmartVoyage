from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from config import Config

try:
    from langchain_ollama import ChatOllama
except ImportError:  # pragma: no cover - dependency validation happens at runtime
    ChatOllama = None


ORDER_AGENT_SYSTEM_PROMPT = (
    "你是一个交通票务订单助手，能够调用工具完成火车票、飞机票预定，查询用户订单，退票和改签。"
    "你需要仔细分析工具需要的参数，然后从用户提供的信息和上下文里提取信息，尤其不能忽略“当前用户”。"
    "如果用户提供的信息不足以提取到调用工具所有必要参数，则向用户追问，以获取该信息。"
    "不能自己编撰参数。"
)

HOTEL_AGENT_SYSTEM_PROMPT = (
    "你是一个酒店助手，能够调用工具完成酒店查询、酒店预订和酒店订单查询。"
    "你需要优先基于工具返回的真实数据回答，不能编造酒店、房型、价格、库存或日期。"
    "如果用户要预订酒店，必须收集到入住城市、酒店名、房型、入住日期、入住晚数和房间数。"
    "如果信息不足，请明确追问。"
)


def build_chat_model(
    config: Config,
    *,
    provider: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    ollama_base_url: str | None = None,
    temperature: float = 0.1,
) -> BaseChatModel:
    provider = provider or config.provider
    selected_model = model_name or config.model_name

    if provider == "openai_compatible":
        return ChatOpenAI(
            model=selected_model,
            base_url=base_url or config.base_url,
            api_key=api_key or config.api_key,
            temperature=temperature,
        )

    if provider == "ollama":
        if ChatOllama is None:
            raise ImportError(
                "provider=ollama 需要安装 langchain-ollama。"
                "请执行 `uv pip install langchain-ollama` 或重新安装 requirements.txt。"
            )
        return ChatOllama(
            model=selected_model,
            base_url=ollama_base_url or config.ollama_base_url,
            temperature=temperature,
        )

    raise ValueError(
        f"Unsupported provider: {provider}. Expected 'openai_compatible' or 'ollama'."
    )


def build_order_agent(model: BaseChatModel, tools: list[Any]):
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=ORDER_AGENT_SYSTEM_PROMPT,
    )


def build_hotel_agent(model: BaseChatModel, tools: list[Any]):
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=HOTEL_AGENT_SYSTEM_PROMPT,
    )


def build_structured_llm(model: BaseChatModel, schema: type[Any]):
    if ChatOllama is not None and isinstance(model, ChatOllama):
        return model.with_structured_output(
            schema,
            method="json_schema",
        )

    return model.with_structured_output(
        schema,
        method="function_calling",
        strict=True,
    )


def extract_text_from_agent_result(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    for message in reversed(messages):
        text = _message_text(message)
        if text:
            return text
    return ""


def _message_text(message: Any) -> str:
    if isinstance(message, BaseMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
        return "\n".join(part for part in parts if part)

    return str(content).strip() if content else ""
