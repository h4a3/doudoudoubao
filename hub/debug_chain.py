# -*- coding: utf-8 -*-
"""诊断工具：拉取指定豆包会话的 /im/chain/single 原文并做结构摘要。

用法：
  python hub/debug_chain.py [conversation_id] [account_id]
"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import _pull_chain  # noqa: E402


def _summary(data: dict) -> None:
    print("顶层 keys:", list(data.keys()))
    print("code:", data.get("code"), "| message:", data.get("message"))
    down = (data.get("downlink_body") or {}).get("pull_singe_chain_downlink_body") or {}
    print("downlink keys:", list(down.keys()))
    msgs = down.get("messages") or []
    print("messages 数量:", len(msgs))
    for idx, m in enumerate(msgs):
        print(f"--- message {idx} ---")
        print("  keys:", list(m.keys()))
        content = m.get("content") or ""
        try:
            blocks = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            print("  content 原文:", str(content)[:500])
            continue
        if not isinstance(blocks, list):
            print("  content 类型:", type(blocks).__name__, str(blocks)[:500])
            continue
        for bi, b in enumerate(blocks):
            if not isinstance(b, dict):
                print(f"  block {bi}:", str(b)[:300])
                continue
            c = b.get("content") or {}
            print(f"  block {bi}: block_type={b.get('block_type')}, content keys={list(c.keys())}")
            if "creation_block" in c:
                creations = c["creation_block"].get("creations") or []
                print(f"    creations 数量: {len(creations)}")
                for cr in creations:
                    v = cr.get("video") or {}
                    print(
                        f"    creation id={cr.get('id')} | video.status={v.get('status')} | "
                        f"vid={v.get('vid')} | has_download_url={bool(v.get('download_url'))}"
                    )
                    if v.get("error") or v.get("error_msg"):
                        print(f"    video error: {v.get('error') or v.get('error_msg')}")
            if "text_block" in c:
                print("    text:", (c["text_block"].get("text") or "")[:400])
            if not c:
                print("    content 原文:", json.dumps(b, ensure_ascii=False)[:600])


async def main(conversation_id: str, account_id: str) -> None:
    session_file = PROJECT / "accounts" / account_id / ".doubao_session.json"
    if not session_file.exists():
        print(f"缺少会话文件: {session_file}")
        return
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=60)
    async with client:
        data = await _pull_chain(client, conversation_id)

    out = PROJECT / "logs" / f"chain_debug_{conversation_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"原始响应已保存: {out}")
    _summary(data)


if __name__ == "__main__":
    conv = sys.argv[1] if len(sys.argv) > 1 else "38439494746496514"
    acc = sys.argv[2] if len(sys.argv) > 2 else "acc01"
    asyncio.run(main(conv, acc))
