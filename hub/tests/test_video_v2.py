# -*- coding: utf-8 -*-
"""新版视频协议的自测（不依赖网络）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hub.video_v2 import (  # noqa: E402
    build_v2_payload,
    find_confirm_request,
    find_rejection_text,
    parse_ack,
    parse_creation_result,
)


def test_payload():
    payload = build_v2_payload("小狗跳舞", "9:16", 7, "seedance_v2.0", "verify_fp")
    ability = payload["chat_ability"]
    assert ability["ability_type"] == 17, ability
    ability_param = json.loads(ability["ability_param"])
    assert ability_param == {"ratio": "9:16", "model": "seedance_v2.0", "duration": 7}, ability_param
    assert payload["messages"][-1]["content_block"][0]["block_type"] == 10000
    print("[1] 新版 payload OK")


def test_parse_ack_text_sse():
    raw = 'event: SSE_ACK\ndata: {"ack_client_meta":{"conversation_id":"abc123"}}\n\n'
    assert parse_ack(raw)["conversation_id"] == "abc123"
    print("[2] SSE_ACK 解析 OK")


def test_parse_ack_json_sse():
    event_data = json.dumps({"conversation_id": "conv456"})
    raw = "data: " + json.dumps({"event_type": 2002, "event_data": event_data})
    assert parse_ack(raw)["conversation_id"] == "conv456"
    print("[3] JSON ACK 兼容解析 OK")


def test_parse_creation():
    block = {
        "content": {
            "creation_block": {
                "creations": [{
                    "id": "123",
                    "video": {
                        "status": 3,
                        "vid": "v0269abc",
                        "download_url": "https://example.com/v.mp4",
                        "cover": {"image_thumb": {"url": "https://example.com/c.jpg"}},
                    },
                }],
            }
        }
    }
    message = {"content": json.dumps([block], ensure_ascii=False)}
    data = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [message]}}}
    result = parse_creation_result(data)
    assert result and result["vid"] == "v0269abc" and result["video_url"].endswith("v.mp4"), result
    print("[4] creation 视频解析 OK")


def test_parse_creation_content_block():
    # 新链接口把块放在 content_block 字段，而不是 content JSON 字符串
    block = {
        "block_type": 10000,
        "content": {
            "creation_block": {
                "creations": [{
                    "id": "c1",
                    "video": {
                        "status": 3,
                        "vid": "vNew",
                        "download_url": "https://example.com/new.mp4",
                    },
                }],
            }
        },
    }
    message = {"content": "", "content_block": [block]}
    data = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [message]}}}
    result = parse_creation_result(data)
    assert result and result["vid"] == "vNew", result
    print("[5] content_block 新版视频解析 OK")


def test_find_confirm_button():
    block = {
        "block_type": 10103,
        "content": {
            "button_block": {
                "display_text": "确认生成",
                "command": 3,
                "scene": "creation_portrait_video_auth_confirm",
                "extra": {"creation_btn_rely_info": "{}"},
            }
        },
    }
    message = {"message_id": "m1", "content": "", "content_block": [block]}
    data = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [message]}}}
    confirm = find_confirm_request(data)
    assert confirm and confirm["display_text"] == "确认生成", confirm
    assert "auth_confirm" in confirm["scene"]
    print("[6] 确认生成按钮识别 OK")


def test_find_rejection():
    block = {"block_type": 10000, "content": {"text_block": {"text": "生成内容中疑似包含侵权/违规内容，无法返回该内容，换个主题再试试，生成额度未扣除。"}}}
    message = {"content": "", "content_block": [block]}
    data = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [message]}}}
    text = find_rejection_text(data)
    assert text and "侵权" in text and "额度未扣除" in text, text

    # 授权声明不是拒绝，必须跳过
    disclaimer = {"block_type": 10000, "content": {"text_block": {"text": "你在本功能中上传、使用的素材，均已获充分授权，无侵权违法风险。违反前述承诺或存在不当使用本功能等违反《豆包使用规范》的行为，相关责任需由你自行承担。"}}}
    data2 = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [{"content": "", "content_block": [disclaimer]}]}}}
    assert find_rejection_text(data2) is None
    print("[7] 豆包审核拒绝原文识别（含授权声明排除）OK")


if __name__ == "__main__":
    test_payload()
    test_parse_ack_text_sse()
    test_parse_ack_json_sse()
    test_parse_creation()
    test_parse_creation_content_block()
    test_find_confirm_button()
    test_find_rejection()
    print("video_v2 自测全部通过")
