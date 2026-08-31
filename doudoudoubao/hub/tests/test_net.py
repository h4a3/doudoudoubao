# -*- coding: utf-8 -*-
"""网络错误识别与自动重试的自测。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hub.net import is_network_error, run_with_retry  # noqa: E402


def test_classify():
    assert is_network_error("Cannot connect to host www.doubao.com:443 ssl:default [getaddrinfo failed]")
    assert is_network_error("Connection reset by peer")
    assert not is_network_error("豆包返回成功但没有视频数据")
    assert not is_network_error("服务过载，请稍后重试")
    print("[1] 网络错误分类 OK")


def test_retry_network():
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("Cannot connect to host www.doubao.com:443 [getaddrinfo failed]")
        return "ok"

    result = asyncio.run(run_with_retry(flaky, backoff=(0, 0, 0)))
    assert result == "ok" and len(calls) == 3, (result, calls)
    print("[2] 网络错误自动重试 OK")


def test_no_retry_business():
    calls = []

    async def business():
        calls.append(1)
        raise RuntimeError("额度耗尽")

    try:
        asyncio.run(run_with_retry(business, backoff=(0, 0, 0)))
        raise AssertionError("should raise")
    except RuntimeError as exc:
        assert "额度" in str(exc)
    assert len(calls) == 1, calls
    print("[3] 业务错误不重试 OK")


if __name__ == "__main__":
    test_classify()
    test_retry_network()
    test_no_retry_business()
    print("net 自测全部通过")
