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
import json
import logging
import traceback
import platform
import pathlib
from urllib.parse import quote

from markdownify import MarkdownConverter

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
    equation_tex_from_url,
    image_filename_from_url,
    image_source_url,
    is_equation_url,
    local_record_is_intact,
    parse_source_identity,
    record_from_validation,
    snapshot_from_html,
    validate_markdown,
)


# 读取配置文件
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config
    except FileNotFoundError:
        print("未找到config.json文件，尝试读取旧版zhihuUrls.json文件")
        try:
            with open('zhihuUrls.json', 'r', encoding='utf-8') as f:
                urls = json.load(f)
                return {"zhihuUrls": urls, "outputPath": "", "os": ""}
        except FileNotFoundError:
            print("未找到配置文件，请创建config.json文件并配置收藏夹信息")
            return {"zhihuUrls": [], "outputPath": "", "os": ""}

# 获取当前操作系统类型
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
def parse_output_path(path_str, os_type):
    if not path_str:
        return None
    
    # 如果没有指定os，则自动检测
    if not os_type:
        os_type = get_current_os()
    
    try:
        if os_type.lower() == "windows":
            # Windows路径处理
            # 支持 D:\path\to\folder 或 D:/path/to/folder 格式
            path_str = path_str.replace('/', '\\')
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["linux", "freebsd", "openbsd", "netbsd", "solaris", "aix"]:
            # Unix-like系统路径处理
            # 支持 /usr/local/lib 格式
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["macos", "darwin"]:
            # macOS路径处理
            # 支持 /Users/username/Documents 或 ~/Documents 格式
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["cygwin", "msys"]:
            # Cygwin/MSYS环境路径处理
            # 支持 /cygdrive/c/path 或 /c/path 格式
            if path_str.startswith('/cygdrive/'):
                # Cygwin格式: /cygdrive/c/path -> C:\path
                drive_path = path_str[10:]  # 移除 /cygdrive/
                if len(drive_path) >= 2 and drive_path[1] == '/':
                    path_str = drive_path[0].upper() + ':' + drive_path[1:].replace('/', '\\')
            elif path_str.startswith('/') and len(path_str) >= 3 and path_str[2] == '/':
                # MSYS格式: /c/path -> C:\path
                path_str = path_str[1].upper() + ':' + path_str[2:].replace('/', '\\')
            return pathlib.Path(path_str).resolve()
        else:
            # 其他系统，尝试通用处理
            logging.warning(f"未知操作系统类型: {os_type}，尝试通用路径处理")
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
    except Exception as e:
        logging.error(f"路径解析失败: {path_str}, 错误: {str(e)}")
        return None

# 读取cookies
def load_cookies(cookie_file=None):
    cookie_path = pathlib.Path(
        cookie_file or os.environ.get("ZHIHU_COOKIES_FILE", "cookies.json")
    )
    try:
        with cookie_path.open('r', encoding='utf-8') as f:
            cookies_list = json.load(f)
        cookies_dict = {}
        for cookie in cookies_list:
            cookies_dict[cookie['name']] = cookie['value']
        return cookies_dict
    except FileNotFoundError:
        print("未找到cookies.json文件，将使用无登录模式访问（部分内容可能无法获取）")
        return {}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"cookies文件格式无效，将使用无登录模式访问: {exc}")
        return {}

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
    
    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), collection_name)
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', collection_name)

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

