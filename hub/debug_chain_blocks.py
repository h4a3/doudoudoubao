# -*- coding: utf-8 -*-
"""打印链消息的所有 block 类型/文本/按钮/创作状态。"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
conv = sys.argv[1] if len(sys.argv) > 1 else "38439462527393282"
data = json.loads(
    (PROJECT / "logs" / f"chain_debug_{conv}.json").read_text(encoding="utf-8")
)
msgs = data.get("downlink_body", {}).get("pull_singe_chain_downlink_body", {}).get("messages", [])
print("status_code:", data.get("status_code"), data.get("status_desc"))
for i, m in enumerate(msgs):
    print("--- msg", i, "user_type", m.get("user_type"), "index", m.get("index_in_conv"), "---")
    for bi, b in enumerate(m.get("content_block") or []):
        c = b.get("content") or {}
        text = (c.get("text_block") or {}).get("text", "")
        button = c.get("button_block") or {}
        thinking = c.get("thinking_block") or {}
        creation = c.get("creation_block") or {}
        print(f"  block {bi} type={b.get('block_type')} keys={list(c.keys())}")
        if text:
            print("    text:", text[:300])
        if button:
            print("    button:", json.dumps(button, ensure_ascii=False)[:400])
        if thinking:
            print("    thinking:", json.dumps(thinking, ensure_ascii=False)[:200])
        if creation:
            for cr in creation.get("creations") or []:
                v = cr.get("video") or {}
                print("    creation:", cr.get("id"), "status", v.get("status"), "vid", v.get("vid"))
