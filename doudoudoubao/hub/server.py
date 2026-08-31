# -*- coding: utf-8 -*-
"""豆包视频工作台 Web 服务。

启动：  .venv/Scripts/python.exe -m hub.server
访问：  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .account_pool import AccountPool
from .auto_salvage import auto_salvage_loop
from .config import (
    DURATION_CHOICES,
    MAX_IMAGE_ATTACHMENTS,
    MAX_REF_IMAGES_WITH_VIDEO,
    OUTPUT_DIR,
    REF_ROLES,
    TMP_DIR,
    VIDEO_MODEL_CHOICES,
    WEB_HOST,
    WEB_PORT,
    session_file,
)
from .logs import log_hub, sse_format
from .qrlogin import QRLoginManager
from .rewrite import rewrite_prompt
from .runner import TaskRunner

app = FastAPI(title="豆包视频工作台", version="0.1.0")

pool = AccountPool()
qr_manager = QRLoginManager(pool)
runner = TaskRunner(pool)

STATIC_DIR = Path(__file__).resolve().parent / "static"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def _startup() -> None:
    log_hub.bind_loop(asyncio.get_running_loop())
    log_hub.ok(f"豆包视频工作台已启动 → http://{WEB_HOST}:{WEB_PORT}")
    log_hub.log("提示：先在「账号管理」扫码登录豆包账号，再提交生成任务", level="info")
    log_hub.log("已启动自动回收：每 45 秒扫描豆包最近会话，发现新生成视频自动下载到「生成结果」", level="info")
    asyncio.create_task(auto_salvage_loop(pool))


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return RedirectResponse("/app/")


@app.get("/app/")
async def app_page():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# 账号管理
# ---------------------------------------------------------------------------

@app.get("/api/state")
async def api_state():
    return {
        "accounts": pool.snapshot(),
        "tasks": runner.public_tasks(),
        "qr": qr_manager.snapshot(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }


@app.post("/api/accounts/qr/start")
async def qr_start(nickname: str = Form("")):
    try:
        result = qr_manager.start(nickname=nickname)
    except Exception as exc:
        log_hub.error(f"启动扫码登录异常: {exc}")
        raise HTTPException(500, f"启动扫码登录失败: {exc}") from exc
    if not result.get("ok"):
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/accounts/qr/status")
async def qr_status():
    return qr_manager.snapshot()


@app.post("/api/accounts/qr/cancel")
async def qr_cancel():
    try:
        return qr_manager.cancel()
    except Exception as exc:
        log_hub.error(f"取消扫码登录异常: {exc}")
        raise HTTPException(500, f"取消失败: {exc}") from exc


@app.post("/api/accounts/{account_id}/toggle")
async def account_toggle(account_id: str):
    acc = pool.get(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    from .account_pool import STATUS_ACTIVE, STATUS_DISABLED
    new_status = STATUS_DISABLED if acc.status == STATUS_ACTIVE else STATUS_ACTIVE
    pool.set_status(account_id, new_status)
    log_hub.log(f"账号 {account_id} 已{'启用' if new_status == STATUS_ACTIVE else '停用'}", level="info")
    return {"ok": True, "status": new_status}


@app.delete("/api/accounts/{account_id}")
async def account_delete(account_id: str):
    acc = pool.get(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    pool.remove_account(account_id)
    sf = session_file(account_id)
    if sf.exists():
        sf.unlink()
    log_hub.warn(f"账号 {account_id} 已删除（含会话文件）", account_id=account_id)
    return {"ok": True}


@app.post("/api/accounts/{account_id}/rename")
async def account_rename(account_id: str, nickname: str = Form(...)):
    if not pool.rename(account_id, nickname):
        raise HTTPException(404, "账号不存在或昵称为空")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 生成任务
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".webm", ".avi"}


def _save_upload(upload: UploadFile, dest_dir: Path, allowed_ext: set) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(400, f"不支持的文件类型 {suffix}，允许：{sorted(allowed_ext)}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex[:8]}{suffix}"
    with open(dest, "wb") as f:
        while chunk := upload.file.read(1 << 20):
            f.write(chunk)
    return dest


@app.post("/api/generate")
async def api_generate(
    prompt: str = Form(...),
    duration: int = Form(5),
    ratio: str = Form("16:9"),
    model: str = Form("seedance_v2.0"),
    mode: str = Form("image"),
    ref_images: Optional[List[UploadFile]] = File(default=None),
    ref_image: Optional[UploadFile] = File(None),  # 兼容旧版单图字段
    ref_roles: str = Form("[]"),
    import_video: Optional[UploadFile] = File(None),
    auto_confirm: str = Form("true"),
    auto_declare: str = Form("true"),
):
    auto_confirm_bool = str(auto_confirm).strip().lower() not in ("0", "false", "no", "off")
    auto_declare_bool = str(auto_declare).strip().lower() not in ("0", "false", "no", "off")
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(400, "提示词不能为空")
    if duration not in DURATION_CHOICES:
        raise HTTPException(400, f"时长仅支持 {DURATION_CHOICES} 秒")
    if ratio not in ("16:9", "9:16", "1:1"):
        raise HTTPException(400, "比例仅支持 16:9 / 9:16 / 1:1")
    if model not in VIDEO_MODEL_CHOICES:
        raise HTTPException(400, f"不支持的视频模型，可选：{list(VIDEO_MODEL_CHOICES)}")
    if mode not in ("image", "video"):
        raise HTTPException(400, "mode 仅支持 image / video")

    # 参考图：支持多张（新字段 ref_images），也兼容旧的单图 ref_image
    uploads: List[UploadFile] = [u for u in (ref_images or []) if u and u.filename]
    if ref_image and ref_image.filename:
        uploads.append(ref_image)
    try:
        roles: List[str] = json.loads(ref_roles or "[]")
        if not isinstance(roles, list):
            roles = []
    except json.JSONDecodeError:
        roles = []
    roles = [r if r in REF_ROLES else "参考图" for r in roles]

    if len(uploads) > MAX_IMAGE_ATTACHMENTS:
        raise HTTPException(400, f"参考图最多 {MAX_IMAGE_ATTACHMENTS} 张")
    if mode == "video":
        if len(uploads) > MAX_REF_IMAGES_WITH_VIDEO:
            raise HTTPException(
                400,
                f"导入视频会再抽取最多 3 帧作为参考，"
                f"参考图最多只能 {MAX_REF_IMAGES_WITH_VIDEO} 张（合计 ≤{MAX_IMAGE_ATTACHMENTS}）",
            )

    if mode == "video" and not (import_video and import_video.filename):
        raise HTTPException(400, "视频参考模式需要上传一个视频文件")

    ref_paths = [_save_upload(u, TMP_DIR, ALLOWED_IMAGE_EXT) for u in uploads]
    video_path = _save_upload(import_video, TMP_DIR, ALLOWED_VIDEO_EXT) if import_video and import_video.filename else None

    task = await runner.submit(
        prompt=prompt,
        duration=duration,
        ratio=ratio,
        model=model,
        mode=mode,
        auto_confirm=auto_confirm_bool,
        auto_declare=auto_declare_bool,
        ref_image_paths=ref_paths,
        ref_roles=roles,
        import_video_path=video_path,
    )
    return {"ok": True, "task_id": task.id, "ref_image_count": len(ref_paths), "auto_confirm": auto_confirm_bool, "auto_declare": auto_declare_bool, "model": model}


@app.post("/api/rewrite")
async def api_rewrite(prompt: str = Form(...)):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(400, "提示词不能为空")
    acc = next(
        (a for a in pool.snapshot() if a.get("logged_in") and a.get("available")),
        None,
    )
    if not acc:
        raise HTTPException(400, "没有可用且已登录的豆包账号，请先扫码登录")
    try:
        rewritten = await rewrite_prompt(session_file(acc["id"]), prompt)
    except Exception as exc:
        log_hub.warn(f"AI 改写失败: {exc}")
        raise HTTPException(500, f"AI 改写失败: {exc}") from exc
    log_hub.ok("AI 已按你的真实需求完成合规改写")
    return {"ok": True, "rewritten": rewritten}


@app.get("/api/tasks")
async def api_tasks():
    return {"tasks": runner.public_tasks()}


# ---------------------------------------------------------------------------
# 实时日志（SSE）
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def api_events():
    async def stream():
        # 先补发历史，再持续推送
        for entry in log_hub.history():
            yield sse_format(entry)
        async for entry in log_hub.subscribe():
            yield sse_format(entry)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------

@app.get("/api/videos")
async def api_videos():
    items = []
    for f in sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        items.append({
            "name": f.name,
            "size_mb": round(f.stat().st_size / 1048576, 1),
            "url": f"/output/{f.name}",
        })
    return {"videos": items}


app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/app/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main()