def smart_content_detection(soup, url):
    """
    智能内容检测 - 当标准选择器失败时的备用方案
    """
    logging.debug(f"开始智能内容检测: {url}")
    
    # 策略1: 查找包含大量文本的div元素
    all_divs = soup.find_all('div')
    text_length_threshold = 200  # 最少文本长度
    
    candidates = []
    for div in all_divs:
        text_content = div.get_text(strip=True)
        if len(text_content) > text_length_threshold:
            # 计算直接子节点中的文本密度
            direct_text_length = len(''.join(div.find_all(text=True, recursive=False)))
            total_length = len(text_content)
            
            # 过滤掉主要是链接或导航的容器
            link_count = len(div.find_all('a'))
            text_to_link_ratio = total_length / max(link_count, 1)
            
            candidates.append({
                'element': div,
                'text_length': total_length,
                'text_to_link_ratio': text_to_link_ratio,
                'classes': div.get('class', [])
            })
    
    # 按文本长度排序，选择最长的
    candidates.sort(key=lambda x: x['text_length'], reverse=True)
    
    if candidates:
        best_candidate = candidates[0]
        logging.debug(f"智能检测找到候选内容，长度: {best_candidate['text_length']}, classes: {best_candidate['classes']}")
        
        # 如果最佳候选者文本长度足够长，返回它
        if best_candidate['text_length'] > 500:
            return best_candidate['element']
    
    # 策略2: 查找文章相关的容器
    article_containers = soup.find_all(['article', 'main'])
    for container in article_containers:
        text_content = container.get_text(strip=True)
        if len(text_content) > text_length_threshold:
            logging.debug(f"找到文章容器: {container.name}")
            return container
    
    # 策略3: 查找包含多个段落的容器
    for div in all_divs:
        paragraphs = div.find_all('p')
        if len(paragraphs) >= 3:  # 至少3个段落
            total_p_text = sum(len(p.get_text(strip=True)) for p in paragraphs)
            if total_p_text > text_length_threshold:
                logging.debug(f"找到多段落容器，段落数: {len(paragraphs)}")
                return div
    
    logging.debug("智能内容检测未找到合适的内容")
    return None

def analyze_page_error(soup, response, url):
    """
    分析页面错误类型，区分404、登录要求、解析失败等
    """
    page_text = response.text.lower()
    
    # 检查404错误
    if '404' in page_text or 'not found' in page_text or '页面不存在' in page_text:
        return "该文章链接被404, 无法直接访问"
    
    # 检查登录要求
    if '登录' in response.text or 'login' in page_text or '请先登录' in response.text:
        return "该文章需要登录访问，请检查cookies配置"
    
    # 检查访问权限
    if '403' in page_text or 'forbidden' in page_text or '访问被拒绝' in response.text:
        return "该文章访问被拒绝，可能需要特殊权限"
    
    # 检查内容是否被删除
    if '已删除' in response.text or '内容不存在' in response.text or 'deleted' in page_text:
        return "该文章内容已被删除或不存在"
    
    # 检查是否有重定向
    if response.url != url:
        return f"页面被重定向到: {response.url}, 可能是登录页面或错误页面"
    
    # 检查页面是否包含正常的知乎页面结构
    zhihu_indicators = ['知乎', 'zhihu', 'www.zhihu.com']
    has_zhihu_structure = any(indicator in page_text for indicator in zhihu_indicators)
    
    if not has_zhihu_structure:
        return "页面结构异常，可能不是正常的知乎页面"
    
    # 如果页面看起来正常但找不到内容，可能是页面结构变化
    return "页面结构可能发生变化，无法解析文章内容"

debug_log_file = setup_debug_logging()

cookies = load_cookies()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Connection": "keep-alive",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.zhihu.com/",
    "sec-ch-ua": "\"Microsoft Edge\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# API 请求的 headers（用于获取回答内容）
api_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.zhihu.com/",
    "x-requested-with": "fetch",
    "sec-ch-ua": "\"Microsoft Edge\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
}

def _download_image_content(src, request_get=None, sleep=None, attempts=3):
    request_get = request_get or requests.get
    sleep = sleep or time.sleep
    last_error = None
    for attempt in range(attempts):
        try:
            response = request_get(
                url=src,
                headers=headers,
                cookies=cookies,
                timeout=30,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep(attempt + 1)
    raise last_error


# 图片预取并发数：zhimg CDN 限流宽松，但与项级并发（3）叠加时控制峰值请求数
IMAGE_PREFETCH_WORKERS = 4


def _asset_file_ready(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def prefetch_images(image_urls, assets_dir, request_get=None, max_workers=IMAGE_PREFETCH_WORKERS):
    """渲染前并发预取图片到 assets 目录。

    本地已有同名非空文件时跳过（zhimg 图片 URL 含内容 hash，内容更新会换 URL），
    失败只记录日志，留给渲染阶段的单图重试兜底。
    """
    targets = []
    seen = set()
    for url in image_urls or []:
        if not url or url in seen or is_equation_url(url):
            continue
        seen.add(url)
        target_path = os.path.join(assets_dir, image_filename_from_url(url))
        if _asset_file_ready(target_path):
            continue
        targets.append((url, target_path))
    if not targets:
        return

    os.makedirs(assets_dir, exist_ok=True)

    def _fetch(pair):
        url, target_path = pair
        try:
            content = _download_image_content(url, request_get=request_get)
            with open(target_path, 'wb') as fp:
                fp.write(content)
        except requests.RequestException as exc:
            logging.warning(f"图片预取失败，渲染时重试: {url}: {exc}")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_fetch, targets))
    logging.debug(f"图片预取完成: {len(seen)} 个URL，其中 {len(targets)} 张新下载")


