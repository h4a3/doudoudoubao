# -*- coding: utf-8 -*-
"""提示词合规改写：让豆包按用户真实需求重写，保留核心剧情但降低拒绝概率。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT / "doubao2api") not in sys.path:
    sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from .net import run_with_retry  # noqa: E402
from .video_v2 import get_doubao_version, refresh_doubao_version  # noqa: E402


REWRITE_INSTRUCTION = """你是视频分镜提示词改写助手。
请严格保留用户的核心真实需求：人物关系、场景氛围、剧情推进、机位/运镜意图都不能丢。
把内容改写成符合平台安全规范的含蓄版本：
- 露骨/亲密动作改为含蓄氛围描写（如“贴近、衣袂轻动、呼吸交缠”代替直接动作）；
- 不出现真人肖像、侵权/违规词、危险动作、血腥惊悚词；
- 保留角色设定、服装、场景、光线、声音、镜头运动等分镜信息；
- 必须原样保留提示词里的 @图N(角色) 引用标记（例如 @图1(人物)、@图2(场景)），不要删改；
- 必须保留原始的比例、时长、镜头参数描述，不要改动；
- 只输出改写后的提示词正文，不要解释、不要开头语、不要 Markdown。

需要改写的提示词：
"""


async def rewrite_prompt(session_file, prompt: str) -> str:
    """调用豆包文本能力改写提示词，不消耗视频额度。"""
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)

    async def _call() -> str:
        async with client:
            # 优先检测豆包客户端版本，过低则自动对齐最新版本参数
            await refresh_doubao_version(client)
            _orig_sec = client._security_params
            client._security_params = lambda: {
                **_orig_sec(),
                "pc_version": get_doubao_version(),
                "doubao_pc_version": get_doubao_version(),
                "web_platform": "browser",
            }
            result = await client.chat_completion(
                REWRITE_INSTRUCTION + prompt,
                need_deep_think=0,
            )
            text = (result.text or "").strip()
            if not text:
                raise RuntimeError("豆包未返回改写结果")
            return text

    return await run_with_retry(_call, label="提示词AI改写")
