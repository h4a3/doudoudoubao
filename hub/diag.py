# -*- coding: utf-8 -*-
"""豆包视频生成失败的本地启发式诊断提示。

知识来源：用户提供的《豆包生成视频卡审核 / 生成失败常见场景》手册。
这里的提示只用于把「为什么失败、下一步怎么查」讲清楚，
**不自动拦截、不替代豆包官方审核结论**。
"""
from __future__ import annotations

import re
from typing import List

from .net import is_network_error

# 内容安全高频拦截词（命中只是提示"疑似"，不是定罪）
RISK_KEYWORDS = [
    "政治", "军政", "敏感历史", "战争", "暴乱", "血腥", "恐怖", "惊悚", "自残", "自杀",
    "打架", "斗殴", "赌博", "毒品", "违禁", "造假", "诈骗", "绑架", "囚禁", "跳楼",
    "刀具", "枪支", "爆炸", "死亡", "灵异", "鬼", "色情", "低俗", "暴露", "擦边",
    "暧昧", "危险动作", "危险行为",
]

# 常见版权 IP 角色名（只列高频，其他 IP 同理）
IP_NAMES = [
    "哪吒", "孙悟空", "奥特曼", "路飞", "鸣人", "佐助", "皮卡丘", "初音", "米老鼠",
    "唐老鸭", "迪士尼", "漫威", "复仇者", "钢铁侠", "蜘蛛侠", "蝙蝠侠", "超人",
    "哈利波特", "火影", "海贼王", "龙珠", "柯南", "蜡笔小新", "哆啦A梦", "哆啦a梦",
]

# 真人肖像相关提示词
REAL_PERSON_HINTS = ["明星", "网红", "公众人物", "名人", "演员", "歌手", "真人照片", "写真"]


def _weird_chars(prompt: str) -> bool:
    """是否包含疑似乱码/emoji/特殊控制符号（可能让审核解析异常）。"""
    if "\ufffd" in prompt:
        return True
    if re.search(r"[\u2600-\u27BF\uD800-\uDBFF\uDC00-\uDFFF]", prompt):
        return True
    if re.search(r"[<>{}[\]\\^~|`]", prompt):
        return True
    return False


def _ratio_conflict(prompt: str) -> bool:
    """提示词里同时出现多种画幅/时长描述，可能造成参数冲突。"""
    ratios = [r for r in ("16:9", "9:16", "4:3", "3:4", "1:1", "21:9") if r in prompt]
    return len(set(ratios)) >= 2


def diagnose_no_video(task) -> List[str]:
    """接口返回成功但没有视频：典型是模型/审核拒绝，而不是程序坏了。"""
    lines: List[str] = []
    prompt = getattr(task, "prompt", "") or ""

    hit_risk = [w for w in RISK_KEYWORDS if w in prompt]
    if hit_risk:
        lines.append(
            f"① 内容安全：提示词命中疑似敏感词 {hit_risk}，豆包可能直接拦截；"
            "删掉相关描写（暴力/惊悚/危险动作/低俗等）后重试。"
        )

    if len(prompt) > 160:
        lines.append(
            "② 长提示词：豆包会把长文本拆成多段扫描，只要一小段踩线整个任务就失败；"
            "建议拆成 1-3 句的短提示词，逐段测试定位问题。"
        )

    hit_ip = [w for w in IP_NAMES if w.lower() in prompt.lower()]
    if hit_ip:
        lines.append(
            f"③ 版权 IP：出现 {hit_ip} 等角色名，容易触发版权拦截；"
            "改成泛化的外貌/服装/画风描述，不要写角色名。"
        )

    hit_real = [w for w in REAL_PERSON_HINTS if w in prompt]
    if hit_real:
        lines.append(
            "④ 肖像：提示词疑似指向真人/公众人物，人脸比对会拦截；"
            "改用手绘/卡通/虚拟形象描述。"
        )

    if getattr(task, "ref_image_paths", None):
        lines.append(
            "⑤ 参考图：带图任务会做人脸/版权/水印校验；真人照片建议先转成绘画或卡通风格，"
            "并检查画面角落是否有路人人脸、文字、logo、水印。可先去掉参考图做纯文生测试。"
        )

    if getattr(task, "import_video_path", None):
        lines.append(
            "⑥ 导入视频：抽出的帧里如果出现真人脸、水印 logo、敏感画面也会被拦；"
            "换一段干净素材，或先只传参考图测试。"
        )

    if _weird_chars(prompt):
        lines.append(
            "⑦ 特殊符号：提示词里有乱码/emoji/特殊符号，偶发导致审核解析异常；"
            "删掉特殊符号，统一换成中文标点。"
        )

    if _ratio_conflict(prompt):
        lines.append("⑧ 参数冲突：提示词里写了多种画幅/矛盾运镜，可能造成生成异常；只保留一种。")

    lines.append(
        "通用排查顺序：长脚本拆短 → 去掉真人名/IP 名 → 先不传参考图 → 换中文标点；"
        "失败后间隔几分钟再试，避免短时间高频提交触发操作风控。"
    )
    lines.append("补充判断：若账号额度没有扣减，基本可判定为审核/模型拒绝，而不是技术故障。")
    return lines


