# -*- coding:utf-8 -*-
"""知乎源数据抓取层：收藏夹分页、回答/专栏元数据与内容快照候选。

接缝说明：sources 把"URL → CollectionFetchResult / SourceSnapshot"的
全部知乎 API 知识（分页对账、API/网页双候选、网页候选熔断）收在
本模块；传输经 ZhihuClient（可注入假 transport），调用方无需感知
头部、Cookie 或限流策略。
"""
import json
import logging
import re
import time
from dataclasses import replace

import requests
from bs4 import BeautifulSoup

from integrity import (
    CollectionFetchResult,
    SourceMetadata,
    canonicalize_url,
    choose_best_snapshot,
    parse_source_identity,
    snapshot_from_html,
)
from zhihu_client import ZhihuClient, default_client


def _client(request_get=None, sleep=None):
    """按调用点的注入构造客户端；默认用共享客户端（含已加载 Cookie）。"""
    if request_get is None and sleep is None:
        return default_client()
    return ZhihuClient(
        transport=request_get or requests.get,
        cookies=default_client().cookies,
        sleep=sleep or time.sleep,
    )


# 获取收藏夹的回答总数
def get_article_nums_of_collection(
    collection_id,
    request_get=None,
    sleep=None,
    attempts=3,
    timeout=30,
):
    """Return the API-reported raw item total, or None after retries fail."""
    request_get = request_get or requests.get
    sleep = sleep or time.sleep
    collection_url = f"https://www.zhihu.com/api/v4/collections/{collection_id}/items"
    last_error = None
    for attempt in range(attempts):
        try:
            payload = _client(request_get, sleep).get_page_data(collection_url, timeout=timeout)
            total = payload["paging"]["totals"]
            logging.info(f"收藏夹 {collection_id} 包含 {total} 个项目")
            return int(total)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleep(2 ** attempt)
    logging.error(f"获取收藏夹 {collection_id} 总数失败: {last_error}")
    return None


# 解析出每个回答的具体链接
def fetch_collection_items(
    collection_id,
    request_get=None,
    get_total=None,
    sleep=None,
    attempts=3,
    timeout=30,
):
    """Fetch and reconcile every raw collection item."""
    collection_id = str(collection_id).replace('\n', '')
    request_get = request_get or requests.get
    get_total = get_total or get_article_nums_of_collection
    sleep = sleep or time.sleep
    logging.info(f"开始获取收藏夹 {collection_id} 的完整项目列表")

    limit = 20
    offset = 0
    visited_offsets = set()
    # 总数优先从第一页分页响应的 paging.totals 读取（省一次独立请求），
    # 响应缺失该字段时才回退到 get_total 请求。
    result = None
    while True:
        if result is not None and offset >= result.expected_total:
            break
        if offset in visited_offsets:
            result.page_failures.append({"offset": offset, "error": "paging_offset_loop"})
            break
        visited_offsets.add(offset)
        collection_url = (
            f"https://www.zhihu.com/api/v4/collections/{collection_id}/items"
            f"?offset={offset}&limit={limit}"
        )
        payload = None
        last_error = None
        for attempt in range(attempts):
            try:
                logging.info(
                    f"请求收藏夹API: offset={offset}, limit={limit}, attempt={attempt + 1}"
                )
                payload = _client(request_get, sleep).get_page_data(collection_url, timeout=timeout)
                if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                    raise ValueError("收藏夹API响应缺少data数组")
                break
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    sleep(2 ** attempt)
        if payload is None:
            if result is None:
                result = CollectionFetchResult(collection_id, 0)
            result.page_failures.append({"offset": offset, "error": str(last_error)})
            break

        page_items = payload['data']
        logging.info(f"成功获取 {len(page_items)} 个原始项目")

        if result is None:
            paging_info = payload.get("paging")
            totals = (
                paging_info.get("totals")
                if isinstance(paging_info, dict)
                else None
            )
            if totals is None:
                try:
                    totals = get_total(collection_id)
                except Exception as exc:
                    result = CollectionFetchResult(collection_id, 0)
                    result.page_failures.append({"offset": 0, "error": str(exc)})
                    result.reconcile()
                    return result
            if totals is None:
                result = CollectionFetchResult(collection_id, 0)
                result.page_failures.append({"offset": 0, "error": "collection_total_unavailable"})
                result.reconcile()
                return result
            logging.info(f"收藏夹 {collection_id} 包含 {totals} 个项目")
            result = CollectionFetchResult(collection_id, int(totals))
            if result.expected_total == 0:
                result.reconcile()
                return result

        for raw_item in page_items:
            result.add_raw_item(raw_item)

        paging = payload.get("paging")
        if isinstance(paging, dict):
            if paging.get("is_end") is True:
                result.reached_end = True
                break
            next_url = str(paging.get("next") or "")
            next_match = re.search(r"[?&]offset=(\d+)", next_url)
            if next_match:
                next_offset = int(next_match.group(1))
                if next_offset <= offset:
                    result.page_failures.append(
                        {"offset": offset, "error": f"invalid_next_offset:{next_offset}"}
                    )
                    break
                offset = next_offset
                continue

        if len(page_items) < limit:
            break
        offset += limit

    result.reconcile()
    if result.complete:
        if result.total_mismatch:
            logging.warning(
                f"收藏夹 {collection_id} 已到API末页，但报告总数与可见项目不一致: "
                f"expected={result.expected_total}, raw={result.raw_item_count}"
            )
        logging.info(
            f"收藏夹 {collection_id} 完整获取: raw={result.raw_item_count}, "
            f"exportable={len(result.exportable_items)}, "
            f"unsupported={len(result.unsupported_items)}"
        )
    else:
        logging.error(
            f"收藏夹 {collection_id} 获取不完整: expected={result.expected_total}, "
            f"raw={result.raw_item_count}, failures={result.page_failures}"
        )
    return result


