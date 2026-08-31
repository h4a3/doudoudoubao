# -*- coding: utf-8 -*-
"""Playwright 兜底：在豆包网页上自动点击「确认生成」。

只在后台无浏览器重放失败后调用；用账号专属持久化浏览器目录，
页内请求由豆包自己的 JS 注入 a_bogus 签名，成功率最高。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional


def click_confirm_in_browser(
    cookies: Dict[str, str],
    conversation_id: str,
    user_data_dir=None,
    timeout_seconds: int = 120,
) -> bool:
    from playwright.sync_api import sync_playwright

    profile = str(user_data_dir) if user_data_dir else None
    if profile:
        Path(profile).mkdir(parents=True, exist_ok=True)

    pw_cookies = [
        {"name": str(k), "value": str(v), "domain": ".doubao.com", "path": "/"}
        for k, v in (cookies or {}).items()
        if k and v
    ]

    with sync_playwright() as p:
        if profile:
            context = p.chromium.launch_persistent_context(
                profile,
                headless=False,
                viewport={"width": 940, "height": 650},
                args=[
                    "--window-size=1000,720",
                    "--window-position=-2000,-2000",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1280, "height": 800})

        try:
            if pw_cookies:
                context.add_cookies(pw_cookies)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                f"https://www.doubao.com/chat/{conversation_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            try:
                page.wait_for_selector(
                    '[data-plugin-identifier*="auth_confirm"]',
                    timeout=60000,
                )
            except Exception:
                return False

            # 等风控 SDK / msToken 初始化完成，避免 710022002
            page.wait_for_timeout(15000)
            page.locator("text=确认生成").last.click()
            page.wait_for_timeout(8000)
            return True
        except Exception:
            return False
        finally:
            try:
                context.close()
            except Exception:
                pass
