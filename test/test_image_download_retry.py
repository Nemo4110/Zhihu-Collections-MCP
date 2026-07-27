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
            self.assertIn("![[image.jpg]]", rendered)

    def test_existing_non_empty_asset_is_reused_after_retry_exhaustion(self):
        with workspace_directory() as directory:
            asset = directory / "Images" / "assets" / "image.jpg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"existing-image")

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
                rendered = main.ObsidianStyleConverter().convert_img(self.make_image(), "")

            self.assertEqual(request_get.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
            self.assertEqual(asset.read_bytes(), b"existing-image")
            self.assertIn("![[image.jpg]]", rendered)


if __name__ == "__main__":
    unittest.main()
