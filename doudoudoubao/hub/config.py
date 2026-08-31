# -*- coding: utf-8 -*-
"""全局配置：目录布局、生成参数与风控默认参数。"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 目录布局
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # E:\doudoudoubao
ACCOUNTS_DIR = PROJECT_ROOT / "accounts"                # 账号池数据根目录
ACCOUNTS_DB = ACCOUNTS_DIR / "accounts.json"            # 账号池状态文件
OUTPUT_DIR = PROJECT_ROOT / "output"                    # 生成视频输出目录
LOGS_DIR = PROJECT_ROOT / "logs"                        # 运行日志
TMP_DIR = PROJECT_ROOT / "tmp"                          # 上传/任务临时文件

# 基座项目路径（逆向库，进程内直接 import）
DOUBAO2API_DIR = PROJECT_ROOT / "doubao2api"
NOMARK_DIR = PROJECT_ROOT / "doubao-nomark"
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

# Web 服务
WEB_HOST = "127.0.0.1"
WEB_PORT = int(os.environ.get("DOUBAO_HUB_PORT", "8000"))


def account_dir(account_id: str) -> Path:
    """单个账号的隔离目录（会话文件都存这里）。"""
    return ACCOUNTS_DIR / account_id


def session_file(account_id: str) -> Path:
    """账号会话文件（doubao2api 的 .doubao_session.json 格式）。"""
    return account_dir(account_id) / ".doubao_session.json"


# ---------------------------------------------------------------------------
# 生成参数
# ---------------------------------------------------------------------------
VIDEO_TIMEOUT = float(os.environ.get("DOUBAO_VIDEO_TIMEOUT", "600"))  # 单次生成最长等待（秒）
HEARTBEAT_SEC = 10.0          # 生成等待期的心跳日志间隔
DOWNLOAD_TIMEOUT = 120.0      # 视频下载超时（秒）

# 豆包网页端时长字段名。抓包确认前的候选字段，按序注入 content_dict；
# 若实测 4-10s 不生效，把 DOUBAO_DURATION_FIELD 改成抓包到的字段名即可。
DURATION_FIELD = os.environ.get("DOUBAO_DURATION_FIELD", "duration")
DURATION_CHOICES = tuple(range(4, 11))   # 4-10 秒（滑条选择）
VIDEO_MODEL = os.environ.get("DOUBAO_VIDEO_MODEL", "seedance_v2.0")  # 默认 Seedance 2.0 Fast
VIDEO_MODEL_CHOICES = {
    "seedance_v2.0": "Seedance 2.0 Fast（默认/快速）",
    "seedance_v2.0_std": "Seedance 2.0（标准/进阶，2倍消耗）",
    "seedance_v2.0_mini": "Seedance 2.0 Mini（日常）",
}

# 参考图相关限制（来自 DoubaoManager 2026-07 抓包：图生最多 9 张图）
MAX_IMAGE_ATTACHMENTS = 9          # 一次生成最多携带的参考图数量
VIDEO_EXTRACT_FRAMES = 3           # 导入视频最多抽取的帧数（首/中/尾）
MAX_REF_IMAGES_WITH_VIDEO = MAX_IMAGE_ATTACHMENTS - VIDEO_EXTRACT_FRAMES  # 6
REF_ROLES = ("参考图", "人物", "场景", "音色")  # 可选的角色标注

# ---------------------------------------------------------------------------
# 风控参数（账号风险治理的默认值，均可在添加账号时覆盖）
# ---------------------------------------------------------------------------
DAILY_VIDEO_QUOTA = 10        # 单账号每日视频额度（豆包网页端实测约 10 点，10 秒视频耗 2 点）
MIN_INTERVAL_SEC = 60         # 同一账号两次生成之间的最小间隔
JITTER_SEC = 30               # 在最小间隔之上附加的随机抖动上限（秒）
CAPTCHA_COOLDOWN_HOURS = 6    # 触发风控验证码后的隔离时长（小时）
FAIL_COOLDOWN_HOURS = 2       # 连续失败达阈值后的隔离时长（小时）
FAIL_QUARANTINE_THRESHOLD = 3 # 连续失败几次后隔离
QUOTA_RESERVE = 1             # 至少保留的余量：余量低于该值即换号，避免在临界点触发限流
