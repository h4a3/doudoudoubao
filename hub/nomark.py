# -*- coding: utf-8 -*-
"""去水印模块：移植自 ihmily/doubao-nomark（MIT）。

原理：豆包网页端"下载"按钮给的视频是服务端烧录水印后的版本，
而凭视频 vid 调用 /samantha/media/get_play_info 拿到的
original_media_info.main_url 是生成原始流（无水印）。
"""
from __future__ import annotations

import base64
from typing import Optional

import httpx

from .config import DOWNLOAD_TIMEOUT

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
        "NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) "
        "WindowsWechat(0x63090c33) XWEB/14315 Flue"
    ),
    "origin": "https://www.doubao.com",
}

_PARAMS = {
    "version_code": "20800",
    "language": "zh-CN",
    "device_platform": "web",
    "aid": "497858",
    "real_aid": "497858",
    "pkg_type": "release_version",
    "device_id": "",
    "pc_version": "2.51.7",
    "region": "",
    "sys_region": "",
    "samantha_web": "1",
    "use-olympus-account": "1",
    "web_tab_id": "",
}


async def fetch_original_url_by_vid(vid: str) -> Optional[str]:
    """凭视频 vid 换取无水印原始流地址。失败返回 None（不抛异常）。"""
    if not vid:
        return None
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            resp = await client.post(
                "https://www.doubao.com/samantha/media/get_play_info",
                params=_PARAMS,
                headers=_HEADERS,
                json={"key": vid},
            )
            result = resp.json()
            media = result.get("data", {}).get("original_media_info", {})
            url = media.get("main_url", "")
            return url or None
    except Exception:
        return None


async def download_video(url: str, dest_path, chunk: int = 1 << 20) -> int:
    """下载视频到本地，返回字节数。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"User-Agent": _HEADERS["User-Agent"]}) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                async for data in resp.aiter_bytes(chunk):
                    f.write(data)
                    total += len(data)
    return total


def decode_b64_url(value: str) -> str:
    """doubao2api 风格的 base64 URL 解码（容错）。"""
    if not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except Exception:
        return value
