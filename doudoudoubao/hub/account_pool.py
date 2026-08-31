# -*- coding: utf-8 -*-
"""
多账号池与风控调度。

设计目标：把「多豆包账号轮换白嫖免费额度」的账号风险治理做成代码规则——

1. 额度管理   每账号每日生成次数限额，达到即自动换号，不硬顶触发平台限流；
2. 请求节流   同一账号两次请求强制间隔 + 随机抖动，避免机器化突发流量；
3. 风控熔断   触发验证码/连续失败立即隔离冷却，期间绝不再调度该账号；
4. 独立会话   每账号独立会话文件与隔离目录，互不串号、互不连坐；
5. 故障可恢复 冷却到期自动回归，支持人工解禁。

所有账号的 doubao2api 客户端都在 hub 进程内运行（进程内多客户端），
不再采用"每账号一个服务实例"的端口分配方案。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .config import (
    ACCOUNTS_DB,
    ACCOUNTS_DIR,
    CAPTCHA_COOLDOWN_HOURS,
    DAILY_VIDEO_QUOTA,
    FAIL_COOLDOWN_HOURS,
    FAIL_QUARANTINE_THRESHOLD,
    MIN_INTERVAL_SEC,
    QUOTA_RESERVE,
    account_dir,
    session_file,
)

# 账号状态机
STATUS_ACTIVE = "active"          # 可调度
STATUS_QUARANTINE = "quarantine"  # 风控冷却中（到期自动恢复）
STATUS_DISABLED = "disabled"      # 手动停用


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _today() -> str:
    return date.today().isoformat()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class Account:
    id: str
    nickname: str
    status: str = STATUS_ACTIVE
    quota_used: int = 0          # 当日已用点数（10 秒视频耗 2 点）
    quota_date: str = _today()
    quota_limit: int = DAILY_VIDEO_QUOTA
    min_interval_sec: int = MIN_INTERVAL_SEC
    cooldown_until: str = ""
    cooldown_reason: str = ""
    consecutive_failures: int = 0
    total_used: int = 0
    last_used_at: str = ""
    created_at: str = field(default_factory=lambda: _iso(_now()))
    note: str = ""

    def quota_remaining(self) -> int:
        if self.quota_date != _today():
            return self.quota_limit
        return max(0, self.quota_limit - self.quota_used)

    def in_cooldown(self) -> bool:
        if not self.cooldown_until:
            return False
        try:
            until = datetime.fromisoformat(self.cooldown_until)
        except ValueError:
            return False
        return _now() < until

    def available(self) -> bool:
        """是否可参与调度：启用、不在冷却、当日还有余量。"""
        return (
            self.status == STATUS_ACTIVE
            and not self.in_cooldown()
            and self.quota_remaining() > QUOTA_RESERVE
        )

    def logged_in(self) -> bool:
        """该账号是否已有可用会话文件。"""
        return session_file(self.id).exists()


class AccountPool:
    """线程安全的账号池，状态持久化到 accounts.json。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else ACCOUNTS_DB
        self._lock = threading.RLock()
        self.accounts: List[Account] = []
        self._load()

    # -- 持久化 ------------------------------------------------------------

    def _load(self) -> None:
        if not self.db_path.exists():
            self.accounts = []
            return
        try:
            raw = json.loads(self.db_path.read_text(encoding="utf-8"))
            self.accounts = [Account(**item) for item in raw.get("accounts", [])]
        except Exception:
            self.accounts = []

    def _save(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"accounts": [asdict(a) for a in self.accounts]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self.db_path)

    # -- 增删改 ------------------------------------------------------------

    def add_account(
        self,
        nickname: str = "",
        quota_limit: int = DAILY_VIDEO_QUOTA,
        min_interval_sec: int = MIN_INTERVAL_SEC,
        note: str = "",
    ) -> Account:
        with self._lock:
            idx = 1
            taken = {a.id for a in self.accounts}
            while f"acc{idx:02d}" in taken:
                idx += 1
            acc = Account(
                id=f"acc{idx:02d}",
                nickname=nickname or f"账号{idx}",
                quota_limit=quota_limit,
                min_interval_sec=min_interval_sec,
                note=note,
            )
            account_dir(acc.id).mkdir(parents=True, exist_ok=True)
            self.accounts.append(acc)
            self._save()
            return acc

    def remove_account(self, account_id: str) -> bool:
        with self._lock:
            before = len(self.accounts)
            self.accounts = [a for a in self.accounts if a.id != account_id]
            if len(self.accounts) < before:
                self._save()
                return True
            return False

    def set_status(self, account_id: str, status: str) -> bool:
        with self._lock:
            acc = self.get(account_id)
            if not acc:
                return False
            acc.status = status
            if status == STATUS_ACTIVE:
                acc.cooldown_until = ""
                acc.cooldown_reason = ""
                acc.consecutive_failures = 0
            self._save()
            return True

    def rename(self, account_id: str, nickname: str) -> bool:
        with self._lock:
            acc = self.get(account_id)
            if not acc or not nickname.strip():
                return False
            acc.nickname = nickname.strip()
            self._save()
            return True

    def get(self, account_id: str) -> Optional[Account]:
        with self._lock:
            for acc in self.accounts:
                if acc.id == account_id:
                    return acc
            return None

    # -- 调度 --------------------------------------------------------------

    def pick_account(self, exclude_busy: Optional[set] = None) -> Optional[Account]:
        """选出下一个可用账号：优先余量最多，其次最久未使用。"""
        with self._lock:
            exclude = exclude_busy or set()
            candidates = [
                a for a in self.accounts
                if a.available() and a.id not in exclude and a.logged_in()
            ]
            if not candidates:
                return None
            candidates.sort(
                key=lambda a: (-a.quota_remaining(), a.last_used_at or "")
            )
            return candidates[0]

    def next_wait_seconds(self) -> float:
        """若当前无可用账号，返回最近的恢复等待秒数（供前端提示）。"""
        with self._lock:
            waits: List[float] = []
            now = _now()
            for acc in self.accounts:
                if acc.status != STATUS_ACTIVE:
                    continue
                if acc.in_cooldown():
                    try:
                        until = datetime.fromisoformat(acc.cooldown_until)
                        waits.append((until - now).total_seconds())
                    except ValueError:
                        pass
                elif acc.quota_remaining() <= QUOTA_RESERVE:
                    # 额度用尽：等到次日 0 点
                    tomorrow = datetime.combine(
                        date.today(), datetime.min.time(), tzinfo=now.tzinfo
                    )
                    waits.append((tomorrow + timedelta(days=1) - now).total_seconds())
            return min(waits) if waits else 0.0

    def recommended_delay(self, account_id: str) -> float:
        """调用前的建议等待（节流）：距上次使用的间隔 + 随机抖动。"""
        with self._lock:
            acc = self.get(account_id)
            if not acc or not acc.last_used_at:
                return 0.0
            try:
                last = datetime.fromisoformat(acc.last_used_at)
            except ValueError:
                return 0.0
            elapsed = (_now() - last).total_seconds()
            required = acc.min_interval_sec + random.uniform(0, 30)
            return max(0.0, required - elapsed)

    # -- 事件上报 ------------------------------------------------------------

    def report_success(self, account_id: str, cost: int = 1) -> None:
        with self._lock:
            acc = self.get(account_id)
            if not acc:
                return
            if acc.quota_date != _today():
                acc.quota_date = _today()
                acc.quota_used = 0
            acc.quota_used += cost
            acc.total_used += 1
            acc.consecutive_failures = 0
            acc.last_used_at = _iso(_now())
            self._save()

    def report_failure(self, account_id: str, reason: str = "") -> None:
        with self._lock:
            acc = self.get(account_id)
            if not acc:
                return
            acc.consecutive_failures += 1
            if acc.consecutive_failures >= FAIL_QUARANTINE_THRESHOLD:
                self._quarantine(
                    acc,
                    hours=FAIL_COOLDOWN_HOURS,
                    reason=f"连续失败{acc.consecutive_failures}次: {reason}",
                )
            self._save()

    def report_captcha(self, account_id: str) -> None:
        """触发平台风控（验证码 710022004 等）：立即隔离。"""
        with self._lock:
            acc = self.get(account_id)
            if not acc:
                return
            self._quarantine(acc, hours=CAPTCHA_COOLDOWN_HOURS, reason="触发平台风控")
            self._save()

    def _quarantine(self, acc: Account, hours: float, reason: str) -> None:
        acc.status = STATUS_QUARANTINE
        acc.cooldown_until = _iso(_now() + timedelta(hours=hours))
        acc.cooldown_reason = reason

    # -- 自愈：冷却到期自动恢复 ------------------------------------------------

    def sweep(self) -> None:
        with self._lock:
            changed = False
            for acc in self.accounts:
                if acc.status == STATUS_QUARANTINE and not acc.in_cooldown():
                    acc.status = STATUS_ACTIVE
                    acc.cooldown_until = ""
                    acc.cooldown_reason = ""
                    acc.consecutive_failures = 0
                    changed = True
                # 跨天重置额度
                if acc.quota_date != _today():
                    acc.quota_date = _today()
                    acc.quota_used = 0
                    changed = True
            if changed:
                self._save()

    # -- 视图 ----------------------------------------------------------------

    def snapshot(self) -> List[dict]:
        with self._lock:
            self.sweep()
            out = []
            for acc in self.accounts:
                item = asdict(acc)
                item["quota_remaining"] = acc.quota_remaining()
                item["available"] = acc.available()
                item["logged_in"] = acc.logged_in()
                out.append(item)
            return out


def throttle_sleep(seconds: float) -> None:
    """同步节流等待（在 worker 线程/任务中调用）。"""
    if seconds > 0:
        time.sleep(seconds)
