# -*- coding: utf-8 -*-
"""实时日志中枢：内存环形缓冲 + SSE 广播。

所有后台工作（扫码登录、生成流水线）都往这里写日志，
前端通过 /api/events 的 SSE 长连接实时收到，
"正在做什么、卡在哪一步"一目了然。
"""
from __future__ import annotations

import asyncio
import functools
import json
from collections import deque
from datetime import datetime
from typing import AsyncIterator, Deque, List, Optional

MAX_HISTORY = 2000

_LEVEL_ICON = {"info": "·", "ok": "✓", "warn": "⚠", "error": "✗", "step": "▶"}


def _safe_console(text: str) -> None:
    """控制台打印永不抛异常。

    服务若从工作台/终端以“管道被关闭”的方式启动，print(flush=True)
    会抛 OSError，并让记录日志的接口全部 500。这里吞掉一切打印异常。
    """
    try:
        print(text, flush=True)
    except Exception:
        pass


class LogHub:
    """线程安全（借用 asyncio 环）的全局日志中心。"""

    def __init__(self) -> None:
        self._history: Deque[dict] = deque(maxlen=MAX_HISTORY)
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._seq = 0

    # -- 发布 ------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """记下主事件循环，供后台线程日志转投。"""
        self._loop = loop

    def log(
        self,
        message: str,
        level: str = "info",
        task_id: str = "",
        account_id: str = "",
    ) -> dict:
        entry = {
            "seq": self._next_seq(),
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "task_id": task_id,
            "account_id": account_id,
            "message": message,
        }
        self._history.append(entry)
        self._broadcast(entry)
        icon = _LEVEL_ICON.get(level, "·")
        prefix = "".join(
            p for p in (f"[{entry['ts']}]",
                        f"[{task_id}]" if task_id else "",
                        f"[{account_id}]" if account_id else "")
            if p
        )
        _safe_console(f"{prefix} {icon} {message}")
        return entry

    # 便捷别名
    def step(self, msg: str, **kw) -> dict:
        return self.log(msg, level="step", **kw)

    def ok(self, msg: str, **kw) -> dict:
        return self.log(msg, level="ok", **kw)

    def warn(self, msg: str, **kw) -> dict:
        return self.log(msg, level="warn", **kw)

    def error(self, msg: str, **kw) -> dict:
        return self.log(msg, level="error", **kw)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _broadcast(self, entry: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    # 后台线程（如 QRLogin 线程）调用时转投到主循环
    def log_threadsafe(self, message: str, level: str = "info", **kw) -> None:
        if self._loop is None or self._loop is _running_loop():
            self.log(message, level=level, **kw)
            return
        self._loop.call_soon_threadsafe(
            functools.partial(self.log, message, level=level, **kw)
        )

    # -- 订阅 ------------------------------------------------------------

    def history(self, limit: int = 300) -> List[dict]:
        entries = list(self._history)
        return entries[-limit:]

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        try:
            while True:
                entry = await q.get()
                yield entry
        finally:
            self._subscribers.remove(q)


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


log_hub = LogHub()


def sse_format(entry: dict) -> str:
    return f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
