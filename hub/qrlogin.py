# -*- coding: utf-8 -*-
"""扫码登录管理：包装 doubao2api 的 QRLogin，供 Web 前端轮询。

流程：
  POST /api/accounts/qr/start   → 起后台线程拉二维码并轮询状态
  GET  /api/accounts/qr/status  → 前端 1s 轮询，拿二维码 base64 与状态
  登录成功 → 会话写入 accounts/<id>/.doubao_session.json → 账号入池

针对「获取二维码时间过长 / 失败」的加固：
  1. doubao2api 的 QRLogin 在后台线程执行，任何异常都会转成 status=error，
     错误文案原样展示给前端，而不是接口 500；
  2. 看门狗：超过 QR_FETCH_TIMEOUT_SEC 还没拿到二维码，自动取消并报超时，
     前端不会无限转圈；
  3. 启动线程失败会回滚预创建的账号，取消/超时会清掉账号和残留状态，
     保证下一次「重新获取」一定可用。
"""
from __future__ import annotations

import base64
import threading
import time
from typing import Optional

from .account_pool import AccountPool, STATUS_ACTIVE
from .config import session_file
from .logs import log_hub

# 拉二维码阶段的最长等待时间（正常约 1-3 秒，留足网络抖动余量）
QR_FETCH_TIMEOUT_SEC = 35.0
WATCHDOG_TICK_SEC = 1.0

_STATUS_TEXT = {
    "idle": "",
    "fetching": "正在获取二维码…",
    "waiting": "等待扫码…",
    "scanned": "已扫码，请在手机上确认",
    "confirmed": "登录成功！",
    "expired": "二维码已过期，请点击「重新获取」",
    "error": "出错",
}


