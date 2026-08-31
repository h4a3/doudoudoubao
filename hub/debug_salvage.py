# -*- coding: utf-8 -*-
"""把豆包会话里 status=3 的视频下载回 output/（优先无水印原流）。

用法：python hub/debug_salvage.py [conversation_id ...]
不带参数时默认回收之前的两条调试会话。
"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from hub.nomark import download_video, fetch_original_url_by_vid  # noqa: E402
from hub.video_v2 import _iter_creations  # noqa: E402

CONVS = sys.argv[1:] or [
    "38439427246941698",
    "38439360275270914",
]


def collect_creations(conv: str):
    data = json.loads((PROJECT / "logs" / f"chain_debug_{conv}.json").read_text(encoding="utf-8"))
    out = []
    for creation in _iter_creations(data):
        video = creation.get("video") or {}
        if video.get("status") == 3 and video.get("download_url"):
            out.append({
                "vid": str(video.get("vid", "")),
                "download_url": video["download_url"],
            })
    return out


async def main():
    output = PROJECT / "output"
    output.mkdir(exist_ok=True)
    done = 0
    for conv in CONVS:
        for item in collect_creations(conv):
            vid = item["vid"]
            print("vid:", vid)
            clean = await fetch_original_url_by_vid(vid)
            url = clean or item["download_url"]
            name = f"测试回收_{conv}_{vid}.mp4"
            try:
                size = await download_video(url, output / name)
                print("saved:", name, f"{size / 1048576:.1f} MB", "无水印" if clean else "水印版")
                done += 1
            except Exception as exc:
                print("download failed:", name, exc)
    print("DONE", done)


if __name__ == "__main__":
    asyncio.run(main())
