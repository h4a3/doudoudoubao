# -*- coding: utf-8 -*-
"""诊断：走 aispace 链路找指定 vid 的无水印下载地址。"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "doubao2api"))

from doubao2api.client import DoubaoChatClient  # noqa: E402
from hub.video_v2 import _v2_query_params  # noqa: E402


async def post_json(client, endpoint: str, body: dict) -> dict:
    params = _v2_query_params(client)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{client.BASE_URL}{endpoint}?{query}"
    async with client.session.post(
        url,
        data=json.dumps(body, ensure_ascii=False),
        headers={"Content-Type": "application/json"},
        timeout=30,
    ) as resp:
        print(endpoint, "HTTP", resp.status)
        text = await resp.text()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print("非JSON响应:", text[:500])
            return {}


async def main(vid: str) -> None:
    session_file = PROJECT / "accounts" / "acc01" / ".doubao_session.json"
    client = DoubaoChatClient.from_session(session_file=str(session_file), timeout_seconds=120)
    async with client:
        home = await post_json(client, "/samantha/aispace/homepage", {})
        out = PROJECT / "logs" / f"aispace_homepage_{vid}.json"
        out.write_text(json.dumps(home, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", out)

        folder_id = ""
        for child in home.get("data", {}).get("children", []):
            print("child:", child.get("name"), child.get("id"), "type", child.get("type"))
            if child.get("name") == "我的创作":
                folder_id = str(child.get("id", ""))
        print("folder_id:", folder_id)
        if not folder_id:
            print("未找到我的创作目录")
            return

        nodes = await post_json(client, "/samantha/aispace/node_info", {
            "node_id": folder_id,
            "need_full_path": True,
            "size": 50,
            "sort_param": {"need_sort_config": True, "sort_order": 1, "sort_type": 0},
        })
        out = PROJECT / "logs" / f"aispace_node_{vid}.json"
        out.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", out)

        node_id = ""
        for child in nodes.get("data", {}).get("children", []):
            print("node:", child.get("key"), child.get("id"), child.get("name"))
            if str(child.get("key", "")) == vid:
                node_id = str(child.get("id", ""))
        print("node_id:", node_id)
        if not node_id:
            print("未找到该 vid 节点")
            return

        info = await post_json(client, "/samantha/aispace/get_download_info", {
            "requests": [{"node_id": node_id}],
        })
        out = PROJECT / "logs" / f"aispace_download_{vid}.json"
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", out)
        infos = info.get("data", {}).get("download_infos") or []
        for i, item in enumerate(infos):
            for key in ("main_url", "backup_url", "url"):
                val = item.get(key)
                if val:
                    print(f"download_infos[{i}].{key} =", val[:400])


if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else "v0269cg10004da9qku27dld43grtvu70"
    asyncio.run(main(vid))
