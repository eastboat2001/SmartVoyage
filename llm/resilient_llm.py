"""
功能：提供带重试、轻重模型路由和 fallback 的模型调用器。
作用：统一管理结构化调用、文本调用和 agent 调用的可靠性与 metrics。
实现方式：在每次调用时按任务阶段选择模型顺序，并记录重试与耗时。
"""

from __future__ import annotations

import time
from typing import Any

from core.logging import logger
from core.config import Config
from observability.metrics import increment_metric, record_phase_time
from llm.model_factory import build_chat_model, build_structured_llm


class ResilientModelInvoker:
    def __init__(self, config: Config, *, temperature: float = 0.1):
        self.config = config
        self.temperature = temperature
        self.primary_model_spec = {
            "provider": config.provider,
            "model_name": config.model_name,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "ollama_base_url": config.ollama_base_url,
        }
        self.fallback_model_spec = self._build_fallback_model_spec()
        self.light_model_spec = self._build_light_model_spec()

        self.primary_model = self._build_model(self.primary_model_spec)
        self.fallback_model = self._build_model(self.fallback_model_spec)
        self.light_model = self._build_model(self.light_model_spec)

    def _build_model(self, spec: dict[str, Any] | None):
        if not spec:
            return None
        return build_chat_model(
            self.config,
            provider=spec.get("provider"),
            model_name=spec.get("model_name"),
            base_url=spec.get("base_url"),
            api_key=spec.get("api_key"),
            ollama_base_url=spec.get("ollama_base_url"),
            temperature=self.temperature,
        )

    def _build_fallback_model_spec(self) -> dict[str, Any] | None:
        if not self.config.fallback_provider or not self.config.fallback_model_name:
            return None
        return {
            "provider": self.config.fallback_provider,
            "model_name": self.config.fallback_model_name,
            "base_url": self.config.fallback_base_url,
            "api_key": self.config.fallback_api_key,
            "ollama_base_url": self.config.fallback_ollama_base_url,
        }

    def _build_light_model_spec(self) -> dict[str, Any] | None:
        if not self.config.light_model_name:
            return None
        return {
            "provider": self.config.light_model_provider or self.config.provider,
            "model_name": self.config.light_model_name,
            "base_url": self.config.light_model_base_url,
            "api_key": self.config.light_model_api_key,
            "ollama_base_url": self.config.light_model_ollama_base_url,
        }

    def invoke_structured(
        self,
        prompt: Any,
        schema: type[Any],
        payload: dict[str, Any],
        *,
        description: str,
        metrics: dict[str, Any] | None = None,
        phase_name: str = "",
        task_key: str = "",
    ) -> Any:
        retries = self.config.structured_retry_count
        start = time.perf_counter()
        resolved_task_key = task_key or phase_name
        try:
            return self._invoke_with_models(
                description=description,
                retries=retries,
                factory=lambda model: prompt | build_structured_llm(model, schema),
                payload=payload,
                validate_result=lambda result: result is not None and hasattr(result, "model_dump"),
                invalid_result_message="结构化输出为空或不符合预期",
                metrics=metrics,
                task_key=resolved_task_key,
            )
        finally:
            if phase_name:
                record_phase_time(metrics, phase_name, (time.perf_counter() - start) * 1000)

    def invoke_text(
        self,
        prompt: Any,
        payload: dict[str, Any],
        *,
        description: str,
        metrics: dict[str, Any] | None = None,
        phase_name: str = "",
        task_key: str = "",
    ) -> str:
        retries = self.config.text_retry_count
        start = time.perf_counter()
        resolved_task_key = task_key or phase_name
        try:
            result = self._invoke_with_models(
                description=description,
                retries=retries,
                factory=lambda model: prompt | model,
                payload=payload,
                metrics=metrics,
                task_key=resolved_task_key,
            )
            content = getattr(result, "content", result)
            return content.strip() if isinstance(content, str) else str(content).strip()
        finally:
            if phase_name:
                record_phase_time(metrics, phase_name, (time.perf_counter() - start) * 1000)

    async def ainvoke_agent(
        self,
        agent_factory,
        payload: dict[str, Any],
        *,
        description: str,
        metrics: dict[str, Any] | None = None,
        phase_name: str = "",
        task_key: str = "",
    ) -> Any:
        start = time.perf_counter()
        errors: list[str] = []
        resolved_task_key = task_key or phase_name
        try:
            for model_label, model in self._iter_models(resolved_task_key):
                for attempt in range(1, self.config.text_retry_count + 1):
                    try:
                        logger.info(f"{description}: 使用 {model_label} 模型执行，第 {attempt} 次尝试。")
                        increment_metric(metrics, "llm_call_count")
                        agent = agent_factory(model)
                        return await agent.ainvoke(payload)
                    except Exception as exc:
                        error_text = f"{model_label} attempt {attempt}: {exc}"
                        errors.append(error_text)
                        logger.warning(f"{description} 失败：{error_text}")
                        if attempt >= self.config.text_retry_count:
                            logger.warning(f"{description}: {model_label} 模型重试耗尽。")
            raise RuntimeError(f"{description} 失败，所有模型均不可用：{' | '.join(errors)}")
        finally:
            if phase_name:
                record_phase_time(metrics, phase_name, (time.perf_counter() - start) * 1000)

    def _invoke_with_models(
        self,
        *,
        description: str,
        retries: int,
        factory,
        payload: dict[str, Any],
        metrics: dict[str, Any] | None = None,
        validate_result=None,
        invalid_result_message: str = "模型返回了无效结果",
        task_key: str = "",
    ) -> Any:
        errors: list[str] = []
        for model_label, model in self._iter_models(task_key):
            for attempt in range(1, retries + 1):
                try:
                    logger.info(f"{description}: 使用 {model_label} 模型执行，第 {attempt} 次尝试。")
                    increment_metric(metrics, "llm_call_count")
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

    def _iter_models(self, task_key: str = ""):
        seen: set[tuple[Any, ...]] = set()
        if self._should_use_light_model(task_key):
            light_signature = self._model_signature(self.light_model_spec)
            if light_signature not in seen:
                seen.add(light_signature)
                yield "light", self.light_model

        primary_signature = self._model_signature(self.primary_model_spec)
        if primary_signature not in seen:
            seen.add(primary_signature)
            yield "primary", self.primary_model

        fallback_signature = self._model_signature(self.fallback_model_spec)
        if self.fallback_model is not None and fallback_signature not in seen:
            yield "fallback", self.fallback_model

    def _should_use_light_model(self, task_key: str) -> bool:
        if self.light_model is None:
            return False
        if not task_key:
            return False
        return task_key in self.config.light_model_phases

    @staticmethod
    def _model_signature(spec: dict[str, Any] | None) -> tuple[Any, ...]:
        if not spec:
            return tuple()
        return (
            spec.get("provider", ""),
            spec.get("model_name", ""),
            spec.get("base_url", ""),
            spec.get("ollama_base_url", ""),
        )