class QRLoginManager:
    """同一时刻只允许一个进行中的扫码登录。"""

    def __init__(self, pool: AccountPool):
        self._pool = pool
        self._qr = None          # QRLogin 实例
        self._account_id = ""    # 登录成功后归属的账号
        self._status = "idle"    # idle|fetching|waiting|scanned|confirmed|expired|error
        self._error = ""
        self._started_at = 0.0   # 本次流程开始时间（秒），用于展示等待时长
        self._watchdog: Optional[threading.Thread] = None
        self._flow_id = 0        # 流程代次：取消/重开后，旧线程的回调一律作废
        self._lock = threading.RLock()

    # -- 对外状态 ----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            qr_b64 = ""
            if self._qr is not None and self._qr.qrcode_data:
                qr_b64 = base64.b64encode(self._qr.qrcode_data).decode("ascii")
            elapsed = 0
            if self._started_at and self._status in ("fetching", "waiting", "scanned"):
                elapsed = max(0, int(time.time() - self._started_at))
            return {
                "status": self._status,
                "status_text": _STATUS_TEXT.get(self._status, ""),
                "qr_base64": qr_b64,
                "account_id": self._account_id,
                "error": self._error,
                "elapsed_seconds": elapsed,
            }

    # -- 启动/取消 ----------------------------------------------------------

    def start(self, nickname: str = "") -> dict:
        with self._lock:
            if self._status not in ("idle", "expired", "error", "confirmed"):
                return {"ok": False, "error": f"已有登录流程进行中（{self._status}）"}

            # 延迟 import：保证无 doubao2api 时其余功能可用
            try:
                import sys
                from .config import DOUBAO2API_DIR
                if str(DOUBAO2API_DIR) not in sys.path:
                    sys.path.insert(0, str(DOUBAO2API_DIR))
                from doubao2api.qr_login import QRLogin, QRStatus  # noqa: F401
            except Exception as exc:
                log_hub.error(f"加载豆包登录组件失败: {exc}")
                return {"ok": False, "error": f"加载豆包登录组件失败: {exc}"}

            acc = None
            try:
                self._flow_id += 1
                flow = self._flow_id

                acc = self._pool.add_account(nickname=nickname)
                self._pool.set_status(acc.id, STATUS_ACTIVE)

                self._qr = QRLogin()
                self._account_id = acc.id
                self._error = ""
                self._status = "fetching"
                self._started_at = time.time()
                self._watchdog = None

                log_hub.step(
                    f"开始为 {acc.id}（{acc.nickname}）获取登录二维码"
                    f"（正常 1-3 秒，最多等 {int(QR_FETCH_TIMEOUT_SEC)} 秒）"
                )

                def on_status(status: "QRStatus", message: str) -> None:
                    with self._lock:
                        if flow != self._flow_id:
                            return  # 该流程已被取消/重开，丢弃旧线程回调
                    mapping = {
                        QRStatus.FETCHING_QR: "fetching",
                        QRStatus.WAITING_SCAN: "waiting",
                        QRStatus.SCANNED: "scanned",
                        QRStatus.CONFIRMED: "confirmed",
                        QRStatus.EXPIRED: "expired",
                        QRStatus.ERROR: "error",
                    }
                    with self._lock:
                        if flow != self._flow_id:
                            return
                        self._status = mapping.get(status, self._status)
                        if status == QRStatus.ERROR:
                            self._error = message
                    log_hub.log_threadsafe(
                        f"[{acc.id}] 扫码状态: {status.value} {message}".strip(),
                        level="warn" if status in (QRStatus.EXPIRED, QRStatus.ERROR) else "info",
                        account_id=acc.id,
                    )

                def on_done(result) -> None:
                    try:
                        with self._lock:
                            if flow != self._flow_id:
                                return
                        self._finish(acc, result)
                    except Exception as exc:  # 不让线程静默死亡
                        with self._lock:
                            if flow != self._flow_id:
                                return
                            self._status = "error"
                            self._error = str(exc)
                            self._account_id = ""
                            self._qr = None
                            self._started_at = 0.0
                        log_hub.log_threadsafe(
                            f"[{acc.id}] 扫码登录异常: {exc}", level="error", account_id=acc.id
                        )

                self._qr.start(on_status=on_status, on_done=on_done)
                self._watchdog = threading.Thread(
                    target=self._watch_fetch, args=(flow,), daemon=True
                )
                self._watchdog.start()
                return {"ok": True, "account_id": acc.id}
            except Exception as exc:
                # 线程启动失败等异常：回滚预创建的账号与状态，绝不让接口 500
                if acc is not None:
                    try:
                        self._pool.remove_account(acc.id)
                    except Exception:
                        pass
                self._qr = None
                self._account_id = ""
                self._status = "error"
                self._error = f"启动扫码失败: {exc}"
                self._started_at = 0.0
                log_hub.error(f"启动扫码登录失败: {exc}")
                return {"ok": False, "error": self._error}

    def cancel(self) -> dict:
        with self._lock:
            self._flow_id += 1  # 作废旧流程的所有线程回调
            qr = self._qr
            acc_id = self._account_id
            was = self._status
            self._qr = None
            self._account_id = ""
            self._status = "idle"
            self._error = ""
            self._started_at = 0.0
            self._watchdog = None

        if qr is not None:
            try:
                qr.cancel()
            except Exception:
                pass

        # 已登录成功的不删账号；其余流程里的预创建账号都清理掉
        if acc_id and was != "confirmed":
            try:
                self._pool.remove_account(acc_id)
            except Exception:
                pass

        log_hub.log("已取消扫码登录", level="warn")
        return {"ok": True}

    # -- 看门狗：拉二维码阶段超时自动中止 -------------------------------------

    def _watch_fetch(self, flow: int) -> None:
        deadline = time.time() + QR_FETCH_TIMEOUT_SEC
        while time.time() < deadline:
            time.sleep(WATCHDOG_TICK_SEC)
            with self._lock:
                if flow != self._flow_id:
                    return  # 流程已取消/重开
                if self._status != "fetching":
                    return
                if self._qr is not None and self._qr.qrcode_data:
                    return  # 已出图，交给 doubao2api 轮询扫码

        with self._lock:
            if flow != self._flow_id or self._status != "fetching":
                return
            self._status = "error"
            self._error = (
                f"获取二维码超时（超过 {int(QR_FETCH_TIMEOUT_SEC)} 秒）。"
                "请点击「重新获取」再试；若多次失败，通常是网络不通或豆包临时风控，稍后再试。"
            )
            qr = self._qr
            acc_id = self._account_id
            self._qr = None
            self._account_id = ""
            self._started_at = 0.0

        if qr is not None:
            try:
                qr.cancel()
            except Exception:
                pass
        if acc_id:
            try:
                self._pool.remove_account(acc_id)
            except Exception:
                pass
        log_hub.error(
            f"[{acc_id}] 获取二维码超时，已自动取消。请稍后重试或检查网络/豆包风控。",
            account_id=acc_id,
        )

    # -- 完成 ---------------------------------------------------------------

    def _finish(self, acc, result) -> None:
        # 取消/看门狗已经处理过该账号时，后到的 on_done 不再覆盖状态
        with self._lock:
            if self._account_id != acc.id:
                return

        if result is None or result.status.name != "CONFIRMED":
            is_error = result is None or result.status.name == "ERROR"
            err = getattr(result, "error", "") or "扫码未完成或已过期"
            with self._lock:
                self._status = "error" if is_error else "expired"
                self._error = err
                self._account_id = ""
                self._qr = None
                self._started_at = 0.0
            self._pool.remove_account(acc.id)
            log_hub.error(
                f"[{acc.id}] 扫码登录{'失败' if is_error else '过期'}: {err}",
                account_id=acc.id,
            )
            return

        # 会话文件：doubao2api 的 .doubao_session.json 格式
        path = session_file(acc.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json_dumps({
                "cookies": result.cookies,
                "params": {
                    "device_id": result.device_params.get("device_id", ""),
                    "web_id": result.device_params.get("web_id", ""),
                    "fp": result.device_params.get("fp", ""),
                    "fp_verified": True,
                },
            }),
            encoding="utf-8",
        )
        with self._lock:
            self._status = "confirmed"
            self._error = ""
            self._qr = None
            self._started_at = 0.0
        log_hub.ok(
            f"[{acc.id}] 扫码登录成功，会话已保存到 {path.name}，可以开始生成视频了",
            account_id=acc.id,
        )


def json_dumps(data) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2)
