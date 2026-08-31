# -*- coding: utf-8 -*-
"""对已有会话抓取「确认生成」真实请求体（只抓取并中断，不真正发送）。"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONV = sys.argv[1] if len(sys.argv) > 1 else "38439462527393282"
ACC = sys.argv[2] if len(sys.argv) > 2 else "acc02"
OUT = PROJECT / "logs" / f"confirm_this_{CONV}.json"


def main() -> None:
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
        page.goto(f"https://www.doubao.com/chat/{CONV}", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector('[data-plugin-identifier*="auth_confirm"]', timeout=60000)
        except Exception as exc:
            print("未找到确认按钮:", exc)
            browser.close()
            return
        clicked[0] = True
        page.locator("text=确认生成").last.click()
        page.wait_for_timeout(8000)
        browser.close()

    if captured:
        OUT.write_text(json.dumps(captured[0], ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", OUT)
    else:
        print("未捕获到确认请求")


if __name__ == "__main__":
    main()
