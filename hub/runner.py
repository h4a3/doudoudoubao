# -*- coding: utf-8 -*-
"""生成流水线：任务队列 + 账号调度 + 视频生成 + 去水印落盘。

每个任务经过这些步骤（每步都写实时日志）：
  创建任务 → 选账号 → 上传参考图 → 提交生成 → 心跳等待 →
  拿视频地址 → 去水印兜底 → 下载保存 → 完成

对 doubao2api 的复用方式：进程内直接调 DoubaoChatClient，
但 generate_video 请求构造部分由本模块重写（generate_video_ex），
以支持：时长参数（5s/10s）、多参考图附件、生成结果 vid 捕获。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .account_pool import AccountPool
from .config import (
    DOUBAO2API_DIR,
    DURATION_FIELD,
    HEARTBEAT_SEC,
    LOGS_DIR,
    MAX_IMAGE_ATTACHMENTS,
    OUTPUT_DIR,
    TMP_DIR,
    VIDEO_EXTRACT_FRAMES,
    VIDEO_MODEL,
    VIDEO_TIMEOUT,
    account_dir,
)
from .diag import diagnose_exception, diagnose_no_video, emit_diagnosis
from .logs import log_hub
from .net import is_network_error, run_with_retry
from .nomark import decode_b64_url, download_video, fetch_original_url_by_vid
from .video_v2 import (
    AUTHORIZATION_STATEMENT,
    FACE_STATEMENT,
    V2SubmitError,
    fetch_aispace_original,
    poll_video_v2,
    refresh_doubao_version,
    submit_v2,
)

PROJECT_TOOLS = DOUBAO2API_DIR.parent / "tools"

if str(DOUBAO2API_DIR) not in sys.path:
    sys.path.insert(0, str(DOUBAO2API_DIR))

from doubao2api.client import DoubaoChatClient, DoubaoChatError  # noqa: E402
from doubao2api.client import GeneratedVideo, VideoGenerationResult  # noqa: E402

# 触发风控/封禁语义的错误关键字（出现即隔离账号）
_RISK_KEYWORDS = ("710022004", "验证码", "captcha", "安全验证", "频繁")


@dataclass
class Task:
    id: str
    prompt: str
    duration: int = 5
    ratio: str = "16:9"
    model: str = "seedance_v2.0"   # 默认 Seedance 2.0 Fast
    mode: str = "image"          # image=文生/图生视频, video=导入视频参考
    auto_confirm: bool = True    # 豆包要求「确认生成」时是否自动确认
    auto_declare: bool = True    # 侵权/真人脸被拒时是否自动声明素材授权并重试
    status: str = "queued"       # queued|running|done|failed
    account_id: str = ""
    message: str = ""            # 给前端的一句话状态
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%m-%d %H:%M:%S"))
    finished_at: str = ""
    video_file: str = ""         # 相对 output/ 的文件名
    cover_url: str = ""
    video_url: str = ""
    duration_actual: float = 0.0
    error: str = ""
    # 中间产物
    ref_image_paths: List[Path] = field(default_factory=list)
    ref_roles: List[str] = field(default_factory=list)
    import_video_path: Optional[Path] = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt[:80],
            "duration": self.duration,
            "ratio": self.ratio,
            "model": self.model,
            "mode": self.mode,
            "auto_confirm": self.auto_confirm,
            "auto_declare": self.auto_declare,
            "status": self.status,
            "account_id": self.account_id,
            "message": self.message,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "video_file": self.video_file,
            "cover_url": self.cover_url,
            "duration_actual": self.duration_actual,
            "ref_image_count": len(self.ref_image_paths),
            "error": self.error,
        }


class TaskRunner:
    def __init__(self, pool: AccountPool):
        self.pool = pool
        self.tasks: Dict[str, Task] = {}
        self._queue: "asyncio.Queue[Task]" = asyncio.Queue()
        self._busy: set = set()          # 正在被占用的账号
        self._worker_started = False
        self._client_cache: Dict[str, DoubaoChatClient] = {}

    # -- 任务提交 ----------------------------------------------------------

    async def submit(
        self,
        prompt: str,
        duration: int,
        ratio: str,
        model: str = "seedance_v2.0",
        mode: str = "image",
        auto_confirm: bool = True,
        auto_declare: bool = True,
        ref_image_paths: Optional[List[Path]] = None,
        ref_roles: Optional[List[str]] = None,
        import_video_path: Optional[Path] = None,
    ) -> Task:
        paths = list(ref_image_paths or [])
        roles = list(ref_roles or [])
        while len(roles) < len(paths):
            roles.append("参考图")
        task = Task(
            id=uuid.uuid4().hex[:8],
            prompt=prompt,
            duration=duration,
            ratio=ratio,
            model=model,
            mode=mode,
            auto_confirm=auto_confirm,
            auto_declare=auto_declare,
            ref_image_paths=paths,
            ref_roles=roles[:len(paths)],
            import_video_path=import_video_path,
        )
        ref_desc = f"｜参考图 {len(paths)} 张" if paths else ""
        self.tasks[task.id] = task
        log_hub.step(
            f"任务 {task.id} 已创建：{prompt[:40]}{'…' if len(prompt) > 40 else ''} "
            f"｜时长 {duration}s ｜比例 {ratio} ｜模式 {mode}{ref_desc}",
            task_id=task.id,
        )
        await self._queue.put(task)
        if not self._worker_started:
            self._worker_started = True
            asyncio.create_task(self._worker())
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    # -- 队列 worker ---------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            task = await self._queue.get()
            try:
                await self._run_task(task)
            except Exception as exc:
                self._fail(task, f"流水线异常: {exc}")
            finally:
                if task.account_id:
                    self._busy.discard(task.account_id)
                self._queue.task_done()

    # -- 单任务流水线 ---------------------------------------------------------

    async def _run_task(self, task: Task) -> None:
        t0 = time.time()
        task.status = "running"
        task.message = "排队中，等待可用账号…"

        # 1. 选账号
        acc = None
        while acc is None:
            acc = self.pool.pick_account(exclude_busy=self._busy)
            if acc is not None:
                break
            wait = self.pool.next_wait_seconds()
            task.message = f"暂无可用账号（额度/风控），约 {int(wait // 60)} 分钟后恢复，继续等待…"
            log_hub.warn(
                f"任务 {task.id} 暂无可用账号：全部处于额度用尽/风控隔离/未登录状态，"
                f"预计 {int(wait // 60)} 分 {int(wait % 60)} 秒后恢复，任务继续排队",
                task_id=task.id,
            )
            await asyncio.sleep(min(30.0, max(5.0, wait)))
        self._busy.add(acc.id)
        task.account_id = acc.id
        delay = self.pool.recommended_delay(acc.id)
        if delay > 0:
            log_hub.log(
                f"账号 {acc.id} 距上次使用太近，节流等待 {int(delay)} 秒（防风控）",
                task_id=task.id, account_id=acc.id, level="info",
            )
            await asyncio.sleep(delay)
        log_hub.step(
            f"任务 {task.id} 调度到账号 {acc.id}（{acc.nickname}，今日余量 {acc.quota_remaining()} 点）",
            task_id=task.id, account_id=acc.id,
        )

        # 2. 建客户端
        try:
            client = await self._get_client(acc.id)
        except Exception as exc:
            self.pool.report_failure(acc.id, f"会话加载失败: {exc}")
            self._fail(task, f"账号 {acc.id} 会话加载失败（可能已过期，请删除后重新扫码登录）: {exc}", acc.id)
            return

        # 3. 生成（先做网络预检，网络故障自动重试）
        try:
            async with client:
                await self._preflight(client, task, acc.id)
                result = await self._generate(client, task, acc.id)
        except DoubaoChatError as exc:
            msg = str(exc)
            if any(k.lower() in msg.lower() for k in _RISK_KEYWORDS):
                self.pool.report_captcha(acc.id)
                self._fail(task, f"触发豆包风控，账号 {acc.id} 已自动隔离冷却，任务失败: {msg}", acc.id)
            elif (
                "豆包审核未通过" in msg
                and task.auto_declare
                and not any(s in task.prompt for s in (AUTHORIZATION_STATEMENT, FACE_STATEMENT))
            ):
                # 自动声明后仍被拒：按“原提示词 + 对应声明”重新排队一个新任务
                statement = (
                    FACE_STATEMENT
                    if any(k in msg for k in ("真人", "人脸", "肖像"))
                    else AUTHORIZATION_STATEMENT
                )
                new_prompt = f"{task.prompt}\n{statement}"
                log_hub.warn(
                    f"声明后仍被拒，已按“原提示词+声明”自动重新排队新任务",
                    task_id=task.id, account_id=acc.id,
                )
                await self.submit(
                    prompt=new_prompt,
                    duration=task.duration,
                    ratio=task.ratio,
                    model=task.model,
                    mode=task.mode,
                    auto_confirm=task.auto_confirm,
                    auto_declare=task.auto_declare,
                    ref_image_paths=task.ref_image_paths,
                    ref_roles=task.ref_roles,
                    import_video_path=task.import_video_path,
                )
                self._fail(
                    task,
                    f"豆包审核未通过，已自动声明并重新排队（新任务已加入队列）: {msg}",
                    acc.id,
                )
            else:
                self.pool.report_failure(acc.id, msg[:120])
                self._fail(task, f"生成失败（账号 {acc.id}）: {msg}", acc.id)
            emit_diagnosis(log_hub, diagnose_exception(msg, task), task_id=task.id, account_id=acc.id)
            return
        except Exception as exc:
            network = is_network_error(str(exc))
            if network:
                log_hub.warn(
                    f"本次为网络/DNS 故障，不累计账号 {acc.id} 的失败次数",
                    task_id=task.id, account_id=acc.id,
                )
            else:
                self.pool.report_failure(acc.id, str(exc)[:120])
            self._fail(task, f"生成异常（账号 {acc.id}）: {exc}", acc.id)
            emit_diagnosis(log_hub, diagnose_exception(str(exc), task), task_id=task.id, account_id=acc.id)
            return

        if not result.videos:
            self.pool.report_failure(acc.id, "响应中无视频")
            self._fail(
                task,
                "豆包返回成功但没有视频数据：通常是内容审核/模型拒绝，具体排查见实时日志的 [诊断] 条目",
                acc.id,
            )
            emit_diagnosis(log_hub, diagnose_no_video(task), task_id=task.id, account_id=acc.id)
            return

        video = result.videos[0]
        task.video_url = video.video_url
        task.duration_actual = video.duration or 0.0
        task.cover_url = video.cover_url or ""
        log_hub.ok(
            f"任务 {task.id} 生成完成：实际时长 {task.duration_actual:.0f}s，"
            f"开始去水印处理与下载",
            task_id=task.id, account_id=acc.id,
        )

        # 4. 去水印兜底 + 下载（网络错误自动重试）
        save_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{task.id}_{acc.id}.mp4"
        dest = OUTPUT_DIR / save_name
        vid = getattr(result, "_vids", [""])[0] if getattr(result, "_vids", None) else ""

        def ntrace(level: str, text: str) -> None:
            log_hub.log(text, level=level, task_id=task.id, account_id=acc.id)

        clean_url = getattr(result, "_clean_url", "") or ""
        if not clean_url:
            self._fail(
                task,
                "未获取到无水印视频地址，未放入生成结果（已尝试 aispace 无水印链路）",
                acc.id,
            )
            return
        log_hub.ok("已换取无水印原始流地址（aispace），开始下载", task_id=task.id)
        final_url = clean_url

        try:
            size = await run_with_retry(
                lambda: download_video(final_url, dest),
                trace=ntrace, label="无水印视频下载",
            )
        except Exception as exc:
            self._fail(task, f"无水印视频下载失败: {exc}", acc.id)
            return

        task.video_file = save_name
        task.status = "done"
        task.finished_at = datetime.now().strftime("%H:%M:%S")
        task.message = f"完成，已保存 {save_name}（{size / 1048576:.1f} MB）"
        # 6-10 秒视频耗 2 点，4-5 秒耗 1 点（豆包网页端点数额度）
        self.pool.report_success(acc.id, cost=2 if task.duration > 5 else 1)
        log_hub.ok(
            f"任务 {task.id} 完成 ✅ 保存至 output/{save_name}（{size / 1048576:.1f} MB，"
            f"总耗时 {time.time() - t0:.0f} 秒）",
            task_id=task.id, account_id=acc.id,
        )

    # -- 生成与心跳 -----------------------------------------------------------

    async def _generate(self, client: DoubaoChatClient, task: Task, account_id: str) -> VideoGenerationResult:
        ref_paths = list(task.ref_image_paths or [])
        ref_roles = list(task.ref_roles or [])
        while len(ref_roles) < len(ref_paths):
            ref_roles.append("参考图")

        # 1. 逐张上传参考图（顺序 = 用户选择顺序，最多 9 张）
        ref_attachments: List[dict] = []
        for i, fp in enumerate(ref_paths):
            role = ref_roles[i] if i < len(ref_roles) else "参考图"
            log_hub.step(
                f"上传参考图 {i + 1}/{len(ref_paths)}（{role}）：{fp.name} …",
                task_id=task.id, account_id=account_id,
            )
            try:
                att = await _upload_image_retry(client, fp.read_bytes(), fp.name, task.id, account_id)
            except Exception as exc:
                log_hub.warn(
                    f"参考图 {i + 1} 上传失败（跳过）：{exc}",
                    task_id=task.id, account_id=account_id,
                )
                continue
            uri = att.get("uri", "")
            if uri:
                ref_attachments.append({"type": "image", "key": uri, "name": fp.name})
                log_hub.ok(
                    f"参考图 {i + 1}/{len(ref_paths)} 上传成功（uri={uri[:40]}…）",
                    task_id=task.id, account_id=account_id,
                )
            else:
                log_hub.warn(
                    f"参考图 {i + 1} 上传未返回 uri（跳过）",
                    task_id=task.id, account_id=account_id,
                )

        # 2. 导入视频 → 抽帧 → 逐帧上传（功能二的兜底路径）
        frame_attachments: List[dict] = []
        if task.import_video_path is not None:
            frames = await extract_video_frames(task.import_video_path, task.id)
            for i, fp in enumerate(frames):
                try:
                    att = await _upload_image_retry(client, fp.read_bytes(), fp.name, task.id, account_id)
                    if att.get("uri"):
                        frame_attachments.append({"type": "image", "key": att["uri"], "name": fp.name})
                        log_hub.ok(
                            f"导入视频抽帧 {i + 1}/{len(frames)} 上传成功",
                            task_id=task.id, account_id=account_id,
                        )
                except Exception as exc:
                    log_hub.warn(
                        f"导入视频抽帧 {i + 1} 上传失败: {exc}",
                        task_id=task.id, account_id=account_id,
                    )

        # 3. 合计最多 9 张附件；超出时优先丢弃多余的抽帧（参考图优先保留）
        room = max(0, MAX_IMAGE_ATTACHMENTS - len(ref_attachments))
        if len(frame_attachments) > room:
            log_hub.warn(
                f"附件总数超过 {MAX_IMAGE_ATTACHMENTS} 张上限，"
                f"只保留前 {room} 张视频抽帧",
                task_id=task.id, account_id=account_id,
            )
            frame_attachments = frame_attachments[:room]
        attachments = ref_attachments + frame_attachments

        if not ref_attachments and not frame_attachments:
            log_hub.warn(
                "没有可用的参考图附件，将按纯文生视频继续",
                task_id=task.id, account_id=account_id,
            )

        # 4. 组装发送给豆包的提示词：@图N(角色) → 参考图N(角色)，并补充角色说明
        prompt = _normalize_prompt_mentions(task.prompt)
        role_lines: List[str] = []
        for i, role in enumerate(ref_roles[:len(ref_attachments)]):
            if role and role != "参考图":
                role_lines.append(f"第{i + 1}张参考图作为{role}参考")
        for i in range(len(frame_attachments)):
            idx = len(ref_attachments) + i + 1
            role_lines.append(f"第{idx}张参考图（来自导入视频抽帧）作为场景参考")
        if role_lines:
            prompt = f"{prompt}\n（参考图说明：{'；'.join(role_lines)}。）"

        log_hub.step(
            f"提交 {task.model} 生成请求（参考图 {len(attachments)} 张，"
            f"时长 {task.duration}s，等待豆包生成，最长 {int(VIDEO_TIMEOUT)} 秒）…",
            task_id=task.id, account_id=account_id,
        )
        gen_task = asyncio.create_task(
            generate_video_ex(
                client,
                prompt=prompt,
                duration=task.duration,
                ratio=task.ratio,
                model=task.model,
                attachments=attachments,
                timeout=VIDEO_TIMEOUT,
                trace_id=task.id,
                auto_confirm=task.auto_confirm,
                auto_declare=task.auto_declare,
                browser_profile_dir=str(account_dir(account_id) / "browser"),
                trace_log=lambda level, text: log_hub.log(
                    text, level=level, task_id=task.id, account_id=account_id
                ),
            )
        )
        heartbeat = asyncio.create_task(
            self._heartbeat(task, account_id, gen_task)
        )
        try:
            result = await gen_task
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return result

    async def _heartbeat(self, task: Task, account_id: str, gen_task: asyncio.Task) -> None:
        """生成期间每 10s 心跳一条日志，让用户知道"还在跑、没卡死"。"""
        start = time.time()
        await asyncio.sleep(HEARTBEAT_SEC)
        while not gen_task.done():
            elapsed = int(time.time() - start)
            task.message = f"豆包生成中，已等待 {elapsed} 秒（高峰期最长可能等 5-10 分钟）…"
            log_hub.log(
                f"任务 {task.id} 生成中…已等待 {elapsed} 秒（豆包免费队列高峰期较慢，属正常）",
                task_id=task.id, account_id=account_id, level="info",
            )
            await asyncio.sleep(HEARTBEAT_SEC)

    # -- 客户端与网络预检 -------------------------------------------------------

    async def _preflight(self, client: DoubaoChatClient, task: Task, account_id: str) -> None:
        """提交前探测 doubao.com 是否可达；网络错误自动重试 3 次。"""
        import aiohttp

        async def _check() -> None:
            async with client.session.get(
                f"{client.BASE_URL}/",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status >= 500:
                    raise RuntimeError(f"豆包首页返回 HTTP {resp.status}")
                await resp.read()

        def trace(level: str, text: str) -> None:
            log_hub.log(text, level=level, task_id=task.id, account_id=account_id)

        await run_with_retry(_check, trace=trace, label="豆包连通性预检")
        log_hub.ok(
            "网络预检通过：doubao.com 可达，开始上传参考图与提交生成请求",
            task_id=task.id, account_id=account_id,
        )

    async def _get_client(self, account_id: str) -> DoubaoChatClient:
        from .config import session_file as sf

        path = str(sf(account_id))
        return DoubaoChatClient.from_session(session_file=path, timeout_seconds=int(VIDEO_TIMEOUT))

    # -- 结果 ------------------------------------------------------------------

    def _fail(self, task: Task, reason: str, account_id: str = "") -> None:
        task.status = "failed"
        task.error = reason
        task.message = reason
        task.finished_at = datetime.now().strftime("%H:%M:%S")
        log_hub.error(f"任务 {task.id} 失败：{reason}", task_id=task.id, account_id=account_id)

    def public_tasks(self, limit: int = 30) -> List[dict]:
        tasks = sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)
        return [t.public() for t in tasks[:limit]]


# ---------------------------------------------------------------------------
# 增强版 generate_video（带时长 / 多附件 / vid 捕获）
# 复用 client 的 _security_params/_extract_async_task_id/_poll_async_video/_parse_video_sse
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"@图(\d+)(?:\(([^)]*)\))?")


def _normalize_prompt_mentions(prompt: str) -> str:
    """把前端插入的 @图N(角色) 转成豆包能看懂的「参考图N(角色)」文本。"""
    return _MENTION_RE.sub(lambda m: f"参考图{m.group(1)}" + (f"（{m.group(2)}）" if m.group(2) else ""), prompt)


async def _upload_image_retry(client, data: bytes, name: str, task_id: str, account_id: str):
    """图片上传（网络错误自动重试 3 次）。"""

    def trace(level: str, text: str) -> None:
        log_hub.log(text, level=level, task_id=task_id, account_id=account_id)

    return await run_with_retry(
        lambda: client.upload_image(data, name),
        trace=trace,
        label=f"图片上传 {name}",
    )


async def generate_video_ex(
    client: DoubaoChatClient,
    prompt: str,
    duration: int = 0,
    ratio: Optional[str] = None,
    model: str = "seedance_v2.0",
    camera_movement: Optional[str] = None,
    attachments: Optional[List[dict]] = None,
    timeout: float = 600,
    trace_id: str = "",
    trace_log=None,
    auto_confirm: bool = True,
    auto_declare: bool = True,
    browser_profile_dir: Optional[str] = None,
) -> VideoGenerationResult:
    """视频生成入口：新版 /chat/completion 协议优先，旧版兜底。

    新版协议（2026-07 抓包）：POST /chat/completion + chat_ability(ability_type=17)
    → SSE_ACK 拿 conversation_id → 轮询 /im/chain/single 取 creation_block 视频。
    旧版 /samantha 协议仅在「新版提交阶段失败」时回退。
    """
    try:
        return await _generate_v2(
            client, prompt, duration, ratio or "16:9", model,
            attachments, timeout, trace_id, trace_log,
            auto_confirm, auto_declare, browser_profile_dir,
        )
    except V2SubmitError as exc:
        if trace_log:
            trace_log("warn", f"新版协议提交失败（{exc}），回退旧版 /samantha 协议重试")
        return await _generate_v1(
            client, prompt, duration, ratio, camera_movement,
            attachments, timeout, trace_id, trace_log,
        )


async def _generate_v2(
    client: DoubaoChatClient,
    prompt: str,
    duration: int,
    ratio: str,
    model: str,
    attachments: Optional[List[dict]],
    timeout: float,
    trace_id: str,
    trace_log,
    auto_confirm: bool = True,
    auto_declare: bool = True,
    browser_profile_dir: Optional[str] = None,
) -> VideoGenerationResult:
    """新版协议：/chat/completion → SSE_ACK → /im/chain/single。"""
    images = [
        {"uri": a["key"], "name": a.get("name", "image.png")}
        for a in (attachments or [])
        if a.get("key")
    ]
    await refresh_doubao_version(client, trace_log)
    conversation_id, raw_submit = await submit_v2(
        client,
        prompt=prompt,
        ratio=ratio,
        duration=duration,
        model=model,
        images=images,
        trace_log=trace_log,
    )
    if trace_log:
        _trace_sse(raw_submit, trace_log, "①新版提交响应")

    found = await poll_video_v2(
        client,
        conversation_id,
        timeout,
        trace_log,
        auto_confirm=auto_confirm,
        auto_declare=auto_declare,
        ratio=ratio,
        duration=duration,
        model=model,
        browser_profile_dir=browser_profile_dir,
    )

    result = VideoGenerationResult(prompt=prompt)
    video = GeneratedVideo(
        video_url=found.get("video_url", ""),
        cover_url=found.get("cover_url", ""),
        duration=float(duration or 0),
    )
    result.videos.append(video)
    vid = found.get("vid", "")
    clean_url = ""
    if vid:
        try:
            clean_url = await fetch_aispace_original(client, vid, trace_log)
            if trace_log and clean_url:
                trace_log("ok", "已通过 aispace「我的创作」换取无水印原流地址")
        except Exception as exc:
            if trace_log:
                trace_log("warn", f"aispace 无水印链路失败（{exc}），稍后走兜底链路")
    try:
        result._clean_url = clean_url or ""  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        result._vids = [vid] if vid else []  # type: ignore[attr-defined]
    except Exception:
        pass
    if trace_log:
        trace_log("ok", "③卡点判定：视频已从 IM 频道取到，进入去水印与下载流程")
    return result


async def _generate_v1(
    client: DoubaoChatClient,
    prompt: str,
    duration: int,
    ratio: Optional[str],
    camera_movement: Optional[str],
    attachments: Optional[List[dict]],
    timeout: float,
    trace_id: str,
    trace_log,
) -> VideoGenerationResult:
    """旧版 /samantha/chat/completion 协议（content_type 2020/2021）。"""
    import aiohttp
    from urllib.parse import urlencode

    content_dict: Dict = {"text": prompt}
    if ratio:
        content_dict["ratio"] = ratio
    if camera_movement:
        content_dict["camera_movement"] = camera_movement
    if duration:
        content_dict[DURATION_FIELD] = duration

    message: Dict = {
        "content": json.dumps(content_dict, ensure_ascii=False),
        "content_type": 2020,  # SamanthaVideoGenerationInput
        "attachments": list(attachments or []),
        "references": [],
        "skill": {
            "skill_type": 17,
            "skill_type_no_default": 17,
            "skill_id": "17",
            "skill_id_no_default": "17",
        },
    }

    sent_event: Dict = {
        "messages": [message],
        "completion_option": {
            "is_regen": False,
            "with_suggest": True,
            "need_create_conversation": True,
            "launch_stage": 1,
            "is_replace": False,
            "is_delete": False,
            "is_ai_playground": False,
            "memory_type": 2,
            "message_from": 0,
            "use_deep_think": False,
            "use_auto_cot": False,
            "resend_for_regen": False,
            "enable_commerce_credit": False,
            "action_bar_skill_id": 17,
        },
        "evaluate_option": {"web_ab_params": ""},
        "local_conversation_id": str(uuid.uuid4()),
        "local_message_id": str(uuid.uuid4()),
    }

    url = f"{client.BASE_URL}/samantha/chat/completion?{urlencode(client._security_params())}"
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Agw-Js-Conv": "str",
    }

    async def _do_submit() -> str:
        async with client.session.post(
            url,
            data=json.dumps(sent_event, ensure_ascii=False),
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise DoubaoChatError(f"generate_video failed ({resp.status}): {error_text[:500]}")
            return (await resp.read()).decode("utf-8", errors="replace")

    raw = await run_with_retry(_do_submit, trace=trace_log, label="旧版协议提交 /samantha/chat/completion")

    if trace_log:
        _trace_sse(raw, trace_log, "①提交响应")

    task_id = client._extract_async_task_id(raw)
    if not task_id:
        # 没有异步任务ID：豆包可能在提交阶段就直接拒绝/返回同步结果
        texts = _extract_video_sse_texts(raw)
        if texts and trace_log:
            trace_log("warn", f"②卡点：豆包未受理异步生成，直接返回文本：{texts[:3]}")
        if any("服务过载" in t or "重试" in t for t in texts):
            raise DoubaoChatError("generate_video: 服务过载，请稍后重试")
        try:
            result = client._parse_video_sse(raw, prompt)
        except Exception:
            _save_sse_dump(raw, trace_id, trace_log)
            raise
        if not result.videos and trace_log:
            trace_log("warn", "②卡点判定：任务没进入异步生成队列，也没有视频URL → 提交阶段就被审核/模型拒绝")
            _save_sse_dump(raw, trace_id, trace_log)
        return _attach_vids(result, raw)

    raw_poll = await _poll_async_video_raw(client, task_id, timeout, trace_log)
    if trace_log:
        _trace_sse(raw_poll, trace_log, f"②轮询响应(task={task_id})")

    try:
        result = client._parse_video_sse(raw_poll, prompt)
    except Exception:
        _save_sse_dump(raw + "\n\n===== poll =====\n\n" + raw_poll, trace_id, trace_log)
        raise

    if not result.videos and trace_log:
        trace_log("warn", "③卡点判定：轮询流已正常结束，但最后没有 content_type=2021 的视频URL → 豆包服务端完成了任务却未产出视频（审核/模型拒绝）")
        _save_sse_dump(raw + "\n\n===== poll =====\n\n" + raw_poll, trace_id, trace_log)

    return _attach_vids(result, raw + raw_poll)


def _extract_video_sse_texts(raw: str) -> List[str]:
    """从视频 SSE 里提取 content_type=2001 的纯文本消息（通常是拒绝/过载原因）。"""
    texts: List[str] = []
    for block in (raw or "").split("\n\n"):
        if not block.strip():
            continue
        data_str = ""
        for line in block.strip().split("\n"):
            if line.startswith("data:"):
                data_str = line[5:].strip()
        if not data_str:
            continue
        try:
            data = json.loads(data_str)
            if data.get("event_type") != 2001:
                continue
            event_data_str = data.get("event_data", "")
            ed = json.loads(event_data_str) if isinstance(event_data_str, str) else (event_data_str or {})
            msg = ed.get("message", {}) if isinstance(ed, dict) else {}
            if msg.get("content_type") != 2001:
                continue
            content_str = msg.get("content", "")
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
            if isinstance(content, dict) and content.get("text"):
                texts.append(str(content["text"]))
        except (json.JSONDecodeError, KeyError, AttributeError):
            continue
    return texts


async def _poll_async_video_raw(
    client: DoubaoChatClient, task_id: str, timeout: float, trace_log=None
) -> str:
    """轮询豆包异步生成任务，返回原始 SSE 文本（保留全部细节供诊断）。"""
    import aiohttp
    from urllib.parse import urlencode

    params = client._security_params()
    params.pop("fp", None)
    url = f"{client.BASE_URL}/samantha/chat/async/stream?{urlencode(params)}"
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Agw-Js-Conv": "str",
    }
    body = json.dumps({"task_id": task_id, "event_id": 0})

    async def _do_poll() -> str:
        async with client.session.post(
            url,
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise DoubaoChatError(
                    f"generate_video async poll failed ({resp.status}): {error_text[:500]}"
                )
            return (await resp.read()).decode("utf-8", errors="replace")

    return await run_with_retry(_do_poll, trace=trace_log, label="旧版协议轮询 async/stream")


def _trace_sse(raw: str, trace_log, title: str) -> None:
    """逐事件解读豆包 SSE，输出到实时日志（最多 25 条明细 + 1 条统计）。

    兼容两种 SSE 格式：
      1. 文本 SSE：event: SSE_ACK / data: {...}
      2. JSON SSE：data: {"event_type":2001,"event_data":"{...}"}
    """
    if not raw or trace_log is None:
        return
    counts: Dict[str, int] = {}
    lines: List[str] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        data_str = ""
        for line in block.strip().split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_str = line[5:].strip()
        if not data_str:
            continue

        # 文本 SSE：按 event 名统计，SSE_ACK 提取会话号，其余不刷屏
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            if event_name:
                counts[event_name] = counts.get(event_name, 0) + 1
                if event_name == "SSE_ACK":
                    lines.append(f"{title} · event=SSE_ACK · 原始={data_str[:200]}")
                elif event_name == "STREAM_ERROR":
                    lines.append(f"{title} · event=STREAM_ERROR · 原始={data_str[:200]}")
            continue

        event_type = data.get("event_type") or event_name
        counts[event_type] = counts.get(event_type, 0) + 1
        if not event_type:
            continue

        event_data_str = data.get("event_data", "")
        try:
            ed = json.loads(event_data_str) if isinstance(event_data_str, str) else (event_data_str or {})
        except json.JSONDecodeError:
            ed = {}
        msg = ed.get("message", {}) if isinstance(ed, dict) else {}
        content_type = msg.get("content_type")
        content = msg.get("content", "")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                pass

        parts: List[str] = []
        if ed.get("is_finish"):
            parts.append("finish=1")
        fin_reason = ed.get("fin_reason") or {}
        if fin_reason:
            parts.append(f"fin_reason={fin_reason}")
        if content_type is not None:
            parts.append(f"content_type={content_type}")
        if isinstance(content, dict):
            keys = [str(k) for k in list(content.keys())[:10]]
            if keys:
                parts.append("字段=" + ",".join(keys))
            for key in ("status", "error_code", "code", "description", "message", "gen_status", "text"):
                if key in content and content[key] is not None:
                    val = str(content[key])[:120]
                    parts.append(f"{key}={val}")
            # 视频/任务结果常藏在 data[] 或 tasks{} 里，再看一层
            inner = content.get("data")
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                for key in ("status", "error_code", "code", "description", "message", "gen_status",
                            "video_url", "url", "vid", "duration", "model", "music_gen_failed",
                            "music_gen_failed_msg"):
                    if inner[0].get(key) is not None:
                        val = str(inner[0][key])[:120]
                        parts.append(f"item.{key}={val}")
            elif isinstance(inner, dict):
                for key in ("status", "error_code", "code", "description", "message"):
                    if inner.get(key) is not None:
                        parts.append(f"data.{key}={str(inner[key])[:120]}")
            tasks = content.get("tasks")
            if isinstance(tasks, dict) and tasks:
                first = next(iter(tasks.values()), {})
                if isinstance(first, dict):
                    for key in ("status", "error_code", "gen_status", "music_gen_failed",
                                "music_gen_failed_msg", "vid", "title"):
                        if first.get(key) is not None:
                            parts.append(f"task.{key}={str(first[key])[:120]}")
        elif isinstance(content, str) and content:
            parts.append(f"内容={content[:120]}")

        if event_type == 2005:
            parts.append(f"event_data={str(event_data_str)[:200]}")
            lines.append(f"{title} · event=2005(错误) · " + " | ".join(parts))
        else:
            lines.append(f"{title} · event={event_type} · " + (" | ".join(parts) if parts else "（无明细）"))

        if len(lines) >= 25:
            break

    if counts:
        trace_log("info", f"{title} 事件统计：{counts}")
    for line in lines:
        trace_log("warn" if "event=2005" in line else "info", line)


def _save_sse_dump(raw: str, trace_id: str, trace_log=None) -> None:
    """失败时把原始 SSE 存到 logs/ 目录，便于事后精确排查。"""
    if not raw:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"video_sse_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{trace_id or 'task'}.txt"
        path = LOGS_DIR / name
        path.write_text(raw[:2_000_000], encoding="utf-8")
        if trace_log:
            trace_log("warn", f"[证据留存] 原始 SSE 已保存：{path}")
    except Exception:
        pass


_VID_RE = re.compile(r'\\{0,4}"vid\\{0,4}":\\{0,4}"(\d{10,30})\\{0,4}"')


def _attach_vids(result: VideoGenerationResult, raw: str) -> VideoGenerationResult:
    vids = list(dict.fromkeys(_VID_RE.findall(raw or "")))
    try:
        result._vids = vids  # type: ignore[attr-defined]
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# 导入视频抽帧（功能二的兜底路径）
# ---------------------------------------------------------------------------

def _find_ffmpeg() -> tuple:
    """定位 ffmpeg / ffprobe：优先项目 tools/ 目录，其次系统 PATH。"""
    import shutil

    local = PROJECT_TOOLS / "ffmpeg.exe"
    if local.exists():
        probe = PROJECT_TOOLS / "ffprobe.exe"
        return str(local), str(probe if probe.exists() else local)
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg, shutil.which("ffprobe") or sys_ffmpeg
    return "", ""


async def extract_video_frames(
    video_path: Path, task_id: str, max_frames: int = VIDEO_EXTRACT_FRAMES
) -> List[Path]:
    """用 ffmpeg 从导入视频抽首帧与关键帧作为参考图。不可用时返回空列表。"""
    ffmpeg, ffprobe = _find_ffmpeg()
    if not ffmpeg:
        log_hub.warn(
            "未找到 ffmpeg，无法从导入视频抽帧；将仅使用参考图生成。"
            "请把 ffmpeg.exe / ffprobe.exe 放入项目 tools/ 目录，或安装后加入 PATH。",
            task_id=task_id,
        )
        return []

    out_dir = TMP_DIR / f"frames_{task_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: List[Path] = []

    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
        str(video_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    duration = 0.0
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        data = json.loads(out.decode("utf-8", errors="replace") or "{}")
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except Exception:
        pass

    stamps = [0.0]
    if duration > 4:
        stamps = [0.0, duration * 0.5, duration * 0.9][:max_frames]

    for i, ts in enumerate(stamps):
        out_file = out_dir / f"frame_{i}.jpg"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3", str(out_file),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
            if out_file.exists() and out_file.stat().st_size > 0:
                frames.append(out_file)
        except asyncio.TimeoutError:
            proc.kill()

    log_hub.log(
        f"导入视频抽帧完成：{len(frames)} 帧（时长 {duration:.1f}s）",
        task_id=task_id, level="info",
    )
    return frames
