# -*- coding: utf-8 -*-
"""抓取豆包「确认生成」按钮的完整确认请求（只抓取并中断，不真正发送，避免风控）。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import submit_v2  # noqa: E402

ACC = sys.argv[1] if len(sys.argv) > 1 else "acc01"
IMG_PATHS = sys.argv[2:] or [
    str(PROJECT / "tmp" / "695215e3.jpg"),
    str(PROJECT / "tmp" / "872c250c.jpg"),
]
OUT = PROJECT / "logs" / "confirm_full_body.json"


async def submit() -> str:
    session_file = PROJECT / "accounts" / ACC / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    images = []
    async with client:
        for path in IMG_PATHS:
            p = Path(path)
            att = await client.upload_image(p.read_bytes(), p.name)
            uri = att.get("uri", "")
            if not uri:
                raise RuntimeError(f"上传失败: {path}")
            images.append({"uri": uri, "name": p.name})
            print("uploaded:", p.name, uri)
        conv, raw = await submit_v2(
            client,
            prompt="生成视频：画面自然运动，保持人物特征",
            ratio="16:9",
            duration=5,
            model="seedance_v2.0",
            images=images,
            trace_log=lambda level, text: print(level, text),
        )
    print("conversation_id:", conv)
    return conv


def capture(conversation_id: str) -> None:
    from playwright.sync_api import sync_playwright

    session = json.loads(
        (PROJECT / "accounts" / ACC / ".doubao_session.json").read_text(encoding="utf-8")
    )
    cookies = [
        {"name": str(k), "value": str(v), "domain": ".doubao.com", "path": "/"}
        for k, v in (session.get("cookies") or {}).items()
    ]
    captured = []
    clicked = [False]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()

        def handle_route(route):
            req = route.request
            body = req.post_data or ""
            if (
                clicked[0]
                and req.method == "POST"
                and "doubao.com/chat/completion" in req.url
                and '"message_status":1' in body
            ):
                captured.append({"url": req.url, "body": body})
                print("CAPTURED FULL CONFIRM BODY, len:", len(body))
                route.abort()
                return
            route.continue_()

        page.route("**/*", handle_route)
        page.goto(f"https://www.doubao.com/chat/{conversation_id}", wait_until="domcontentloaded", timeout=60000)
        print("waiting for confirm container (max 60s)...")
        try:
            page.wait_for_selector('[data-plugin-identifier*="auth_confirm"]', timeout=60000)
            print("confirm container appeared")
        except Exception as exc:
            print("confirm container NOT found:", exc)
            print("body tail:", page.locator("body").inner_text()[-600:])
            browser.close()
            return
        loc = page.locator('[data-plugin-identifier*="auth_confirm"]')
        print("confirm container:", loc.count())
        if loc.count():
            clicked[0] = True
            page.locator("text=确认生成").last.click()
            page.wait_for_timeout(6000)
        browser.close()

    if captured:
        OUT.write_text(json.dumps(captured[0], ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", OUT)
    else:
        print("未捕获到确认请求")


if __name__ == "__main__":
    # 安全闸：本脚本会真实提交豆包生成请求并消耗每日免费额度。
    if "--go" not in sys.argv:
        print("安全闸：此脚本会消耗豆包额度。确认要执行请加参数 --go")
        sys.exit(0)
    conv = asyncio.run(submit())
    capture(conv)
