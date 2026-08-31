# -*- coding: utf-8 -*-
"""account_pool 单元测试（不联网，只验证调度与风控规则）。"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hub.account_pool import AccountPool, STATUS_QUARANTINE, STATUS_ACTIVE  # noqa: E402
from hub.config import session_file  # noqa: E402


def _fake_login(account_id: str) -> None:
    """给账号造一个会话文件（仅测调度逻辑，不联网）。"""
    sf = session_file(account_id)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text('{"cookies": {"sessionid": "test"}, "params": {}}', encoding="utf-8")


def run():
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "accounts.json"
    pool = AccountPool(db_path=db)
    # 把账号数据与会话文件都放进临时目录，避免污染真实 accounts/
    import hub.config as cfg
    cfg.ACCOUNTS_DIR = tmp / "accounts"
    import hub.account_pool as ap
    ap.session_file = lambda aid: cfg.ACCOUNTS_DIR / aid / ".doubao_session.json"

    # 1. 添加账号
    a1 = pool.add_account("测试一号", quota_limit=3, min_interval_sec=0)
    a2 = pool.add_account("测试二号", quota_limit=3, min_interval_sec=0)
    assert a1.id == "acc01" and a2.id == "acc02"
    _fake_login(a1.id); _fake_login(a2.id)
    print("[1] 添加账号 OK")

    # 2. 调度选择：余量相同选最久未用
    pick = pool.pick_account()
    assert pick is not None and pick.id == "acc01"
    print("[2] 调度选择 OK")

    # 3. 成功上报：额度扣减、换号
    pool.report_success("acc01")
    pool.report_success("acc01")
    acc1 = pool.get("acc01")
    assert acc1.quota_used == 2
    # 余量剩1 <= QUOTA_RESERVE(1) → acc01 不可用，应选 acc02
    pick2 = pool.pick_account()
    assert pick2 is not None and pick2.id == "acc02", f"换号失败: {pick2.id if pick2 else None}"
    print("[3] 额度扣减 + 自动换号 OK")

    # 4. 风控熔断：验证码立即隔离
    pool.report_captcha("acc02")
    acc2 = pool.get("acc02")
    assert acc2.status == STATUS_QUARANTINE and acc2.in_cooldown()
    assert pool.pick_account() is None, "全部隔离后应无可用账号"
    wait = pool.next_wait_seconds()
    assert wait > 0
    print(f"[4] 风控熔断 OK（隔离中，最近恢复等待 {wait/3600:.1f}h）")

    # 5. 连续失败隔离
    a3 = pool.add_account("测试三号", quota_limit=3, min_interval_sec=0)
    _fake_login(a3.id)
    for i in range(3):
        pool.report_failure(a3.id, f"网络错误{i}")
    acc3 = pool.get(a3.id)
    assert acc3.status == STATUS_QUARANTINE and "连续失败" in acc3.cooldown_reason
    print("[5] 连续失败隔离 OK")

    # 6. 冷却到期自动恢复（手动把到期时间改到过去）
    acc2.cooldown_until = (datetime.now(timezone.utc).astimezone() - timedelta(hours=1)).isoformat()
    pool._save()
    pool.sweep()
    acc2b = pool.get("acc02")
    assert acc2b.status == STATUS_ACTIVE and not acc2b.in_cooldown()
    assert pool.pick_account() is not None
    print("[6] 冷却自动恢复 + 回归调度 OK")

    # 7. 手动停用与解禁
    pool.set_status("acc02", "disabled")
    assert pool.get("acc02").status == "disabled"
    pool.set_status("acc02", STATUS_ACTIVE)
    assert pool.get("acc02").status == STATUS_ACTIVE
    print("[7] 手动停用/解禁 OK")

    # 8. 持久化：重新加载数据不丢
    pool2 = AccountPool(db_path=db)
    assert len(pool2.accounts) == 3
    assert pool2.get("acc01").quota_used == 2
    print("[8] 持久化重载 OK")

    # 9. 节流建议
    pool.report_success("acc01")
    assert pool.recommended_delay("acc01") >= 0
    print("[9] 节流建议 OK")

    print("\n全部 9 项自测通过 ✓")


if __name__ == "__main__":
    run()