class ObsidianStyleConverter(MarkdownConverter):
    """
    Create a custom MarkdownConverter that adds two newlines after an image
    """

    def chomp(self, text):
        """
        If the text in an inline tag like b, a, or em contains a leading or trailing
        space, strip the string and return a space as suffix of prefix, if needed.
        This function is used to prevent conversions like
            <b> foo</b> => ** foo**
        """
        prefix = ' ' if text and text[0] == ' ' else ''
        suffix = ' ' if text and text[-1] == ' ' else ''
        text = text.strip()
        return (prefix, suffix, text)

    def convert_img(self, *args, **kwargs):
        logging.debug(f"convert_img called with args: {args}, kwargs={kwargs}")
        try:
            # 提取参数，适配不同的调用方式
            if len(args) >= 2:
                el, text = args[0], args[1]
            else:
                el = kwargs.get('el')
                text = kwargs.get('text', '')
            
            alt = el.attrs.get('alt', None) or ''
            src = image_source_url(el)
            if not src:
                return ''

            if is_equation_url(src):
                tex = alt or equation_tex_from_url(src)
                if not tex:
                    return ''
                parent = getattr(el, 'parent', None)
                is_block = len(tex) > 70 or bool(
                    parent
                    and getattr(parent, 'name', None) in {'p', 'div', 'figure'}
                    and not parent.get_text('', strip=True)
                    and len(parent.find_all('img')) == 1
                )
                result = f"\n\n$$\n{tex}\n$$\n\n" if is_block else f"${tex}$"
                logging.debug(f"convert_img returning equation: {result}")
                return result

            # 使用全局变量获取当前收藏夹名称
            global current_collection_name
            downloadDir = get_output_path(current_collection_name)
            if not os.path.exists(downloadDir):
                os.makedirs(downloadDir)
            assetsDir = os.path.join(downloadDir,'assets')
            if not os.path.exists(assetsDir):
                os.makedirs(assetsDir)

            img_content_name = image_filename_from_url(src)
            imgPath = os.path.join(assetsDir, img_content_name)
            # 预取阶段已下载或历史渲染留下的非空文件直接复用（图片 URL 含内容 hash）
            if not _asset_file_ready(imgPath):
                try:
                    img_content = _download_image_content(src)
                    with open(imgPath, 'wb') as fp:
                        fp.write(img_content)
                except requests.RequestException:
                    if not _asset_file_ready(imgPath):
                        raise
                    logging.warning(f"图片下载失败，复用已有资源: {src}")

            escaped_alt = alt.replace('\n', ' ').replace(']', r'\]')
            asset_path = quote(f"assets/{img_content_name}", safe="/-._~")
            result = f"![{escaped_alt}]({asset_path})"
            logging.debug(f"convert_img returning: {result}")
            return result
        except Exception as e:
            logging.error(f"convert_img error: {str(e)}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise

    def convert_a(self, *args, **kwargs):
        logging.debug(f"convert_a called with args: {args}, kwargs={kwargs}")
        try:
            # 提取参数，适配不同的调用方式
            if len(args) >= 2:
                el, text = args[0], args[1]
                convert_as_inline = args[2] if len(args) > 2 else None
            else:
                el = kwargs.get('el')
                text = kwargs.get('text', '')
                convert_as_inline = kwargs.get('convert_as_inline')
            
            prefix, suffix, text = self.chomp(text)
            if not text:
                return ''
            href = el.get('href')
            # title = el.get('title')

            if el.get('aria-labelledby') and el.get('aria-labelledby').find('ref') > -1:
                text = text.replace('[', '[^')
                result = '%s' % text
                logging.debug(f"convert_a returning (aria-labelledby): {result}")
                return result
            if (el.attrs and 'data-reference-link' in el.attrs) or ('class' in el.attrs and ('ReferenceList-backLink' in el.attrs['class'])):
                text = '[^{}]: '.format(href[5])
                result = '%s' % text
                logging.debug(f"convert_a returning (reference-link): {result}")
                return result

            # 调用父类方法，适配不同的参数组合
            try:
                if convert_as_inline is not None:
                    result = super(ObsidianStyleConverter, self).convert_a(el, text, convert_as_inline, **kwargs)
                else:
                    result = super(ObsidianStyleConverter, self).convert_a(el, text, **kwargs)
            except TypeError:
                # 如果参数不匹配，尝试不同的调用方式
                try:
                    result = super(ObsidianStyleConverter, self).convert_a(*args, **kwargs)
                except TypeError:
                    result = super(ObsidianStyleConverter, self).convert_a(el, text)
            
            logging.debug(f"convert_a returning (super): {result}")
            return result
        except Exception as e:
            logging.error(f"convert_a error: {str(e)}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise

    def convert_li(self, *args, **kwargs):
        logging.debug(f"convert_li called with args: {args}, kwargs={kwargs}")
        try:
            # 提取参数，适配不同的调用方式
            if len(args) >= 2:
                el, text = args[0], args[1]
                convert_as_inline = args[2] if len(args) > 2 else None
            else:
                el = kwargs.get('el')
                text = kwargs.get('text', '')
                convert_as_inline = kwargs.get('convert_as_inline')
            
            if el and el.find('a', {'aria-label': 'back'}) is not None:
                result = '%s\n' % ((text or '').strip())
                logging.debug(f"convert_li returning (aria-label back): {result}")
                return result

            # 调用父类方法，适配不同的参数组合
            try:
                if convert_as_inline is not None:
                    result = super(ObsidianStyleConverter, self).convert_li(el, text, convert_as_inline, **kwargs)
                else:
                    result = super(ObsidianStyleConverter, self).convert_li(el, text, **kwargs)
            except TypeError:
                # 如果参数不匹配，尝试不同的调用方式
                try:
                    result = super(ObsidianStyleConverter, self).convert_li(*args, **kwargs)
                except TypeError:
                    result = super(ObsidianStyleConverter, self).convert_li(el, text)
            
            logging.debug(f"convert_li returning (super): {result}")
            return result
        except Exception as e:
            logging.error(f"convert_li error: {str(e)}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise

def markdownify(html, **options):
    return ObsidianStyleConverter(**options).convert(html)


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
            response = request_get(
                collection_url,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
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
                response = request_get(
                    collection_url,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
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
    response = request_get(
        f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=updated_time",
        headers=api_headers,
        cookies=cookies,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
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
        api_response = request_get(
            f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content,updated_time",
            headers=api_headers,
            cookies=cookies,
            timeout=30,
        )
        api_response.raise_for_status()
        payload = api_response.json()
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
            page_response = request_get(
                canonical_url,
                headers=headers,
                cookies=cookies,
                timeout=30,
            )
            page_response.raise_for_status()
            candidates.extend(parse_answer_page_candidates(metadata, page_response.text))
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
        response = request_get(
            _article_api_url(article_id),
            headers=api_headers,
            cookies=cookies,
            timeout=30,
        )
        response.raise_for_status()
        return _article_metadata_from_payload(
            canonical_url,
            source_type,
            article_id,
            response.json(),
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
        api_response = request_get(
            _article_api_url(article_id),
            headers=api_headers,
            cookies=cookies,
            timeout=30,
        )
        api_response.raise_for_status()
        payload = api_response.json()
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

    response = request_get(
        canonical_url,
        headers=headers,
        cookies=cookies,
        timeout=30,
    )
    response.raise_for_status()
    return parse_article_page_candidates(metadata, response.text)


def fetch_source_snapshot(url, request_get=None):
    source_type, _ = parse_source_identity(url)
    candidates = (
        fetch_answer_snapshots(url, request_get=request_get)
        if source_type == "answer"
        else fetch_article_snapshots(url, request_get=request_get)
    )
    return choose_best_snapshot(candidates)


# 获得单条答案的数据
def get_single_answer_content(answer_url):
    logging.debug(f"开始获取回答内容: {answer_url}")
    flush_logs()

    # 从URL中提取answer_id（用于API备用请求）
    # URL格式: https://www.zhihu.com/question/xxx/answer/yyy 或 https://www.zhihu.com/question/xxx/answer/yyy?param
    import re
    answer_id_match = re.search(r'/answer/(\d+)', answer_url)
    answer_id = answer_id_match.group(1) if answer_id_match else None

    try:
        # 发送请求
        html_content = requests.get(answer_url, headers=headers, cookies=cookies)
        logging.debug(f"HTTP请求状态码: {html_content.status_code}")

        # 如果返回403，尝试使用API备用方案
        if html_content.status_code == 403:
            logging.warning(f"HTML请求返回403，尝试API备用方案: {answer_url}")
            flush_logs()

            if answer_id:
                api_url = f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content"
                logging.debug(f"调用API: {api_url}")

                try:
                    api_response = requests.get(api_url, headers=api_headers, cookies=cookies)
                    api_response.raise_for_status()
                    api_data = api_response.json()

                    if api_data and 'content' in api_data:
                        # API返回的content是HTML格式的回答内容
                        content_html = api_data['content']
                        logging.info(f"API备用方案成功获取内容，长度: {len(content_html)}")
                        flush_logs()

                        # 直接使用API返回的HTML内容，不需要BeautifulSoup解析
                        # 但需要处理图片和链接卡片
                        soup = BeautifulSoup(content_html, "lxml")

                        # 去除不必要的style标签
                        for el in soup.find_all('style'):
                            el.extract()

                        for el in soup.select('img[src*="data:image/svg+xml"]'):
                            el.extract()

                        for el in soup.find_all('a'):
                            aclass = el.get('class')
                            if isinstance(aclass, list) and 'LinkCard' in aclass:
                                linkcard_name = el.get('data-text')
                                el.string = linkcard_name if linkcard_name is not None else el.get('href')
                            try:
                                if el.get('href') and el.get('href').startswith('mailto'):
                                    el.name = 'p'
                            except:
                                pass

                        # 添加html外层标签
                        answer_content = html_template(soup)
                        return answer_content

                except Exception as api_e:
                    logging.error(f"API备用方案失败: {str(api_e)}")
                    flush_logs()
            else:
                logging.error(f"无法从URL提取answer_id: {answer_url}")
                flush_logs()
                return -1

        html_content.raise_for_status()
        logging.debug(f"HTTP请求成功，状态码: {html_content.status_code}")

        soup = BeautifulSoup(html_content.text, "lxml")
        
        # 尝试多种可能的选择器
        answer_content = None
        selectors = [
            ('div', {'class': "AnswerCard"}),
            ('div', {'class': "QuestionAnswer-content"}),
            ('div', {'class': "RichContent"}),
            ('div', {'class': "ContentItem-expandButton"}),
        ]
        
        for tag, attrs in selectors:
            elements = soup.find_all(tag, attrs)
            if elements:
                logging.debug(f"找到{len(elements)}个 {tag} {attrs} 元素")
                for element in elements:
                    inner = element.find("div", class_="RichContent-inner")
                    if inner:
                        answer_content = inner
                        logging.debug("成功找到RichContent-inner元素")
                        break
                if answer_content:
                    break
        
        # 如果还没找到，尝试直接查找RichContent-inner
        if not answer_content:
            answer_content = soup.find("div", class_="RichContent-inner")
            if answer_content:
                logging.debug("直接找到RichContent-inner元素")
        
        # 如果仍未找到，尝试其他可能的内容容器
        if not answer_content:
            fallback_selectors = [
                "div.RichText",
                "div.Post-RichText", 
                "div.ContentItem-content",
                ".QuestionAnswer .RichContent",
            ]
            
            for selector in fallback_selectors:
                answer_content = soup.select_one(selector)
                if answer_content:
                    logging.debug(f"使用备用选择器找到内容: {selector}")
                    break
        
        if not answer_content:
            logging.error(f"未找到回答内容容器: {answer_url}")
            # 保存页面HTML以供调试
            debug_dir = get_debug_path()
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = os.path.join(debug_dir, f"debug_answer_{answer_url.split('/')[-1]}.html")
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html_content.text)
            logging.debug(f"页面HTML已保存到: {debug_file}")
            flush_logs()
            return -1
        
        # 去除不必要的style标签
        for el in answer_content.find_all('style'):
            el.extract()
            
    except Exception as e:
        logging.error(f"获取回答内容时发生错误: {str(e)}")
        logging.error(f"URL: {answer_url}")
        flush_logs()
        return -1

    for el in answer_content.select('img[src*="data:image/svg+xml"]'):
        el.extract()
    
    for el in answer_content.find_all('a'): # 处理回答中的卡片链接
        aclass = el.get('class')
        if isinstance(aclass, list):
            if aclass[0] == 'LinkCard':
                linkcard_name = el.get('data-text')
                el.string = linkcard_name if linkcard_name is not None else el.get('href')
        else:
            pass
        try:
            if el.get('href').startswith('mailto'): # 特殊bug, 正文的aaa@bbb.ccc会被识别为邮箱, 嵌入<a href='mailto:xxx'>中, markdown转换时会报错
                el.name = 'p'
        except:
            print(answer_url, el) # 一些广告卡片, 不需要处理
        
    # 添加html外层标签
    answer_content = html_template(answer_content)

    return answer_content


# 获取单条专栏文章的内容
def get_single_post_content(paper_url):
    logging.debug(f"开始获取专栏文章内容: {paper_url}")
    flush_logs()

    # 专栏文章可能较大，添加重试机制
    max_retries = 3
    last_error = None

    for retry in range(max_retries):
        post_content = None
        try:
            # 发送请求，添加超时设置，使用流式下载处理大文章
            html_content = requests.get(paper_url, headers=headers, cookies=cookies, timeout=120, stream=True)
            html_content.raise_for_status()
            logging.debug(f"HTTP请求成功，状态码: {html_content.status_code}")

            # 流式读取内容，避免大文章的网络中断问题
            content_bytes = b''
            for chunk in html_content.iter_content(chunk_size=8192):
                if chunk:
                    content_bytes += chunk
            html_text = content_bytes.decode('utf-8', errors='ignore')
            logging.debug(f"内容长度: {len(html_text)} 字符")

            soup = BeautifulSoup(html_text, "lxml")

            # 尝试多种可能的选择器
            selectors = [
                ('div', {'class': "Post-RichText"}),
                ('div', {'class': "RichContent"}),
                ('div', {'class': "RichContent-inner"}),
                ('div', {'class': "Post-content"}),
                ('div', {'class': "Post-RichTextContainer"}),
                ('div', {'class': "ztext"}),
                ('div', {'class': "Post-Main"}),
                ('div', {'class': "Article-RichText"}),
            ]

            for tag, attrs in selectors:
                post_content = soup.find(tag, attrs)
                if post_content:
                    logging.debug(f"找到专栏内容: {tag} {attrs}")
                    break

            # 如果还没找到，尝试CSS选择器
            if not post_content:
                fallback_selectors = [
                    "div.RichText",
                    "div.Post-content",
                    "div.ContentItem-content",
                    ".Post .RichContent",
                    ".Post-RichTextContainer",
                    ".ztext",
                    ".Post-Main .RichContent",
                    "[data-zop-editor]",
                    ".Article-RichText",
                ]

                for selector in fallback_selectors:
                    post_content = soup.select_one(selector)
                    if post_content:
                        logging.debug(f"使用备用选择器找到内容: {selector}")
                        break

            # 如果仍然没找到，尝试智能内容检测
            if not post_content:
                post_content = smart_content_detection(soup, paper_url)
                if post_content:
                    logging.debug("使用智能内容检测找到内容")

            if not post_content:
                # 检查是否是真正的404或其他错误
                error_message = analyze_page_error(soup, html_content, paper_url)

                logging.error(f"未找到专栏内容容器: {paper_url} - {error_message}")
                # 保存页面HTML以供调试
                debug_dir = get_debug_path()
                os.makedirs(debug_dir, exist_ok=True)
                debug_file = os.path.join(debug_dir, f"debug_post_{paper_url.split('/')[-1]}.html")
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(html_content.text)
                logging.debug(f"页面HTML已保存到: {debug_file}")
                flush_logs()
                post_content = error_message

            if post_content and hasattr(post_content, 'find_all'):
                # 去除不必要的style标签
                for el in post_content.find_all('style'):
                    el.extract()

                for el in post_content.select('img[src*="data:image/svg+xml"]'):
                    el.extract()

                for el in post_content.find_all('a'): # 处理专栏文章中的卡片链接
                    aclass = el.get('class')
                    if isinstance(aclass, list):
                        if aclass[0] == 'LinkCard':
                            linkcard_name = el.get('data-text')
                            el.string = linkcard_name if linkcard_name is not None else el.get('href')
                    else:
                        pass
                    try:
                        if el.get('href').startswith('mailto'): # 特殊bug, 正文的aaa@bbb.ccc会被识别为邮箱, 嵌入<a href='mailto:xxx'>中, markdown转换时会报错
                            el.name = 'p'
                    except:
                        logging.warning(f"处理链接时出现问题: {paper_url}, {el}")

            # 成功获取内容，跳出重试循环
            if post_content:
                break

        except Exception as e:
            last_error = str(e)
            logging.warning(f"获取专栏文章内容时发生错误 (尝试 {retry + 1}/{max_retries}): {str(e)}")
            logging.warning(f"URL: {paper_url}")
            flush_logs()

            if retry < max_retries - 1:
                import time
                time.sleep(2)  # 等待2秒后重试
                continue
            else:
                logging.error(f"达到最大重试次数，放弃: {paper_url}")
                flush_logs()
                post_content = f"该文章链接获取失败: {last_error}"

    # 添加html外层标签
    post_content = html_template(post_content)

    return post_content


def html_template(data):
    # api content
    html = '''
        <html>
        <head>
        </head>
        <body>
        %s
        </body>
        </html>
        ''' % data
    return html


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



def flush_logs():
    """强制刷新所有日志处理器"""
    import sys
    root_logger = logging.getLogger()
    
    for handler in root_logger.handlers:
        try:
            if hasattr(handler, 'flush'):
                handler.flush()
            # 如果是文件处理器，强制同步
            if hasattr(handler, 'stream') and hasattr(handler.stream, 'flush'):
                handler.stream.flush()
                # 强制操作系统刷新
                if hasattr(handler.stream, 'fileno'):
                    try:
                        os.fsync(handler.stream.fileno())
                    except:
                        pass
        except:
            pass
    
    # 强制刷新标准输出
    sys.stdout.flush()
    sys.stderr.flush()


def _default_fetch_metadata(item):
    if item.source_type == "answer":
        return fetch_answer_metadata(item.url)
    if item.source_type == "article":
        return fetch_article_metadata(item.url)
    return SourceMetadata(item.url, item.source_type, item.source_id, None)


def _default_render_markdown(snapshot, item):
    body = markdownify(snapshot.html, heading_style="ATX")
    return f"> {item.url}\n{body}"


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
    render_markdown_fn = render_markdown_fn or _default_render_markdown

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
            if item_report.get("url") in manifest.records:
                with _manifest_lock:
                    manifest.save()
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
#     content = get_single_answer_content(url)
#     md = markdownify(content, heading_style="ATX")
#     id = url.split('/')[-1]
#
#     downloadDir = os.path.join(os.path.dirname(__file__), 'downloads', '剪藏')
#     if not os.path.exists(downloadDir):
#         os.makedirs(downloadDir)
#     with open(os.path.join(downloadDir, id + ".md"), "w", encoding='utf-8') as md_file:
#         md_file.write(md)
#     print("{} 转换成功".format(id))
#
# def testMarkdownifySinglePost():
#     url = 'https://zhuanlan.zhihu.com/p/386395767'
#     content = get_single_post_content(url)
#     md = markdownify(content, heading_style="ATX")
#     id = url.split('/')[-1]
#     with open("./" + id + ".md", "w", encoding='utf-8') as md_file:
#         md_file.write(md)
#     print("{} 转换成功".format(id))
#
#
# # if __name__ == '__main__':
# #     testMarkdownifySingleAnswer()
#
