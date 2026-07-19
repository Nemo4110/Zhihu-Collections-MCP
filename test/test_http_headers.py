# -*- coding: utf-8 -*-
"""HTTP 请求头回归测试。"""
import unittest

import main


class HttpHeaderTests(unittest.TestCase):
    def test_only_advertises_encodings_supported_without_optional_dependencies(self):
        """不得声明 Brotli/Zstandard，除非项目安装了对应解压依赖。"""
        self.assertEqual(main.headers["Accept-Encoding"], "gzip, deflate")


if __name__ == "__main__":
    unittest.main()