def get_article_urls_in_collection(collection_id):
    """Compatibility wrapper returning URL/title lists for existing MCP tools."""
    result = fetch_collection_items(collection_id)
    if not result.complete:
        return [], []
    return (
        [item.url for item in result.exportable_items],
        [item.title for item in result.exportable_items],
    )


def _blocked_or_login_page(html_text):
    preview = BeautifulSoup(html_text or "", "lxml").get_text(" ", strip=True).lower()
    markers = ("请登录后继续", "登录 - 知乎", "安全验证", "请求存在异常")
    return any(marker.lower() in preview for marker in markers)


def _initial_entity(page_html, entity_name, source_id):
    soup = BeautifulSoup(page_html or "", "lxml")
    script = soup.select_one("script#js-initialData")
    if not script:
        return None
    raw = script.string or script.get_text()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    state = payload.get("initialState", payload)
    entities = state.get("entities", {}) if isinstance(state, dict) else {}
    collection = entities.get(entity_name, {}) if isinstance(entities, dict) else {}
    if not isinstance(collection, dict):
        return None
    entity = collection.get(str(source_id))
    return entity if isinstance(entity, dict) else None


def _metadata_with_entity_time(metadata, entity):
    if not entity:
        return metadata
    updated = entity.get("updated_time")
    if updated is None:
        updated = entity.get("updatedTime")
    if updated is None:
        updated = entity.get("updated")
    return replace(metadata, updated_time=updated if updated is not None else metadata.updated_time)


def parse_answer_page_candidates(metadata, page_html):
    if _blocked_or_login_page(page_html):
        return []
    soup = BeautifulSoup(page_html or "", "lxml")
    candidates = []
    seen_html = set()
    for element in soup.select("div.RichContent-inner"):
        html_value = str(element)
        if html_value not in seen_html:
            candidates.append(snapshot_from_html(metadata, "answer_page", html_value))
            seen_html.add(html_value)
    entity = _initial_entity(page_html, "answers", metadata.source_id)
    if entity and isinstance(entity.get("content"), str) and entity["content"]:
        entity_metadata = _metadata_with_entity_time(metadata, entity)
        html_value = entity["content"]
        if html_value not in seen_html:
            candidates.append(
                snapshot_from_html(entity_metadata, "answer_initial_data", html_value)
            )
    return candidates


def parse_article_page_candidates(metadata, page_html):
    if _blocked_or_login_page(page_html):
        return []
    soup = BeautifulSoup(page_html or "", "lxml")
    candidates = []
    seen_html = set()
    selectors = (
        "div.Post-RichText",
        "div.Post-RichTextContainer > div.RichText",
        "div.Article-RichText",
        "[data-zop-editor]",
    )
    for selector in selectors:
        for element in soup.select(selector):
            html_value = str(element)
            if html_value not in seen_html:
                candidates.append(snapshot_from_html(metadata, "article_page", html_value))
                seen_html.add(html_value)
    entity = _initial_entity(page_html, "articles", metadata.source_id)
    if entity and isinstance(entity.get("content"), str) and entity["content"]:
        entity_metadata = _metadata_with_entity_time(metadata, entity)
        html_value = entity["content"]
        if html_value not in seen_html:
            candidates.append(
                snapshot_from_html(entity_metadata, "article_initial_data", html_value)
            )
    return candidates


def fetch_answer_metadata(answer_url, request_get=None):
    request_get = request_get or requests.get
    canonical_url = canonicalize_url(answer_url)
    source_type, answer_id = parse_source_identity(canonical_url)
    payload = _client(request_get).get_api(
        f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=updated_time"
    )
    return SourceMetadata(
        canonical_url=canonical_url,
        source_type=source_type,
        source_id=answer_id,
        updated_time=payload.get("updated_time"),
    )


# 知乎网页候选抓取受反爬限流影响，失败率会随请求量累积（典型的 403 限流）。
# 默认懒升级：仅当 API 候选缺失/失败时才抓取网页候选，避免常态下被限流拖慢。
# 连续失败达到上限后熔断：本次运行剩余项目跳过网页候选抓取，仅依赖 API 候选校验。
PAGE_CANDIDATES_ENABLED = True
PAGE_CANDIDATES_EAGER = False
PAGE_CANDIDATE_FAILURE_LIMIT = 5
_page_candidate_failures = 0


