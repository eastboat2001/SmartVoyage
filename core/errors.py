"""
功能：把嵌套异常整理为可读的错误摘要。
作用：让 agent 和 MCP 在失败时输出更稳定、更易定位的问题描述。
实现方式：递归收集异常树中的消息并压缩成单行文本。
"""

from __future__ import annotations


def format_exception_details(exc: BaseException) -> str:
    parts: list[str] = []
    _collect_exception_messages(exc, parts)
    return ' | '.join(parts) if parts else repr(exc)


def _collect_exception_messages(exc: BaseException, parts: list[str]) -> None:
    nested = getattr(exc, 'exceptions', None)
    if nested:
        for sub_exc in nested:
            _collect_exception_messages(sub_exc, parts)
        return

    message = str(exc).strip() or repr(exc)
    exc_type = type(exc).__name__
    parts.append(f'{exc_type}: {message}')
