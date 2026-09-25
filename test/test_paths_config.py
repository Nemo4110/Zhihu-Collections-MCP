# -*- coding: utf-8 -*-
"""paths_config 接缝的行为测试：配置加载、路径解析、Cookie 加载。

接缝：paths_config 是纯文件/路径模块，无网络、无知乎逻辑。
"""
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import paths_config


@contextmanager
def workspace_directory():
    root = Path.cwd() / ".test-tmp"
    root.mkdir(exist_ok=True)
    directory = root / str(uuid.uuid4())
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class LoadConfigTests(unittest.TestCase):
    def test_reads_config_json_when_present(self):
        with workspace_directory() as directory:
            config_file = directory / "config.json"
            config_file.write_text(
                json.dumps({"zhihuUrls": [{"name": "收藏", "url": "https://www.zhihu.com/collection/1"}]}),
                encoding="utf-8",
            )
            config = paths_config.load_config(config_file=str(config_file))
            self.assertEqual(len(config["zhihuUrls"]), 1)
            self.assertEqual(config["zhihuUrls"][0]["name"], "收藏")

    def test_falls_back_to_legacy_zhihu_urls_file(self):
        with workspace_directory() as directory:
            legacy = directory / "zhihuUrls.json"
            legacy.write_text(
                json.dumps([{"name": "旧版", "url": "https://www.zhihu.com/collection/2"}]),
                encoding="utf-8",
            )
            config = paths_config.load_config(
                config_file=str(directory / "config.json"),
                legacy_file=str(legacy),
            )
            self.assertEqual(config["zhihuUrls"][0]["name"], "旧版")
            self.assertEqual(config["outputPath"], "")

    def test_returns_empty_collections_when_no_files_exist(self):
        with workspace_directory() as directory:
            config = paths_config.load_config(
                config_file=str(directory / "missing.json"),
                legacy_file=str(directory / "missing-legacy.json"),
            )
            self.assertEqual(config, {"zhihuUrls": [], "outputPath": "", "os": ""})


class LoadCookiesTests(unittest.TestCase):
    def test_reads_list_of_name_value_pairs_into_dict(self):
        with workspace_directory() as directory:
            cookie_file = directory / "session.json"
            cookie_file.write_text(
                json.dumps([{"name": "z_c0", "value": "token"}]), encoding="utf-8"
            )
            self.assertEqual(
                paths_config.load_cookies(str(cookie_file)), {"z_c0": "token"}
            )

    def test_environment_cookie_path_is_supported(self):
        with workspace_directory() as directory:
            cookie_file = directory / "session.json"
            cookie_file.write_text(
                json.dumps([{"name": "example", "value": "secret"}]), encoding="utf-8"
            )
            with patch.dict("os.environ", {"ZHIHU_COOKIES_FILE": str(cookie_file)}):
                loaded = paths_config.load_cookies()
            self.assertEqual(loaded, {"example": "secret"})

    def test_missing_cookie_file_yields_empty_dict_for_anonymous_mode(self):
        with workspace_directory() as directory:
            self.assertEqual(
                paths_config.load_cookies(str(directory / "nope.json")), {}
            )


class ParseOutputPathTests(unittest.TestCase):
    def test_empty_path_returns_none(self):
        self.assertIsNone(paths_config.parse_output_path("", "windows"))

    def test_windows_path_uses_backslashes_and_resolves_drive(self):
        result = paths_config.parse_output_path("D:/导出/收藏", "windows")
        self.assertIsNotNone(result)
        self.assertEqual(str(result)[0].upper(), "D")
        self.assertIn("导出", str(result))

    def test_unix_home_path_expands_tilde(self):
        result = paths_config.parse_output_path("~/zhihu-out", "linux")
        self.assertIsNotNone(result)
        self.assertTrue(str(result).startswith(str(Path.home())))

    def test_cygdrive_path_maps_to_windows_drive(self):
        result = paths_config.parse_output_path("/cygdrive/c/Users/share", "cygwin")
        self.assertIsNotNone(result)
        rendered = str(result)
        self.assertTrue(rendered.startswith("C:"), rendered)
        self.assertNotIn("cygdrive", rendered)

    def test_unknown_os_still_resolves_path(self):
        result = paths_config.parse_output_path("relative/out", "unknown-os")
        self.assertIsNotNone(result)
        self.assertTrue(str(result).endswith("out"))


class GetCurrentOsTests(unittest.TestCase):
    def test_returns_one_of_the_known_os_names(self):
        self.assertIn(paths_config.get_current_os(), {"windows", "macos", "linux", "unknown"})


if __name__ == "__main__":
    unittest.main()
