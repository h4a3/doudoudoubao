# -*- coding: utf-8 -*-
"""对指定会话执行一次真实浏览器「确认生成」点击。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from hub.browser_confirm import click_confirm_in_browser  # noqa: E402


def main(conv: str, acc: str) -> None:
    session_file = PROJECT / "accounts" / acc / ".doubao_session.json"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    cookies = session.get("cookies") or {}
    profile = str(PROJECT / "accounts" / acc / "browser")
    ok = click_confirm_in_browser(cookies, conv, profile)
    print("CLICKED:", ok)


if __name__ == "__main__":
    conv = sys.argv[1] if len(sys.argv) > 1 else "38439462527393282"
    acc = sys.argv[2] if len(sys.argv) > 2 else "acc02"
    main(conv, acc)
