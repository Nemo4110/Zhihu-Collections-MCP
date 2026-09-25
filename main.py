# -*- coding:utf-8 -*-
import argparse
import os
import random
import sys
import time
import threading
import requests
from bs4 import BeautifulSoup
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import replace
from utils import filter_title_str
from paths_config import get_current_os, load_config, load_cookies, parse_output_path
from zhihu_client import ZhihuClient
from render import prefetch_images, render_markdown
import json
import logging
import traceback
import platform
import pathlib

from integrity import (
    CollectionFetchResult,
    ExportMode,
    IntegrityAction,
    IntegrityManifestStore,
    ManifestSchemaError,
    SourceContentError,
    SourceMetadata,
    SourceSnapshot,
    RunReport,
    atomic_write_json,
    atomic_write_text,
    canonicalize_url,
    choose_best_snapshot,
    decide_action,
    local_record_is_intact,
    parse_source_identity,
    record_from_validation,
    snapshot_from_html,
    validate_markdown,
)


# 读取配置文件
def get_current_os():
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        return "unknown"

# 解析路径，根据操作系统类型处理
# 全局变量存储当前处理的收藏夹名称
current_collection_name = ""

# 全局日志数据存储
processing_log = []

# 全局配置和路径管理
config = {}
base_output_path = None

# 设置调试日志
def setup_debug_logging():
    # 初始化时使用默认路径，稍后会在main中重新配置
    logs_dir = os.path.join(os.path.dirname(__file__), 'downloads', 'logs')
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = os.path.join(logs_dir, f"debug_{timestamp}.log")
    
    # 清除所有已存在的处理器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 创建文件处理器，立即写入
    file_handler = logging.FileHandler(debug_log_file, encoding='utf-8', mode='w')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 设置格式
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 配置根日志记录器
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 测试日志写入
    logging.info(f"日志系统初始化完成，日志文件: {debug_log_file}")
    
    # 强制刷新
    for handler in root_logger.handlers:
        if hasattr(handler, 'flush'):
            handler.flush()
    
    return debug_log_file

# 重新配置日志路径
def reconfigure_logging():
    logs_dir = get_logs_path()
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = os.path.join(logs_dir, f"debug_{timestamp}.log")
    
    # 清除已有的handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.flush()  # 确保刷新
        root_logger.removeHandler(handler)
    
    # 创建新的处理器
    file_handler = logging.FileHandler(debug_log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 设置格式
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 重新添加处理器
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.DEBUG)
    
    return debug_log_file

# 获取输出路径的函数
def get_output_path(collection_name):
    """
    根据配置获取输出路径
    如果配置了outputPath，使用自定义路径
    否则使用默认的downloads路径
    """
    global base_output_path

    # 收藏夹名可能含路径分隔符等非法字符（如 "C/C++"），清理为安全的目录名
    safe_name = filter_title_str(collection_name).strip()
    if not safe_name:
        safe_name = "未命名收藏夹"

    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), safe_name)
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', safe_name)

def get_logs_path():
    """
    获取日志路径
    """
    global base_output_path
    
    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), 'logs')
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', 'logs')

def get_debug_path():
    """
    获取调试文件路径
    """
    global base_output_path
    
    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), 'debug')
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', 'debug')

debug_log_file = setup_debug_logging()

cookies = load_cookies()


