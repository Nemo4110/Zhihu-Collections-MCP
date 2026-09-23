# -*- coding: utf-8 -*-
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup

import main


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


class FakeImageResponse:
    def __init__(self, content=b"image-bytes"):
        self.content = content

    def raise_for_status(self):
        return None


class ImageDownloadRetryTests(unittest.TestCase):
    def make_image(self):
        return BeautifulSoup(
            '<img src="https://pic.zhimg.com/image.jpg" alt="chart">',
            "lxml",
        ).img

    def test_image_download_retries_transient_failure_then_succeeds(self):
        with workspace_directory() as directory:
            with (
                patch.object(main, "base_output_path", directory),
                patch.object(main, "current_collection_name", "Images"),
                patch.object(
                    main.requests,
                    "get",
                    side_effect=[requests.ConnectionError("reset"), FakeImageResponse()],
                ) as request_get,
                patch.object(main.time, "sleep") as sleep,
            ):
                rendered = main.ObsidianStyleConverter().convert_img(self.make_image(), "")

            asset = directory / "Images" / "assets" / "image.jpg"
            self.assertEqual(request_get.call_count, 2)
            sleep.assert_called_once_with(1)
            self.assertEqual(asset.read_bytes(), b"image-bytes")
            self.assertEqual(rendered, "![chart](assets/image.jpg)")

    def test_existing_non_empty_asset_skips_download_entirely(self):
        with workspace_directory() as directory:
            asset = directory / "Images" / "assets" / "image.jpg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"existing-image")

            with (
                patch.object(main, "base_output_path", directory),
                patch.object(main, "current_collection_name", "Images"),
                patch.object(main.requests, "get") as request_get,
            ):
                rendered = main.ObsidianStyleConverter().convert_img(self.make_image(), "")

            request_get.assert_not_called()
            self.assertEqual(asset.read_bytes(), b"existing-image")
            self.assertEqual(rendered, "![chart](assets/image.jpg)")

    def test_existing_non_empty_asset_is_reused_after_retry_exhaustion(self):
        with workspace_directory() as directory:
            asset = directory / "Images" / "assets" / "image.jpg"
            asset.parent.mkdir(parents=True)
            # 本地只有空文件（视为无效），下载重试耗尽后不能保留空文件覆盖语义
            asset.write_bytes(b"")

            with (
                patch.object(main, "base_output_path", directory),
                patch.object(main, "current_collection_name", "Images"),
                patch.object(
                    main.requests,
                    "get",
                    side_effect=requests.ConnectionError("reset"),
                ) as request_get,
                patch.object(main.time, "sleep") as sleep,
            ):
                with self.assertRaises(requests.ConnectionError):
                    main.ObsidianStyleConverter().convert_img(self.make_image(), "")

            self.assertEqual(request_get.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
            self.assertEqual(asset.stat().st_size, 0)


class PrefetchImagesTests(unittest.TestCase):
    def test_prefetch_downloads_missing_images_and_skips_existing(self):
        with workspace_directory() as directory:
            assets = directory / "assets"
            assets.mkdir()
            (assets / "have.jpg").write_bytes(b"cached")
            fetched = []

            def request_get(url=None, **kwargs):
                fetched.append(url)
                return FakeImageResponse(content=f"bytes-{url[-6:]}".encode())

            main.prefetch_images(
                [
                    "https://pic.zhimg.com/have.jpg",
                    "https://pic.zhimg.com/miss1.jpg",
                    "https://pic.zhimg.com/miss2.jpg",
                    "https://www.zhihu.com/equation?tex=x",  # 公式 URL 应跳过
                    None,
                ],
                str(assets),
                request_get=request_get,
                max_workers=2,
            )

            self.assertEqual(
                sorted(fetched),
                ["https://pic.zhimg.com/miss1.jpg", "https://pic.zhimg.com/miss2.jpg"],
            )
            self.assertEqual((assets / "have.jpg").read_bytes(), b"cached")
            self.assertTrue((assets / "miss1.jpg").stat().st_size > 0)
            self.assertTrue((assets / "miss2.jpg").stat().st_size > 0)

    def test_prefetch_failure_does_not_raise(self):
        with workspace_directory() as directory:
            def request_get(url=None, **kwargs):
                raise requests.ConnectionError("reset")

            main.prefetch_images(
                ["https://pic.zhimg.com/broken.jpg"],
                str(directory / "assets"),
                request_get=request_get,
                max_workers=1,
            )
            self.assertFalse((directory / "assets" / "broken.jpg").exists())


if __name__ == "__main__":
    unittest.main()

    def test_prefetch_noop_without_urls(self):
        with workspace_directory() as directory:
            with patch.object(main.requests, "get") as request_get:
                main.prefetch_images([], str(directory / "assets"))
                main.prefetch_images(None, str(directory / "assets"))
            request_get.assert_not_called()
