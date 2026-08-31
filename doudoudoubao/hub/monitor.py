# -*- coding: utf-8 -*-
"""后台监控：每 10 秒检查 hub 任务与豆包会话，输出卡点分析。

用法：
  python hub/monitor.py [conversation_id] [account_id]
不带参数时只监控 hub 任务列表。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import (  # noqa: E402
    _pull_chain,
    find_confirm_request,
    find_rejection_text,
    find_text_confirm_request,
    parse_creation_result,
    scan_creation_statuses,
)

CONV = sys.argv[1] if len(sys.argv) > 1 else ""
ACC = sys.argv[2] if len(sys.argv) > 2 else "acc02"


def tasks_summary() -> None:
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:8000/api/tasks", timeout=5)
        data = r.json()
        for t in data.get("tasks", []):
            print(
                time.strftime("%H:%M:%S"),
                t["id"], t["status"],
                "wait:", t.get("message", "")[:80],
                "created:", t.get("created_at"),
            )
    except Exception as exc:
        print(time.strftime("%H:%M:%S"), "hub查询失败:", exc)


async def chain_summary() -> None:
    if not CONV:
        return
    session_file = PROJECT / "accounts" / ACC / ".doubao_session.json"
    if not session_file.exists():
        return
    try:
        client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=60)
        async with client:
            data = await _pull_chain(client, CONV)
        statuses = scan_creation_statuses(data)
        found = parse_creation_result(data)
        confirm = find_confirm_request(data)
        text_confirm = find_text_confirm_request(data)
        rejection = find_rejection_text(data)
        print(
            time.strftime("%H:%M:%S"),
            "chain conv=", CONV,
            "| statuses=", statuses,
            "| found=", bool(found),
            "| button_confirm=", bool(confirm),
            "| text_confirm=", bool(text_confirm),
            "| rejection=", bool(rejection),
        )
        if rejection:
            print("  REJECTION:", rejection[:200])
        if text_confirm:
            print("  TEXT_CONFIRM:", text_confirm[:200])
        if confirm:
            print("  BUTTON_CONFIRM:", confirm.get("display_text"), confirm.get("scene"))
    except Exception as exc:
        print(time.strftime("%H:%M:%S"), "chain查询失败:", exc)


async def main() -> None:
    while True:
        tasks_summary()
        await chain_summary()
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
