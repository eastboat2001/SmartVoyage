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
