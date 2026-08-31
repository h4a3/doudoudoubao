# -*- coding: utf-8 -*-
"""对已有会话自动发送文字确认。"""
import asyncio
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import _pull_chain, send_text_v2  # noqa: E402


async def main(conv: str, acc: str) -> None:
    session_file = PROJECT / "accounts" / acc / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    async with client:
        data = await _pull_chain(client, conv)
        await send_text_v2(client, conv, "确认，按上述参数生成。", data, trace_log=lambda lv, t: print(lv, t))


if __name__ == "__main__":
    conv = sys.argv[1] if len(sys.argv) > 1 else "38439480115955714"
    acc = sys.argv[2] if len(sys.argv) > 2 else "acc02"
    asyncio.run(main(conv, acc))
