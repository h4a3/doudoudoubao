# -*- coding: utf-8 -*-
"""诊断工具：查询豆包账号最近会话列表。"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import _v2_query_params  # noqa: E402


async def main(account_id: str) -> None:
    session_file = PROJECT / "accounts" / account_id / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=60)
    async with client:
        params = _v2_query_params(client)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{client.BASE_URL}/im/chain/recent_conv?{query}"
        body = {
            "cmd": 3200,
            "uplink_body": {"pull_recent_conv_chain_uplink_body": {
                "limit": 10,
                "message_count_per_conv": 1,
                "api_version": 1,
                "conv_version": 0,
                "direction": 3,
                "option": {
                    "not_need_message": False,
                    "need_complete_conversation": True,
                    "need_coco_conversation": True,
                    "need_coco_bot": True,
                },
            }},
            "sequence_id": str(uuid.uuid4()),
            "channel": 2,
            "version": "1",
        }
        resp = await client.session.post(
            url,
            data=json.dumps(body, ensure_ascii=False),
            headers={"Content-Type": "application/json; encoding=utf-8"},
        )
        print("HTTP", resp.status)
        data = await resp.json()
        out = PROJECT / "logs" / f"recent_conv_{account_id}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", out)
        down = (data.get("downlink_body") or {}).get("pull_recent_conv_chain_downlink_body") or {}
        convs = down.get("conversations") or down.get("conv_list") or []
        print("conversations:", len(convs))
        for c in convs:
            print("-", c.get("conversation_id"), "|", (c.get("title") or c.get("brief") or "")[:60],
                  "| update", c.get("update_time"), "| keys", list(c.keys())[:20])


if __name__ == "__main__":
    acc = sys.argv[1] if len(sys.argv) > 1 else "acc01"
    asyncio.run(main(acc))
