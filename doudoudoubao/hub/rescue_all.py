# -*- coding: utf-8 -*-
"""用 aispace 无水印链路重新下载指定 vid 的视频。"""
import asyncio
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.nomark import download_video  # noqa: E402
from hub.video_v2 import fetch_aispace_original  # noqa: E402

JOBS = [
    ("v0269cg10004da9qku27dld43grtvu70", "豆包最新生成"),
    ("v0369cg10004da9qbj27dld66q4g9a00", "测试回收_38439427246941698"),
    ("v0269cg10004da9qbpq7dld9jvsv2gc0", "测试回收_38439427246941698"),
    ("v0269cg10004da9qc0a7dld806i884ig", "测试回收_38439360275270914"),
]


async def main() -> None:
    session_file = PROJECT / "accounts" / "acc01" / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    async with client:
        for vid, prefix in JOBS:
            try:
                url = await fetch_aispace_original(client, vid)
                if not url:
                    print(vid, "未取到 aispace 地址")
                    continue
                dest = PROJECT / "output" / f"{prefix}_{vid}_无水印v2.mp4"
                size = await download_video(url, dest)
                print(vid, "saved", dest.name, f"{size / 1048576:.1f} MB")
            except Exception as exc:
                print(vid, "ERROR", str(exc)[:200])


if __name__ == "__main__":
    asyncio.run(main())