def _reset_page_candidate_state(enabled=True, failure_limit=5, eager=False):
    """重置网页候选抓取状态（测试及 MCP 入口可复用）。"""
    global PAGE_CANDIDATES_ENABLED, PAGE_CANDIDATES_EAGER
    global PAGE_CANDIDATE_FAILURE_LIMIT, _page_candidate_failures
    PAGE_CANDIDATES_ENABLED = enabled
    PAGE_CANDIDATES_EAGER = eager
    PAGE_CANDIDATE_FAILURE_LIMIT = failure_limit
    _page_candidate_failures = 0


def _page_candidates_available():
    return PAGE_CANDIDATES_ENABLED and _page_candidate_failures < PAGE_CANDIDATE_FAILURE_LIMIT


def _record_page_candidate_success():
    global _page_candidate_failures
    _page_candidate_failures = 0


def _record_page_candidate_failure():
    global _page_candidate_failures
    _page_candidate_failures += 1
    if _page_candidate_failures == PAGE_CANDIDATE_FAILURE_LIMIT:
        logging.warning(
            f"网页候选连续失败 {PAGE_CANDIDATE_FAILURE_LIMIT} 次（疑似限流累积），"
            "本次运行剩余项目将跳过网页候选抓取"
        )


def fetch_answer_snapshots(answer_url, request_get=None):
    request_get = request_get or requests.get
    canonical_url = canonicalize_url(answer_url)
    source_type, answer_id = parse_source_identity(canonical_url)
    try:
        metadata = fetch_answer_metadata(canonical_url, request_get=request_get)
    except Exception:
        metadata = SourceMetadata(canonical_url, source_type, answer_id, None)
    candidates = []
    api_candidate_ok = False
    try:
        payload = _client(request_get).get_api(
            f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content,updated_time"
        )
        if isinstance(payload.get("content"), str) and payload["content"]:
            api_metadata = replace(
                metadata,
                updated_time=payload.get("updated_time", metadata.updated_time),
            )
            candidates.append(
                snapshot_from_html(api_metadata, "answer_api", payload["content"])
            )
            api_candidate_ok = True
    except Exception as exc:
        logging.warning(f"回答API候选获取失败: {canonical_url}: {exc}")
    # 懒升级：API 候选成功时不抓网页候选（常态下省一次易被限流的请求），
    # 仅当 API 候选缺失/失败，或显式开启急切模式（--page-candidates）时才尝试网页。
    if _page_candidates_available() and (not api_candidate_ok or PAGE_CANDIDATES_EAGER):
        try:
            page_html = _client(request_get).get_page(canonical_url)
            candidates.extend(parse_answer_page_candidates(metadata, page_html))
            _record_page_candidate_success()
        except Exception as exc:
            logging.warning(f"回答网页候选获取失败: {canonical_url}: {exc}")
            _record_page_candidate_failure()
    return candidates


def _article_api_url(article_id):
    return f"https://zhuanlan.zhihu.com/api/articles/{article_id}"


def _article_metadata_from_payload(canonical_url, source_type, article_id, payload):
    updated_time = payload.get("updated_time")
    if updated_time is None:
        updated_time = payload.get("updated")
    return SourceMetadata(
        canonical_url=canonical_url,
        source_type=source_type,
        source_id=article_id,
        updated_time=updated_time,
    )


def fetch_article_metadata(article_url, request_get=None):
    request_get = request_get or requests.get
    canonical_url = canonicalize_url(article_url)
    source_type, article_id = parse_source_identity(canonical_url)
    try:
        payload = _client(request_get).get_api(_article_api_url(article_id))
        return _article_metadata_from_payload(
            canonical_url,
            source_type,
            article_id,
            payload,
        )
    except Exception as exc:
        logging.warning(f"专栏元数据API获取失败，继续抓取正文: {canonical_url}: {exc}")
        return SourceMetadata(canonical_url, source_type, article_id, None)


def fetch_article_snapshots(article_url, request_get=None):
    request_get = request_get or requests.get
    canonical_url = canonicalize_url(article_url)
    source_type, article_id = parse_source_identity(canonical_url)
    metadata = SourceMetadata(canonical_url, source_type, article_id, None)

    try:
        payload = _client(request_get).get_api(_article_api_url(article_id))
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            api_metadata = _article_metadata_from_payload(
                canonical_url,
                source_type,
                article_id,
                payload,
            )
            return [snapshot_from_html(api_metadata, "article_api", content)]
        logging.warning(f"专栏API未返回正文，尝试网页候选: {canonical_url}")
    except Exception as exc:
        logging.warning(f"专栏API候选获取失败，尝试网页候选: {canonical_url}: {exc}")

    page_html = _client(request_get).get_page(canonical_url)
    return parse_article_page_candidates(metadata, page_html)


def fetch_source_snapshot(url, request_get=None):
    source_type, _ = parse_source_identity(url)
    candidates = (
        fetch_answer_snapshots(url, request_get=request_get)
        if source_type == "answer"
        else fetch_article_snapshots(url, request_get=request_get)
    )
