# -*- coding: utf-8 -*-
"""一次性诊断脚本：已删除收藏夹为何未被归档。

步骤：拉取当前账号收藏夹列表 → 与 config.json 对比得差集（已删除候选）
→ 逐个请求差集夹子的 items API，打印状态码与响应摘要（不打印 Cookie）。
用法：.venv/Scripts/python.exe scripts/diagnose_deleted_collections.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from paths_config import load_config, load_cookies
from zhihu_client import ZhihuClient

client = ZhihuClient(cookies=load_cookies())

# 1) 当前账号的收藏夹列表
me = client.get_api("https://www.zhihu.com/api/v4/me")
url_token = me.get("url_token")
print(f"url_token: {url_token}")

live_ids = {}
offset = 0
while True:
    payload = client.get_api(
        f"https://www.zhihu.com/api/v4/people/{url_token}/collections"
        f"?limit=20&offset={offset}"
    )
    items = payload.get("data", [])
    if not items:
        break
    for item in items:
        live_ids[str(item.get("id"))] = item.get("title")
    offset += 20
print(f"账号现存收藏夹: {len(live_ids)} 个")

# 2) 与 config.json 对比
config = load_config()
configured = {e["url"].rstrip("/").split("/")[-1]: e["name"] for e in config["zhihuUrls"]}
deleted = [cid for cid in configured if cid not in live_ids]
print(f"config 配置: {len(configured)} 个；其中已不在账号列表: {len(deleted)} 个\n")

# 3) 逐个请求已删除夹子的 items API，看真实响应
for cid in deleted:
    name = configured[cid]
    url = f"https://www.zhihu.com/api/v4/collections/{cid}/items?offset=0&limit=1"
    try:
        payload = client.get_page_data(url)
        paging = payload.get("paging", {})
        data = payload.get("data", [])
        print(f"[{name}] id={cid} -> HTTP 200, totals={paging.get('totals')}, "
              f"本页条数={len(data)}, is_end={paging.get('is_end')}")
        if data:
            first = data[0].get("content", {})
            print(f"    首条类型={first.get('type')}, 标题片段={str(first.get('title') or '')[:30]}")
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"[{name}] id={cid} -> HTTP {status}: {str(exc)[:120]}")
