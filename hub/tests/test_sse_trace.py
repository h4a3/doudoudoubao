# -*- coding: utf-8 -*-
"""SSE 诊断解析的小型自测（不依赖网络）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hub.runner import _extract_video_sse_texts, _trace_sse  # noqa: E402


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def test_extract_text():
    payload = {
        "event_type": 2001,
        "event_data": json.dumps({
            "message": {
                "content_type": 2001,
                "content": json.dumps({"text": "服务过载，请稍后重试"}, ensure_ascii=False),
            }
        }),
    }
    texts = _extract_video_sse_texts(_sse(payload))
    assert texts == ["服务过载，请稍后重试"], texts
    print("[1] 提取拒绝文本 OK")


def test_trace_2021():
    out = []

    def cb(level, text):
        out.append((level, text))

    payload = {
        "event_type": 2001,
        "event_data": json.dumps({
            "is_finish": True,
            "message": {
                "content_type": 2021,
                "content": json.dumps({"data": [{"status": 3}]}, ensure_ascii=False),
            },
        }),
    }
    _trace_sse(_sse(payload), cb, "测试")
    joined = "\n".join(t for _, t in out)
    assert "content_type=2021" in joined, joined
    assert "status=3" in joined, joined
    print("[2] 2021 事件解读 OK")


if __name__ == "__main__":
    test_extract_text()
    test_trace_2021()
    print("SSE 诊断自测全部通过")
