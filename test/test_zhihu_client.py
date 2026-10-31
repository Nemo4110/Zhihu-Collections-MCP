# -*- coding: utf-8 -*-
"""ZhihuClient 接缝的行为测试。

接缝说明：ZhihuClient 是知乎 HTTP 的系统边界适配器（传输 + 认证头 +
Cookie + 重试）。测试注入假传输，只断言"以正确的参数调用传输、
正确解析响应、正确重试"，不发真实网络请求。
"""
import unittest
from unittest.mock import patch

import requests

import zhihu_client
from zhihu_client import (
    AuthenticationCheckError,
    AuthenticationError,
    ZhihuClient,
    preflight_authentication,
)


class FakeResponse:
    def __init__(self, payload=None, text="", content=b"", status_ok=True, status_code=None):
        self._payload = payload
        self.text = text
        self.content = content
        self._status_ok = status_ok
        self.status_code = status_code or (200 if status_ok else 403)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError(str(self.status_code), response=self)


class RecordingTransport:
    """记录所有调用，按 URL 前缀路由到预设响应。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url=None, **kwargs):
        self.calls.append({"url": url, **kwargs})
        for prefix, response in self.routes:
            if url.startswith(prefix):
                return response
        raise AssertionError(f"unexpected url: {url}")


class NoSleep:
    def __call__(self, seconds):
        pass


class GetApiTests(unittest.TestCase):
    def test_parses_json_payload(self):
        transport = RecordingTransport(
            [("https://www.zhihu.com/api/", FakeResponse(payload={"id": 2}))]
        )
        client = ZhihuClient(transport=transport, cookies={"z_c0": "t"}, sleep=NoSleep())
        self.assertEqual(client.get_api("https://www.zhihu.com/api/v4/answers/2"), {"id": 2})

    def test_sends_api_headers_cookies_and_timeout(self):
        transport = RecordingTransport(
            [("https://www.zhihu.com/api/", FakeResponse(payload={}))]
        )
        client = ZhihuClient(transport=transport, cookies={"z_c0": "t"}, sleep=NoSleep())
        client.get_api("https://www.zhihu.com/api/v4/answers/2")
        call = transport.calls[0]
        self.assertEqual(call["timeout"], 30)
        self.assertEqual(call["cookies"], {"z_c0": "t"})
        # API 请求与页面请求的区分特征
        self.assertEqual(call["headers"]["x-requested-with"], "fetch")
        self.assertIn("User-Agent", call["headers"])

    def test_raises_on_http_error_status(self):
        transport = RecordingTransport(
            [("https://www.zhihu.com/api/", FakeResponse(status_ok=False))]
        )
        client = ZhihuClient(transport=transport, cookies={}, sleep=NoSleep())
        with self.assertRaises(requests.HTTPError):
            client.get_api("https://www.zhihu.com/api/v4/answers/2")


class GetPageTests(unittest.TestCase):
    def test_returns_page_html(self):
        transport = RecordingTransport(
            [("https://www.zhihu.com/question/", FakeResponse(text="<html>hi</html>"))]
        )
        client = ZhihuClient(transport=transport, cookies={}, sleep=NoSleep())
        self.assertEqual(
            client.get_page("https://www.zhihu.com/question/1/answer/2"), "<html>hi</html>"
        )

    def test_sends_page_headers_not_api_headers(self):
        transport = RecordingTransport(
            [("https://www.zhihu.com/question/", FakeResponse(text=""))]
        )
        client = ZhihuClient(transport=transport, cookies={}, sleep=NoSleep())
        client.get_page("https://www.zhihu.com/question/1/answer/2")
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers["Sec-Fetch-Dest"], "document")
        self.assertNotIn("x-requested-with", headers)

    def test_page_headers_only_advertise_supported_encodings(self):
        """回归：不得声明 Brotli/Zstandard，除非安装了对应解压依赖。"""
        self.assertEqual(zhihu_client.PAGE_HEADERS["Accept-Encoding"], "gzip, deflate")


class GetPageDataTests(unittest.TestCase):
    def test_parses_json_with_page_style_headers(self):
        """收藏夹分页接口用页面风格头部（无 x-requested-with）。"""
        transport = RecordingTransport(
            [("https://www.zhihu.com/api/v4/collections/", FakeResponse(payload={"data": []}))]
        )
        client = ZhihuClient(transport=transport, cookies={}, sleep=NoSleep())
        payload = client.get_page_data(
            "https://www.zhihu.com/api/v4/collections/1/items?offset=0&limit=20"
        )
        self.assertEqual(payload, {"data": []})
        headers = transport.calls[0]["headers"]
        self.assertNotIn("x-requested-with", headers)
        self.assertEqual(headers["Sec-Fetch-Dest"], "document")


class AuthenticationPreflightTests(unittest.TestCase):
    def test_anonymous_mode_does_not_request_user_api(self):
        def unexpected(**kwargs):
            raise AssertionError("anonymous preflight must not perform a request")

        client = ZhihuClient(transport=unexpected, cookies={}, auth_configured=False)
        status = preflight_authentication(client)
        self.assertTrue(status.anonymous)

    def test_valid_cookie_returns_authenticated_status(self):
        transport = RecordingTransport([
            (zhihu_client.ME_API_URL, FakeResponse(payload={"url_token": "user"}))
        ])
        client = ZhihuClient(transport=transport, cookies={"z_c0": "secret"})
        self.assertTrue(preflight_authentication(client).authenticated)
        self.assertEqual(len(transport.calls), 1)

    def test_401_is_authentication_failure_without_cookie_details(self):
        transport = RecordingTransport([
            (zhihu_client.ME_API_URL, FakeResponse(status_ok=False, status_code=401))
        ])
        client = ZhihuClient(transport=transport, cookies={"z_c0": "secret"})
        with self.assertRaises(AuthenticationError) as caught:
            preflight_authentication(client)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("z_c0", str(caught.exception))

    def test_403_is_indeterminate_not_expired_cookie(self):
        transport = RecordingTransport([
            (zhihu_client.ME_API_URL, FakeResponse(status_ok=False, status_code=403))
        ])
        client = ZhihuClient(transport=transport, cookies={"z_c0": "secret"})
        with self.assertRaises(AuthenticationCheckError):
            preflight_authentication(client)

    def test_missing_user_identity_is_authentication_failure(self):
        transport = RecordingTransport([
            (zhihu_client.ME_API_URL, FakeResponse(payload={}))
        ])
        client = ZhihuClient(transport=transport, cookies={"z_c0": "secret"})
        with self.assertRaises(AuthenticationError):
            preflight_authentication(client)

    def test_explicit_cookie_path_counts_as_configured_even_when_empty(self):
        with patch.object(zhihu_client, "_default_instance", None), patch.dict(
            zhihu_client.os.environ, {"ZHIHU_COOKIES_FILE": "missing-cookies.json"}
        ), patch("paths_config.load_cookies", return_value={}):
            self.assertTrue(zhihu_client.default_client().auth_configured)


class DownloadTests(unittest.TestCase):
    def test_returns_bytes_content(self):
        transport = RecordingTransport(
            [("https://pic.zhimg.com/", FakeResponse(content=b"imgbytes"))]
        )
        client = ZhihuClient(transport=transport, cookies={}, sleep=NoSleep())
        self.assertEqual(
            client.download("https://pic.zhimg.com/abc.jpg"), b"imgbytes"
        )

    def test_retries_with_backoff_then_raises_last_error(self):
        class AlwaysFails:
            def __call__(self, url=None, **kwargs):
                raise requests.ConnectionError("reset")

        sleeps = []
        client = ZhihuClient(
            transport=AlwaysFails(), cookies={}, sleep=sleeps.append
        )
        with self.assertRaises(requests.ConnectionError):
            client.download("https://pic.zhimg.com/abc.jpg", attempts=3)
        self.assertEqual(sleeps, [1, 2])

    def test_recovers_after_transient_failure(self):
        class Flaky:
            def __init__(self):
                self.count = 0

            def __call__(self, url=None, **kwargs):
                self.count += 1
                if self.count == 1:
                    raise requests.ConnectionError("reset")
                return FakeResponse(content=b"ok")

        flaky = Flaky()
        client = ZhihuClient(transport=flaky, cookies={}, sleep=NoSleep())
        self.assertEqual(client.download("https://pic.zhimg.com/x.jpg"), b"ok")


if __name__ == "__main__":
    unittest.main()