def _client(request_get=None, sleep=None):
    """按调用点的注入构造客户端；默认用真实 requests 与全局 cookies。"""
    return ZhihuClient(
        transport=request_get or requests.get,
        cookies=cookies,
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
def is_article_already_downloaded(file_path, target_url):
    """
    检查文件是否已存在且包含相同的URL
    :param file_path: 要检查的markdown文件路径
    :param target_url: 目标URL
    :return: True如果文件存在且URL匹配，False否则
    """
    if not os.path.exists(file_path):
        return False
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            # 检查第一行是否为引用块且包含目标URL
            if first_line.startswith('> ') and target_url in first_line:
                return True
    except:
        pass
    
    return False


def get_unique_filename(base_dir, title, url):
    """
    获取唯一的文件名，如果标题重复则添加URL的ID部分
    :param base_dir: 基础目录
    :param title: 文章标题
    :param url: 文章URL
    :return: 唯一的文件路径
    """
    base_filename = filter_title_str(title)
    file_path = os.path.join(base_dir, base_filename + ".md")
    
    # 如果文件不存在，直接返回
    if not os.path.exists(file_path):
        return file_path
    
    # 如果文件存在且URL匹配，返回该路径（用于跳过）
    if is_article_already_downloaded(file_path, url):
        return file_path
    
    # 如果文件存在但URL不匹配，添加URL ID后缀
    url_id = url.split('/')[-1]
    unique_filename = f"{base_filename}_{url_id}"
    return os.path.join(base_dir, unique_filename + ".md")




def save_processing_log():
    """
    保存处理日志到logs目录
    """
    logs_dir = get_logs_path()
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{timestamp}.json"
    log_path = os.path.join(logs_dir, log_filename)
    
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(processing_log, f, ensure_ascii=False, indent=2)
    
    print(f"处理日志已保存到: {log_path}")



def _default_fetch_metadata(item):
    if item.source_type == "answer":
        return fetch_answer_metadata(item.url)
    if item.source_type == "article":
        return fetch_article_metadata(item.url)
    return SourceMetadata(item.url, item.source_type, item.source_id, None)


def _default_render_markdown_for(assets_dir):
    """构造默认渲染函数：正文前加原始 URL 引用行，资产写入 assets_dir。"""
    def render(snapshot, item):
        body = render_markdown(snapshot.html, assets_dir, heading_style="ATX")
        return f"> {item.url}\n{body}"
    return render


# 清单写入/保存互斥锁：项级并发导出时保护 manifest.records 与 save() 序列化
_manifest_lock = threading.Lock()
# 项级并发数：控制正文 API 与渲染的并发峰值，避免触发知乎限流
ITEM_EXPORT_WORKERS = 3


def export_item_with_integrity(
    item,
    collection_dir,
    manifest,
    mode=ExportMode.BALANCED,
    fetch_metadata_fn=None,
    fetch_snapshot_fn=None,
    render_markdown_fn=None,
):
    """Verify, adopt, repair, or refresh one collection item."""
    collection_path = pathlib.Path(collection_dir)
    collection_path.mkdir(parents=True, exist_ok=True)
    assets_dir = collection_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    fetch_metadata_fn = fetch_metadata_fn or _default_fetch_metadata
    fetch_snapshot_fn = fetch_snapshot_fn or fetch_source_snapshot
    render_markdown_fn = render_markdown_fn or _default_render_markdown_for(assets_dir)

    record = manifest.records.get(item.url)
    if record and record.get("markdown_path"):
        file_path = collection_path / record["markdown_path"]
    else:
        file_path = pathlib.Path(get_unique_filename(str(collection_path), item.title, item.url))

    metadata = None
    if item.updated_time is not None:
        # 收藏夹分页响应自带 updated_time（与回答/专栏 API 同源同值），直接复用，
        # 省去逐项元数据请求；时间戳未变化且本地完好时整项零网络请求。
        metadata = SourceMetadata(item.url, item.source_type, item.source_id, item.updated_time)
    else:
        try:
            metadata = fetch_metadata_fn(item)
        except Exception as exc:
            return {
                "name": item.title,
                "url": item.url,
                "status": "metadata_failed",
                "issues": [{"code": "metadata_failed", "message": str(exc)}],
            }

    local_intact = local_record_is_intact(record, file_path, assets_dir)
    action = decide_action(
        mode,
        file_exists=file_path.exists(),
        record=record,
        local_intact=local_intact,
        metadata=metadata,
    )
    if action == IntegrityAction.SKIP_VERIFIED:
        return {
            "name": item.title,
            "url": item.url,
            "status": "skipped_verified",
            "action": action.value,
            "markdown_path": file_path.name,
            "issues": [],
            "warnings": record.get("warnings", []) if record else [],
        }

    try:
        snapshot = fetch_snapshot_fn(item.url)
    except Exception as exc:
        return {
            "name": item.title,
            "url": item.url,
            "status": "source_failed",
            "action": action.value,
            "issues": [{"code": "source_failed", "message": str(exc)}],
        }

    existing_result = None
    existing_markdown = None
    if file_path.exists():
        try:
            existing_markdown = file_path.read_text(encoding="utf-8")
            existing_result = validate_markdown(snapshot, existing_markdown, assets_dir)
        except OSError as exc:
            existing_result = None
            logging.warning(f"读取已有Markdown失败: {file_path}: {exc}")

    if action in {IntegrityAction.FETCH_AND_VERIFY, IntegrityAction.FETCH_AND_AUDIT}:
        if existing_result and existing_result.valid:
            with _manifest_lock:
                manifest.records[item.url] = record_from_validation(
                    snapshot,
                    file_path,
                    existing_markdown,
                    existing_result,
                    item.title,
                )
            status = "audit_verified" if mode in {ExportMode.AUDIT, ExportMode.AUDIT_REPAIR} else "adopted"
            return {
                "name": item.title,
                "url": item.url,
                "status": status,
                "action": action.value,
                "markdown_path": file_path.name,
                "text_coverage": existing_result.text_coverage,
                "issues": [],
                "warnings": [warning.__dict__ for warning in existing_result.warnings],
            }
        if mode == ExportMode.AUDIT:
            issues = existing_result.issues if existing_result else []
            return {
                "name": item.title,
                "url": item.url,
                "status": "invalid",
                "action": action.value,
                "markdown_path": file_path.name,
                "issues": [issue.__dict__ for issue in issues],
                "warnings": [
                    warning.__dict__ for warning in (existing_result.warnings if existing_result else [])
                ],
            }

    try:
        # 渲染前并发预取图片：convert_img 命中本地非空文件即不再发请求，
        # 将图片下载从逐张串行改为小线程池并发（图片下载是长文章的主要耗时）。
        prefetch_images(snapshot.image_urls, assets_dir)
        rendered = render_markdown_fn(snapshot, item)
        rendered_result = validate_markdown(snapshot, rendered, assets_dir)
    except Exception as exc:
        return {
            "name": item.title,
            "url": item.url,
            "status": "render_failed",
            "action": action.value,
            "issues": [{"code": "render_failed", "message": str(exc)}],
        }
    if not rendered_result.valid:
        return {
            "name": item.title,
            "url": item.url,
            "status": "render_invalid",
            "action": action.value,
            "issues": [issue.__dict__ for issue in rendered_result.issues],
            "warnings": [warning.__dict__ for warning in rendered_result.warnings],
        }

    atomic_write_text(file_path, rendered)
    final_markdown = file_path.read_text(encoding="utf-8")
    final_result = validate_markdown(snapshot, final_markdown, assets_dir)
    if not final_result.valid:
        return {
            "name": item.title,
            "url": item.url,
            "status": "final_invalid",
            "action": action.value,
            "issues": [issue.__dict__ for issue in final_result.issues],
            "warnings": [warning.__dict__ for warning in final_result.warnings],
        }
    with _manifest_lock:
        manifest.records[item.url] = record_from_validation(
            snapshot,
            file_path,
            final_markdown,
            final_result,
            item.title,
        )
    if mode == ExportMode.FORCE:
        status = "refreshed"
    elif file_path.exists() and existing_markdown is not None:
        status = "repaired"
    else:
        status = "downloaded"
    return {
        "name": item.title,
        "url": item.url,
        "status": status,
        "action": action.value,
        "markdown_path": file_path.name,
        "text_coverage": final_result.text_coverage,
        "issues": [],
        "warnings": [warning.__dict__ for warning in final_result.warnings],
    }


def export_collection_with_integrity(
    collection_name,
    collection_url,
    mode=ExportMode.BALANCED,
    fetch_result=None,
):
    """Export one collection and persist its manifest after each verified item."""
    collection_id = collection_url.split('?')[0].rstrip('/').split('/')[-1]
    result = fetch_result or fetch_collection_items(collection_id)
    collection_report = {
        "name": collection_name,
        "url": collection_url,
        "collection": result.to_dict(),
        "items": [],
    }
    if not result.complete:
        collection_report["status"] = "collection_incomplete"
        return collection_report

    collection_dir = pathlib.Path(get_output_path(collection_name))
    collection_dir.mkdir(parents=True, exist_ok=True)
    manifest = IntegrityManifestStore.load(
        collection_dir / ".zhihu-integrity.json",
        expected_collection_id=collection_id,
        collection_url=collection_url,
    )
    total_items = len(result.exportable_items)
    # 项级并发导出：IO 密集（正文 API + 图片下载），并发数由 ITEM_EXPORT_WORKERS 控制；
    # 清单读写通过 _manifest_lock 互斥，每项完成后仍即时落盘。
    with ThreadPoolExecutor(max_workers=ITEM_EXPORT_WORKERS) as executor:
        futures = []
        for index, item in enumerate(result.exportable_items, start=1):
            logging.info(f"校验 {collection_name} [{index}/{total_items}]: {item.title}")
            futures.append(
                executor.submit(
                    export_item_with_integrity,
                    item,
                    collection_dir,
                    manifest,
                    mode=mode,
                )
            )
        for future in as_completed(futures):
            item_report = future.result()
            collection_report["items"].append(item_report)
            try:
                if item_report.get("url") in manifest.records:
                    with _manifest_lock:
                        manifest.save()
            except OSError as exc:
                # 同步盘锁定等瞬时故障：清单在下一项完成时会再次落盘，不应让整项/收藏夹失败
                logging.warning(f"清单保存失败（将在下一项完成时重试）: {exc}")
    failures = [
        item for item in collection_report["items"]
        if item["status"] in {
            "metadata_failed", "source_failed", "render_failed", "render_invalid",
            "final_invalid", "invalid"
        }
    ]
    has_warnings = bool(result.total_mismatch) or any(
        item.get("warnings") for item in collection_report["items"]
    )
    if failures:
        collection_report["status"] = "invalid"
    elif has_warnings:
        collection_report["status"] = "verified_with_warnings"
    else:
        collection_report["status"] = "verified"
    return collection_report

def process_single_collection(collection_name, collection_url):
    """Compatibility entry point used by the MCP server."""
    global current_collection_name, processing_log
    current_collection_name = collection_name
    report = export_collection_with_integrity(
        collection_name,
        collection_url,
        mode=ExportMode.BALANCED,
    )
    processing_log.append(report)
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="导出知乎收藏夹并校验内容完整性")
    parser.add_argument("--audit", action="store_true", help="严格获取并审计所有支持内容，不修改文件")
    parser.add_argument("--repair", action="store_true", help="与--audit配合，修复审计发现的问题")
    parser.add_argument("--force", action="store_true", help="强制重新下载并校验所有支持内容")
    parser.add_argument(
        "--no-page-candidates",
        action="store_true",
        help="完全禁用回答网页候选抓取，仅用 API 候选校验",
    )
    parser.add_argument(
        "--page-candidates",
        action="store_true",
        help="急切抓取回答网页候选（默认仅当 API 候选失败时才尝试网页候选）",
    )
    args = parser.parse_args(argv)
    if args.no_page_candidates and args.page_candidates:
        parser.error("--page-candidates 与 --no-page-candidates 不能同时使用")
    if args.repair and not args.audit:
        parser.error("--repair 必须与 --audit 一起使用")
    if args.audit and args.force:
        parser.error("--audit 与 --force 不能同时使用")
    if args.audit and args.repair:
        args.mode = ExportMode.AUDIT_REPAIR
    elif args.audit:
        args.mode = ExportMode.AUDIT
    elif args.force:
        args.mode = ExportMode.FORCE
    else:
        args.mode = ExportMode.BALANCED
    return args


