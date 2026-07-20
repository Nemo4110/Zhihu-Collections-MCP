# -*- coding: utf-8 -*-
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from integrity import (
    atomic_write_text,
    canonicalize_url,
    parse_source_identity,
    sha256_file,
    sha256_text,
)


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


class IntegrityCoreTests(unittest.TestCase):
    def test_canonicalize_answer_url_removes_query_and_fragment(self):
        self.assertEqual(
            canonicalize_url("https://www.zhihu.com/question/1/answer/2?utm=x#section"),
            "https://www.zhihu.com/question/1/answer/2",
        )

    def test_canonicalize_article_url_normalizes_host(self):
        self.assertEqual(
            canonicalize_url("https://www.zhihu.com/p/99?native=0"),
            "https://zhuanlan.zhihu.com/p/99",
        )

    def test_parse_source_identity(self):
        self.assertEqual(
            parse_source_identity("https://zhuanlan.zhihu.com/p/99"),
            ("article", "99"),
        )
        self.assertEqual(
            parse_source_identity("https://www.zhihu.com/question/1/answer/2"),
            ("answer", "2"),
        )

    def test_parse_source_identity_rejects_unknown_url(self):
        with self.assertRaises(ValueError):
            parse_source_identity("https://www.zhihu.com/pin/1")

    def test_hashes_are_deterministic(self):
        with workspace_directory() as directory:
            path = directory / "value.txt"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(sha256_file(path), sha256_text("hello"))

    def test_atomic_write_text_replaces_destination_and_cleans_temp_file(self):
        with workspace_directory() as directory:
            path = directory / "article.md"
            path.write_text("old", encoding="utf-8")
            atomic_write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(list(directory.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
