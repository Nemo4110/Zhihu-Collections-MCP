# -*- coding:utf-8 -*-
"""渲染模块：快照 HTML → Obsidian 风格 Markdown + 本地图片资产。

接缝说明：网络访问只通过注入的 client（ZhihuClient 或测试假件），
资产写入显式的 assets_dir。图片复用、死链降级、公式转 LaTeX 都
在本模块内。
"""
import logging
import os
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

from markdownify import MarkdownConverter

from integrity import (
    equation_tex_from_url,
    image_filename_from_url,
    image_source_url,
    is_equation_url,
)
from zhihu_client import ZhihuClient, default_client

# 图片预取并发数：zhimg CDN 限流宽松，但与项级并发（3）叠加时控制峰值请求数
IMAGE_PREFETCH_WORKERS = 4


def image_asset_key(url):
    """按知乎内容 hash 归并尺寸变体；其他图片沿用本地文件名语义。"""
    match = re.search(r"v2-[0-9a-fA-F]+", urlsplit(url).path)
    return match.group(0).lower() if match else image_filename_from_url(url)


class ImageAssetTracker:
    """单篇文章的图片活动收集器；预取线程与渲染线程共享，内部互斥。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._activity = {}
        self._failures = {}

    def _urls(self, table, key, url):
        entry = table.setdefault(key, {"urls": []})
        if url not in entry["urls"]:
            entry["urls"].append(url)
        return entry

    def record_downloaded(self, url):
        key = image_asset_key(url)
        with self._lock:
            self._urls(self._activity, key, url)["status"] = "downloaded"
            self._failures.pop(key, None)

    def record_reused(self, url):
        key = image_asset_key(url)
        with self._lock:
            entry = self._urls(self._activity, key, url)
            if entry.get("status") != "downloaded":
                entry["status"] = "reused"
            self._failures.pop(key, None)

    def record_failure(self, url, error):
        key = image_asset_key(url)
        with self._lock:
            entry = self._urls(self._failures, key, url)
            entry.update({
                "asset_key": key,
                "status": "remote_fallback",
                "message": str(error),
            })

    def summary(self):
        with self._lock:
            return {
                "downloaded": sum(
                    entry.get("status") == "downloaded" for entry in self._activity.values()
                ),
                "reused": sum(
                    entry.get("status") == "reused" for entry in self._activity.values()
                ),
            }

    def issues(self):
        with self._lock:
            return [dict(entry, urls=list(entry["urls"])) for entry in self._failures.values()]


def _asset_file_ready(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def prefetch_images(
    image_urls,
    assets_dir,
    request_get=None,
    max_workers=IMAGE_PREFETCH_WORKERS,
    asset_tracker=None,
):
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
            if asset_tracker is not None:
                asset_tracker.record_reused(url)
            continue
        targets.append((url, target_path))
    if not targets:
        return

    os.makedirs(assets_dir, exist_ok=True)

    def _fetch(pair):
        url, target_path = pair
        try:
            client = ZhihuClient(
                transport=request_get, cookies=default_client().cookies
            ) if request_get else default_client()
            content = client.download(url)
            with open(target_path, 'wb') as fp:
                fp.write(content)
            if asset_tracker is not None:
                asset_tracker.record_downloaded(url)
        except OSError as exc:  # 含 requests.RequestException（IOError 子类）
            # 预取只是优化；最终渲染是否降级由 convert_img 统一记录。
            logging.debug(f"图片预取失败，渲染时重试: {url}: {exc}")

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
        logging.debug(f"convert_img called with args: {args}, kwargs: {kwargs}")
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

            # 资产目录由 Renderer 显式注入
            assetsDir = self.options['assets_dir']
            if not os.path.exists(assetsDir):
                os.makedirs(assetsDir, exist_ok=True)

            img_content_name = image_filename_from_url(src)
            imgPath = os.path.join(assetsDir, img_content_name)
            tracker = self.options.get('asset_tracker')
            # 预取阶段已下载或历史渲染留下的非空文件直接复用（图片 URL 含内容 hash）
            if not _asset_file_ready(imgPath):
                try:
                    img_content = self.options['client'].download(src)
                    with open(imgPath, 'wb') as fp:
                        fp.write(img_content)
                    if tracker is not None:
                        tracker.record_downloaded(src)
                except OSError as exc:
                    if not _asset_file_ready(imgPath):
                        # 远端资源已失效（如 404 死链）时降级为远程引用，保证正文完整导出；
                        # 该项记为 warning，下次运行会自动重试，远端恢复后升级为本地备份。
                        if tracker is not None:
                            tracker.record_failure(src, exc)
                        logging.debug(f"图片下载失败，降级为远程引用: {src}: {exc}")
                        escaped_alt = alt.replace('\n', ' ').replace(']', r'\]')
                        return f"![{escaped_alt}]({src})"
                    logging.warning(f"图片下载失败，复用已有资源: {src}")

            if tracker is not None:
                tracker.record_reused(src)
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
        logging.debug(f"convert_a called with args: {args}, kwargs: {kwargs}")
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
            logging.error(f"Traceback: {__import__('traceback').format_exc()}")
            raise

    def convert_li(self, *args, **kwargs):
        logging.debug(f"convert_li called with args: {args}, kwargs: {kwargs}")
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
            logging.error(f"Traceback: {__import__('traceback').format_exc()}")
            raise


class Renderer:
    """快照 HTML → Markdown 渲染器；资产落盘到 assets_dir，下载经 client。"""

    def __init__(self, assets_dir, client=None, asset_tracker=None):
        self.assets_dir = assets_dir
        self.client = client if client is not None else default_client()
        self.asset_tracker = asset_tracker

    def render(self, html, **options):
        options.setdefault('heading_style', 'ATX')
        options.setdefault('asset_tracker', self.asset_tracker)
        converter = ObsidianStyleConverter(
            assets_dir=self.assets_dir,
            client=self.client,
            **options,
        )
        return converter.convert(html)


def render_markdown(html, assets_dir, client=None, asset_tracker=None, **options):
    """一次性渲染便捷函数；多次渲染同一目录时优先复用 Renderer 实例。"""
    return Renderer(
        assets_dir, client=client, asset_tracker=asset_tracker
    ).render(html, **options)
