# -*- coding: utf-8 -*-
"""诊断工具：在豆包网页上点击「确认生成」按钮，抓取真实确认请求（抓完即中断，不真正生成）。

用法：
  python hub/debug_confirm.py [conversation_id] [account_id]
"""
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONV = sys.argv[1] if len(sys.argv) > 1 else "38439494746496514"
ACC = sys.argv[2] if len(sys.argv) > 2 else "acc01"
SESSION_FILE = PROJECT / "accounts" / ACC / ".doubao_session.json"
OUT_FILE = PROJECT / "logs" / f"confirm_capture_{CONV}.json"


def main() -> None:
    from playwright.sync_api import sync_playwright

    session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    cookies = [
        {"name": str(k), "value": str(v), "domain": ".doubao.com", "path": "/"}
        for k, v in (session.get("cookies") or {}).items()
    ]

    captured = []
    clicked = [False]
    ws_frames = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()

        def on_ws(ws):
            print("WEBSOCKET:", ws.url)
            ws.on("framesent", lambda payload: ws_frames.append(("sent", payload)))
            ws.on("framereceived", lambda payload: ws_frames.append(("recv", payload)))

        page.on("websocket", on_ws)

        def handle_route(route):
            req = route.request
            if clicked[0] and req.method == "POST":
                captured.append({
                    "url": req.url,
                    "post_data": (req.post_data or "")[:3000],
                })
            route.continue_()

        page.route("**/*", handle_route)
        url = f"https://www.doubao.com/chat/{CONV}"
        print("open", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        print("title:", page.title())
        print("current url:", page.url)
        shot = PROJECT / "logs" / f"confirm_page_{CONV}.png"
        page.screenshot(path=str(shot), full_page=False)
        print("screenshot:", shot)
        try:
            body_text = page.locator("body").inner_text(timeout=5000)
            print("body text:", body_text[:800])
        except Exception as exc:
            print("body text err:", exc)

        btn = page.locator('[data-plugin-identifier*="creation_portrait_video_auth_confirm"]')
        print("确认生成容器数量:", btn.count())
        html = page.content()
        pos = html.find("确认生成")
        print("html 中确认生成位置:", pos)
        if pos >= 0:
            print("html 片段:", html[max(0, pos - 300):pos + 500])
        try:
            buttons = page.locator("button").all_inner_texts()
            print("页面按钮列表:", [b[:40] for b in buttons][:50])
        except Exception as exc:
            print("button list err:", exc)
        if btn.count() == 0:
            print("页面里没找到确认容器，可能已确认过/已生成/需要重扫会话")
        else:
            clicked[0] = True
            target = page.locator("text=确认生成").last
            print("点击目标:", target.count())
            target.click()
            print("已点击确认生成，等待 20 秒观察请求…")
            page.wait_for_timeout(20000)
            try:
                print("点击后页面文本尾部:", page.locator("body").inner_text(timeout=5000)[-500:])
            except Exception as exc:
                print("inner_text err:", exc)

        browser.close()

    print("捕获到的 POST 请求 URL：")
    for item in captured:
        print("-", item["url"][:200])
    print("捕获到的 WebSocket 帧数：", len(ws_frames))
    for direction, payload in ws_frames[-20:]:
        text = payload[:400]
        if "confirm" in text or "creation" in text or "button" in text or "command" in text:
            print(direction, ":", text)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps({"posts": captured, "ws": ws_frames}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已保存 {len(captured)} 个 POST / {len(ws_frames)} 个 WS 帧到: {OUT_FILE}")


if __name__ == "__main__":
    main()
