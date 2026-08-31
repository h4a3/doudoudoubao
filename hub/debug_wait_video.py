# -*- coding: utf-8 -*-
"""诊断工具：轮询指定会话直到出现视频结果，验证结果结构。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import (  # noqa: E402
    _pull_chain,
    find_confirm_request,
    parse_creation_result,
    scan_creation_statuses,
)


async def main(conv: str, minutes: int) -> None:
    session_file = PROJECT / "accounts" / "acc01" / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    deadline = asyncio.get_event_loop().time() + minutes * 60
    async with client:
        n = 0
        while asyncio.get_event_loop().time() < deadline:
            n += 1
            try:
                data = await _pull_chain(client, conv)
            except Exception as exc:
                print(f"[{n}] chain error: {exc}")
                await asyncio.sleep(10)
                continue
            print(f"[{n}] status_code={data.get('status_code')} "
                  f"creations={scan_creation_statuses(data)} "
                  f"confirm={bool(find_confirm_request(data))}")
            found = parse_creation_result(data)
            if found:
                out = PROJECT / "logs" / f"creation_found_{conv}.json"
                out.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
                print("FOUND:", json.dumps(found, ensure_ascii=False))
                print("saved:", out)
                return
            await asyncio.sleep(15)
    print("TIMEOUT no video")


if __name__ == "__main__":
    conv = sys.argv[1] if len(sys.argv) > 1 else "38439360275270914"
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    asyncio.run(main(conv, minutes))