def determine_exit_code(collection_reports):
    if any(
        report.get("status") in {"collection_incomplete", "manifest_error", "config_error"}
        or not report.get("collection", {}).get("complete", True)
        for report in collection_reports
    ):
        return 2
    failing_statuses = {
        "invalid",
        "metadata_failed",
        "source_failed",
        "render_failed",
        "render_invalid",
        "final_invalid",
    }
    if any(
        report.get("status") == "invalid"
        or any(item.get("status") in failing_statuses for item in report.get("items", []))
        for report in collection_reports
    ):
        return 1
    return 0


def save_integrity_report(collection_reports, mode, logs_dir=None, timestamp=None):
    directory = pathlib.Path(logs_dir or get_logs_path())
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    exit_code = determine_exit_code(collection_reports)
    report = RunReport(mode=mode, collections=collection_reports, exit_code=exit_code)
    path = directory / f"integrity_{timestamp}.json"
    atomic_write_json(path, report.to_dict())
    return path


def main(argv=None):
    global config, base_output_path, current_collection_name, processing_log
    global PAGE_CANDIDATES_ENABLED, PAGE_CANDIDATES_EAGER
    args = parse_args(argv)
    if args.no_page_candidates:
        PAGE_CANDIDATES_ENABLED = False
    if args.page_candidates:
        PAGE_CANDIDATES_EAGER = True
    config = load_config()

    if config.get('outputPath'):
        base_output_path = parse_output_path(config['outputPath'], config.get('os', ''))
        if base_output_path:
            print(f"使用自定义输出路径: {base_output_path}")
            reconfigure_logging()
        else:
            print("输出路径解析失败，使用默认路径")
            base_output_path = None
    else:
        print("使用默认输出路径: downloads/")

    if config.get('openCollection', False):
        print("检测到openCollection模式已启用，请先运行 fetch_collections.py")
        return 2

    zhihu_collections = config.get('zhihuUrls', [])
    if not isinstance(zhihu_collections, list) or not zhihu_collections:
        print("没有找到要处理的收藏夹配置")
        return 2

    print(f"运行模式: {args.mode.value}")
    if args.no_page_candidates:
        logging.info("已启用 --no-page-candidates，本次运行跳过回答网页候选抓取")
    if args.page_candidates:
        logging.info("已启用 --page-candidates，本次运行将急切抓取回答网页候选")
    print(f"共找到 {len(zhihu_collections)} 个收藏夹待处理")
    collection_reports = []
    for collection in zhihu_collections:
        collection_name = collection.get('name', '未命名收藏夹')
        collection_url = collection.get('url', '')
        if not collection_url:
            collection_reports.append({
                "name": collection_name,
                "url": collection_url,
                "status": "config_error",
                "collection": {"complete": False},
                "items": [],
            })
            continue
        print(f"\n开始处理收藏夹: {collection_name}")
        current_collection_name = collection_name
        try:
            report = export_collection_with_integrity(
                collection_name,
                collection_url,
                mode=args.mode,
            )
        except ManifestSchemaError as exc:
            report = {
                "name": collection_name,
                "url": collection_url,
                "status": "manifest_error",
                "collection": {"complete": False},
                "items": [],
                "issues": [{"code": "manifest_error", "message": str(exc)}],
            }
        except Exception as exc:
            logging.error(f"收藏夹完整性导出失败: {collection_name}: {exc}")
            logging.error(traceback.format_exc())
            report = {
                "name": collection_name,
                "url": collection_url,
                "status": "invalid",
                "collection": {"complete": True},
                "items": [],
                "issues": [{"code": "collection_exception", "message": str(exc)}],
            }
        collection_reports.append(report)

    report_path = save_integrity_report(collection_reports, args.mode)
    exit_code = determine_exit_code(collection_reports)
    verified = sum(
        1
        for collection in collection_reports
        for item in collection.get("items", [])
        if item.get("status") in {"downloaded", "repaired", "refreshed", "adopted", "audit_verified", "skipped_verified"}
    )
    failed = sum(
        1
        for collection in collection_reports
        for item in collection.get("items", [])
        if item.get("status") in {
            "invalid", "metadata_failed", "source_failed", "render_failed",
            "render_invalid", "final_invalid"
        }
    )
    unsupported = sum(
        len(collection.get("collection", {}).get("unsupported_items", []))
        for collection in collection_reports
    )
    print("\n完整性处理完毕")
    print(f"已验证支持内容: {verified}")
    print(f"失败支持内容: {failed}")
    print(f"不支持内容: {unsupported}")
    print(f"完整性报告: {report_path}")
    processing_log = collection_reports
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())

# def testMarkdownifySingleAnswer():
#     url = "https://www.zhihu.com/question/506166712/answer/2271842801"
