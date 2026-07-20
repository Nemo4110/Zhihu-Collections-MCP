# -*- coding: utf-8 -*-
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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


class CookieLoadingTests(unittest.TestCase):
    def test_environment_cookie_path_is_supported(self):
        with workspace_directory() as directory:
            cookie_file = directory / "session.json"
            cookie_file.write_text(
                json.dumps([{"name": "example", "value": "secret"}]),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ZHIHU_COOKIES_FILE": str(cookie_file)}):
                loaded = main.load_cookies()
            self.assertEqual(loaded, {"example": "secret"})


if __name__ == "__main__":
    unittest.main()
