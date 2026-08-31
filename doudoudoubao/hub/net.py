# -*- coding: utf-8 -*-
"""网络错误识别与自动重试工具。

只对「本地网络/DNS/连接类」错误重试，内容审核、额度、风控等业务错误绝不重试。
"""
from __future__ import annotations

import asyncio
from typing import Callable

# 命中任一关键词即视为本地网络问题（与提示词、账号无关）
_NETWORK_KEYWORDS = (
    "getaddrinfo failed",
    "cannot connect to host",
    "connect call failed",
    "connection reset",
    "connection refused",
    "connection aborted",
    "name or service not known",
    "server disconnected",
    "ssl:",
    "socket",
    "dns",
    "由本地系统中止网络连接",
    "winerror 1236",
)

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = (3.0, 6.0, 12.0)


def is_network_error(message: str) -> bool:
    m = str(message or "").lower()
    return any(k in m for k in _NETWORK_KEYWORDS)


async def run_with_retry(
    factory: Callable,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff=DEFAULT_BACKOFF,
    trace=None,
    label: str = "网络请求",
):
    """执行 factory()；仅对网络类异常自动重试，其余异常直接抛出。

    trace 为可选回调 trace(level, text)，用于把重试过程写进实时日志。
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001 - 统一在最后一轮抛出
            last_exc = exc
            if attempt >= attempts - 1 or not is_network_error(str(exc)):
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            if trace:
                trace(
                    "warn",
                    f"[网络] {label} 失败（{str(exc)[:160]}），"
                    f"{int(wait)} 秒后自动重试（{attempt + 2}/{attempts}）",
                )
            await asyncio.sleep(wait)
    raise last_exc or RuntimeError("网络请求失败")