def diagnose_exception(message: str, task=None) -> List[str]:
    """根据豆包返回的错误文本做分类提示。"""
    lines: List[str] = []
    msg = str(message or "")
    m = msg.lower()

    if is_network_error(msg):
        lines.append(
            "① 本地网络/DNS/连接问题（与提示词、参考图、账号都无关）："
            "DNS 解析失败、连不上 doubao.com 或连接被重置。"
        )
        lines.append(
            "② 程序已自动重试 3 次仍失败；请检查网线/Wi-Fi、DNS 服务器、代理/科学上网开关，"
            "网络恢复后直接重新提交即可。"
        )
        lines.append("③ 本次失败不会累计账号风控失败次数，无需担心账号被隔离。")
        return lines

    if "710022004" in m or "captcha" in m or "验证码" in msg or "安全验证" in msg:
        lines.append(
            "① 操作风控/验证码：账号已自动隔离冷却，请停止重试；"
            "等冷却结束或重新扫码登录后再试。"
        )
        lines.append("② 不要短时间高频重复提交，失败后间隔几分钟更安全。")
    elif "服务过载" in msg or "overload" in m or "稍后重试" in msg:
        lines.append("① 服务端过载/排队：高峰期生成慢或直接失败，不是内容问题；等 2-5 分钟再试，或换一个账号。")
    elif "timeout" in m or "超时" in msg:
        lines.append(
            "① 生成超时：可能是高峰期排队，或画面描述过于复杂（人物多、运镜多）；"
            "简化描述、减少镜头数量后重试。"
        )
    elif "session" in m or "登录" in msg or "过期" in msg:
        lines.append("① 会话失效：请删除该账号后重新扫码登录。")
    elif "额度" in msg or "quota" in m or "limit" in m or "credit" in m:
        lines.append(
            "① 额度耗尽：当日免费视频额度用完，等次日重置或换账号；"
            "注意 5 秒视频耗 1 点、10 秒视频耗 2 点。"
        )
    elif "侵权" in msg or "违规" in msg or "无法返回" in msg or "审核" in msg:
        lines.append("① 豆包合规审核拒绝（通常额度未扣）：提示词或参考图命中侵权/违规规则。")
        lines.append("② 处理顺序：删掉敏感/擦边描写 → 去掉或替换参考图 → 长文本拆短逐句测试 → 换主题或换表达。")
    elif "risk" in m or "风控" in msg:
        lines.append("① 风控拦截：减少提交频率，间隔几分钟再试；若持续出现，重新扫码登录后再试。")
    else:
        lines.append("① 无法从错误文本自动归类，常见原因：内容审核拦截、参考图/导入视频不通过、接口改版或网络波动。")
        lines.append("② 建议按「拆短提示词 → 去掉参考图 → 换账号 → 稍后重试」的顺序排查。")

    return lines


def emit_diagnosis(log_hub, lines: List[str], task_id: str = "", account_id: str = "") -> None:
    """把诊断建议逐条写进实时日志（warn 级别，前端黄色高亮）。"""
    for line in lines:
        log_hub.warn(f"[诊断] {line}", task_id=task_id, account_id=account_id)
