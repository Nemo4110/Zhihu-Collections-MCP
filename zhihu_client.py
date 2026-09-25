# -*- coding:utf-8 -*-
"""知乎 HTTP 客户端：系统边界适配器。

接缝说明：ZhihuClient 把"传输 + 认证头 + Cookie + 重试/退避"收进一个
小接口（get_api / get_page / get_page_data / download），生产环境用
requests 传输，测试注入假传输。页面请求与 API 请求的头部差异是
知乎的反爬语义，由本模块统一持有，调用方无需处理头部。
"""
import time

import requests

# 页面请求的 headers（HTML 文档）
PAGE_HEADERS = {
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

# API 请求的 headers（JSON 接口）
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.zhihu.com/",
    "x-requested-with": "fetch",
    "sec-ch-ua": "\"Microsoft Edge\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
}


_default_instance = None


def default_client():
    """进程内共享的默认客户端：惰性加载一次 Cookie。"""
    global _default_instance
    if _default_instance is None:
        from paths_config import load_cookies
        _default_instance = ZhihuClient(cookies=load_cookies())
    return _default_instance


class ZhihuClient:
    """知乎 HTTP 访问的唯一入口：注入传输与 Cookie，隐藏头部与重试。"""

    def __init__(self, transport=None, cookies=None, sleep=time.sleep):
        self._transport = transport or requests.get
        self.cookies = cookies if cookies is not None else {}
        self._sleep = sleep

    def get_api(self, url, timeout=30):
        """请求知乎 JSON API，返回解析后的 dict。HTTP 错误状态抛 HTTPError。"""
        response = self._transport(
            url=url,
            headers=API_HEADERS,
            cookies=self.cookies,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_page_data(self, url, timeout=30):
        """请求页面风格的 JSON 接口（如收藏夹分页），返回解析后的 dict。

        收藏夹分页 API 要求页面请求头（无 x-requested-with），与
        get_api 的 API 风格头部不同，故独立成具名方法。
        """
        response = self._transport(
            url=url,
            headers=PAGE_HEADERS,
            cookies=self.cookies,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_page(self, url, timeout=30):
        """请求知乎 HTML 页面，返回页面文本。HTTP 错误状态抛 HTTPError。"""
        response = self._transport(
            url=url,
            headers=PAGE_HEADERS,
            cookies=self.cookies,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text

    def download(self, url, attempts=3, timeout=30):
        """下载二进制内容（图片等），网络错误按退避重试，最终失败抛最后一次异常。"""
        last_error = None
        for attempt in range(attempts):
            try:
                response = self._transport(
                    url=url,
                    headers=PAGE_HEADERS,
                    cookies=self.cookies,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.content
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._sleep(attempt + 1)
        raise last_error
