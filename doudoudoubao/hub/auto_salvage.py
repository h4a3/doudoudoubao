# -*- coding: utf-8 -*-
"""自动回收：定期扫描豆包最近会话，把已生成但未下载的视频自动回收进 output/。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT / "doubao2api") not in sys.path:
    sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from .config import session_file  # noqa: E402
from .logs import log_hub  # noqa: E402
from .nomark import download_video  # noqa: E402
from .video_v2 import _chain_query_params, _iter_creations, _pull_chain, fetch_aispace_original  # noqa: E402

OUTPUT_DIR = PROJECT / "output"
RECENT_LIMIT = 6
SCAN_INTERVAL_SEC = 45.0


def _already_have(vid: str) -> bool:
    return any(vid in f.name for f in OUTPUT_DIR.glob("*.mp4"))


async def _recent_convs(client) -> list:
    from urllib.parse import urlencode

    url = f"{client.BASE_URL}/im/chain/recent_conv?{urlencode(_chain_query_params(client))}"
    body = {
        "cmd": 3200,
        "uplink_body": {"pull_recent_conv_chain_uplink_body": {
            "limit": RECENT_LIMIT,
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
        "sequence_id": "auto-salvage",
        "channel": 2,
        "version": "1",
    }
    async with client.session.post(
        url,
        data=json.dumps(body, ensure_ascii=False),
        headers={"Content-Type": "application/json; encoding=utf-8"},
        timeout=30,
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
    down = (data.get("downlink_body") or {}).get("pull_recent_conv_chain_downlink_body") or {}
    return [
        str(c.get("conversation", {}).get("conversation_id"))
        for c in (down.get("cells") or [])
        if c.get("conversation")
    ]


async def _salvage_account(account_id: str) -> int:
    session_path = session_file(account_id)
    if not session_path.exists():
        return 0
    client = DoubaoChatClient.from_session(session_file=str(session_path), timeout_seconds=60)
    saved = 0
    async with client:
        convs = await _recent_convs(client)
        for conv in convs:
            try:
                data = await _pull_chain(client, conv)
            except Exception:
                continue
            for creation in _iter_creations(data):
                video = creation.get("video") or {}
                if video.get("status") != 3:
                    continue
                vid = str(video.get("vid", ""))
                if not vid or _already_have(vid):
                    continue
                try:
                    url = await fetch_aispace_original(client, vid)
                    if not url:
                        log_hub.warn(
                            f"未获取到无水印地址，不放入生成结果（vid={vid}）"
                        )
                        continue
                    OUTPUT_DIR.mkdir(exist_ok=True)
                    name = f"自动回收_{conv}_{vid}_无水印v2.mp4"
                    size = await download_video(url, OUTPUT_DIR / name)
                    log_hub.ok(
                        f"自动回收豆包已生成视频：{name}（{size / 1048576:.1f} MB，无水印）"
                    )
                    saved += 1
                except Exception as exc:
                    log_hub.warn(f"自动回收失败 {vid}: {str(exc)[:200]}")
    return saved


async def auto_salvage_loop(pool, interval: float = SCAN_INTERVAL_SEC) -> None:
    """后台循环：定期扫描所有已登录账号并回收新生成的视频。"""
    while True:
        try:
            accounts = pool.snapshot()
            for acc in accounts:
                if acc.get("logged_in") and acc.get("available"):
                    try:
                        await _salvage_account(acc["id"])
                    except Exception as exc:
                        log_hub.warn(f"自动回收账号 {acc['id']} 异常: {str(exc)[:200]}")
        except Exception as exc:
            log_hub.warn(f"自动回收扫描异常: {str(exc)[:200]}")
        await asyncio.sleep(interval)
