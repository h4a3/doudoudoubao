# -*- coding: utf-8 -*-
"""用 aispace get_download_info 拿到的地址重新下载视频。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from hub.nomark import download_video  # noqa: E402


async def main(vid: str, out_name: str) -> None:
    info_path = PROJECT / "logs" / f"aispace_download_{vid}.json"
    data = json.loads(info_path.read_text(encoding="utf-8"))
    infos = (data.get("data") or {}).get("download_infos") or []
    if not infos:
        print("没有 download_infos")
        return
    url = infos[0].get("main_url") or infos[0].get("url")
    print("main_url:", url[:160])
    dest = PROJECT / "output" / out_name
    size = await download_video(url, dest)
    print("saved:", dest, f"{size / 1048576:.1f} MB")


if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else "v0269cg10004da9qku27dld43grtvu70"
    name = sys.argv[2] if len(sys.argv) > 2 else f"豆包最新生成_{vid}_无水印v2.mp4"
    asyncio.run(main(vid, name))
