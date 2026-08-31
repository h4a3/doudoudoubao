# 豆包生视频工作流（hub）

本地多账号豆包视频生成工作台：调用豆包网页端免费 Seedance 视频能力，自动处理确认、合规声明、无水印下载与实时日志。

> ⚠️ 本项目仅用于个人学习与自用研究，请遵守豆包平台条款与相关法律法规。逆向接口随时可能失效。

## 功能

- 多豆包账号：扫码登录、独立会话、额度/节流/风控隔离
- 本地网页端：`http://127.0.0.1:8000`
- 视频生成：Seedance 2.0 Fast（默认，可切换标准/Mini）、4-10 秒、多参考图 + @角色
- 自动确认：按钮式/文字式「确认生成」自动处理，保持 Fast 模型
- 自动声明：侵权/违规/真人脸被拒时自动声明素材授权并重试/重排队
- 无水印下载：通过 aispace「我的创作」获取原流，仅无水印视频进入生成结果
- 实时日志：网络预检、上传、提交、轮询、卡点诊断
- 自动回收：定期扫描豆包最近会话，回收已生成但漏接的视频

## 快速开始

```bash
git clone <your-repo-url>
cd doubao-video-workflow

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
pip install -r doubao2api/requirements.txt
playwright install chromium
```

启动：

```bash
# Windows
start-hub.bat
# 或
.venv\Scripts\python -m hub.server
```

打开 `http://127.0.0.1:8000`，在「账号管理」扫码登录豆包账号即可使用。

## 目录

```
hub/                  # 主程序（FastAPI + 任务队列 + 诊断 + 自动回收）
doubao2api/           # 第三方豆包网页逆向客户端（MIT）
accounts/accounts.json # 账号配置模板（登录后生成独立会话文件，不会入库）
output/               # 生成的无水印视频（默认 .gitkeep）
```

## 安全说明

- 登录 Cookie 保存在 `accounts/<账号>/.doubao_session.json`，**不要提交到 GitHub**。
- 本项目 `.gitignore` 已排除会话、日志、临时文件和视频产物。
- 请勿将含个人敏感信息的参考图/提示词发布到公开仓库。

## License

本项目代码采用 MIT License，`doubao2api/` 目录保留其原有 LICENSE。
