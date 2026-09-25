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
import render


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

            render.prefetch_images(
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

            render.prefetch_images(
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
                render.prefetch_images([], str(directory / "assets"))
                render.prefetch_images(None, str(directory / "assets"))
            request_get.assert_not_called()
