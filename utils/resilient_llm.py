from __future__ import annotations

from typing import Any

from create_logger import logger
from config import Config
from utils.model_factory import build_chat_model, build_structured_llm


class ResilientModelInvoker:
    def __init__(self, config: Config, *, temperature: float = 0.1):
        self.config = config
        self.temperature = temperature
        self.primary_model = build_chat_model(config, temperature=temperature)
        self.fallback_model = self._build_fallback_model()

    def _build_fallback_model(self):
        if not self.config.fallback_provider or not self.config.fallback_model_name:
            return None

        return build_chat_model(
            self.config,
            provider=self.config.fallback_provider,
            model_name=self.config.fallback_model_name,
            base_url=self.config.fallback_base_url,
            api_key=self.config.fallback_api_key,
            ollama_base_url=self.config.fallback_ollama_base_url,
            temperature=self.temperature,
        )

    def invoke_structured(
        self,
        prompt: Any,
        schema: type[Any],
        payload: dict[str, Any],
        *,
        description: str,
    ) -> Any:
        retries = self.config.structured_retry_count
        return self._invoke_with_models(
            description=description,
            retries=retries,
            factory=lambda model: prompt | build_structured_llm(model, schema),
            payload=payload,
            validate_result=lambda result: result is not None and hasattr(result, "model_dump"),
            invalid_result_message="结构化输出为空或不符合预期",
        )

    def invoke_text(
        self,
        prompt: Any,
        payload: dict[str, Any],
        *,
        description: str,
    ) -> str:
        retries = self.config.text_retry_count
        result = self._invoke_with_models(
            description=description,
            retries=retries,
            factory=lambda model: prompt | model,
            payload=payload,
        )
        content = getattr(result, "content", result)
        return content.strip() if isinstance(content, str) else str(content).strip()

    async def ainvoke_agent(
        self,
        agent_factory,
        payload: dict[str, Any],
        *,
        description: str,
    ) -> Any:
        errors: list[str] = []
        for model_label, model in self._iter_models():
            for attempt in range(1, self.config.text_retry_count + 1):
                try:
                    logger.info(
                        f"{description}: 使用 {model_label} 模型执行，第 {attempt} 次尝试。"
                    )
                    agent = agent_factory(model)
                    return await agent.ainvoke(payload)
                except Exception as exc:
                    error_text = f"{model_label} attempt {attempt}: {exc}"
                    errors.append(error_text)
                    logger.warning(f"{description} 失败：{error_text}")
                    if attempt >= self.config.text_retry_count:
                        logger.warning(f"{description}: {model_label} 模型重试耗尽。")
        raise RuntimeError(f"{description} 失败，所有模型均不可用：{' | '.join(errors)}")

    def _invoke_with_models(
        self,
        *,
        description: str,
        retries: int,
        factory,
        payload: dict[str, Any],
        validate_result=None,
        invalid_result_message: str = "模型返回了无效结果",
    ) -> Any:
        errors: list[str] = []
        for model_label, model in self._iter_models():
            for attempt in range(1, retries + 1):
                try:
                    logger.info(
                        f"{description}: 使用 {model_label} 模型执行，第 {attempt} 次尝试。"
                    )
                    result = factory(model).invoke(payload)
                    if validate_result is not None and not validate_result(result):
                        raise RuntimeError(invalid_result_message)
                    return result
                except Exception as exc:
                    error_text = f"{model_label} attempt {attempt}: {exc}"
                    errors.append(error_text)
                    logger.warning(f"{description} 失败：{error_text}")
                    if attempt >= retries:
                        logger.warning(f"{description}: {model_label} 模型重试耗尽。")
        raise RuntimeError(f"{description} 失败，所有模型均不可用：{' | '.join(errors)}")

    def _iter_models(self):
        yield "primary", self.primary_model
        if self.fallback_model is not None:
            yield "fallback", self.fallback_model
