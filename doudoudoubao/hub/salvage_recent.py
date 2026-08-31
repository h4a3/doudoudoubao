# -*- coding: utf-8 -*-
"""扫描豆包最近会话，回收所有已生成但未下载的视频到 output/。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.nomark import download_video  # noqa: E402
from hub.video_v2 import _pull_chain, fetch_aispace_original, _iter_creations  # noqa: E402

ACC = sys.argv[1] if len(sys.argv) > 1 else "acc02"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 10
OUT = PROJECT / "output"


async def recent_convs(client) -> list:
    from urllib.parse import urlencode
    from hub.video_v2 import _chain_query_params
    url = f"{client.BASE_URL}/im/chain/recent_conv?{urlencode(_chain_query_params(client))}"
    body = {
        "cmd": 3200,
        "uplink_body": {"pull_recent_conv_chain_uplink_body": {
            "limit": LIMIT, "message_count_per_conv": 1, "api_version": 1,
            "conv_version": 0, "direction": 3,
            "option": {"not_need_message": False, "need_complete_conversation": True,
                       "need_coco_conversation": True, "need_coco_bot": True},
        }},
        "sequence_id": "monitor-scan", "channel": 2, "version": "1",
    }
    async with client.session.post(url, data=json.dumps(body, ensure_ascii=False),
                                   headers={"Content-Type": "application/json; encoding=utf-8"},
                                   timeout=30) as resp:
        data = await resp.json()
    cells = (data.get("downlink_body") or {}).get("pull_recent_conv_chain_downlink_body") or {}
    return [str(c.get("conversation", {}).get("conversation_id")) for c in (cells.get("cells") or []) if c.get("conversation")]


def already_have(vid: str) -> bool:
    return any(vid in f.name for f in OUT.glob("*.mp4"))


async def main() -> None:
    session_file = PROJECT / "accounts" / ACC / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    OUT.mkdir(exist_ok=True)
    async with client:
        convs = await recent_convs(client)
        print("convs:", len(convs))
        done = 0
        for conv in convs:
            try:
                data = await _pull_chain(client, conv)
            except Exception as exc:
                print("chain err", conv, exc)
                continue
            for creation in _iter_creations(data):
                video = creation.get("video") or {}
                if video.get("status") != 3:
                    continue
                vid = str(video.get("vid", ""))
                if not vid or already_have(vid):
                    continue
                try:
                    url = await fetch_aispace_original(client, vid)
                    if not url:
                        print("skip no watermark", vid)
                        continue
                    name = f"回收_{conv}_{vid}_无水印v2.mp4"
                    size = await download_video(url, OUT / name)
                    print("saved", name, f"{size/1048576:.1f} MB")
                    done += 1
                except Exception as exc:
                    print("save err", vid, exc)
        print("DONE", done)


if __name__ == "__main__":
    asyncio.run(main())
