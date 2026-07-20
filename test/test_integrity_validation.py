# -*- coding: utf-8 -*-
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from integrity import (
    SourceMetadata,
    image_filename_from_url,
    snapshot_from_html,
    validate_markdown,
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


class IntegrityValidationTests(unittest.TestCase):
    def setUp(self):
        self.metadata = SourceMetadata(
            canonical_url="https://www.zhihu.com/question/1/answer/2",
            source_type="answer",
            source_id="2",
            updated_time=123,
        )

    def test_missing_last_paragraph_fails_coverage(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<h2>Start section heading</h2>"
            "<p>Middle paragraph with enough text for comparison.</p>"
            "<p>Final required paragraph that must survive conversion.</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "## Start section heading\n\n"
            "Middle paragraph with enough text for comparison.\n"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertFalse(result.valid)
        self.assertIn("missing_last_segment", [issue.code for issue in result.issues])
        self.assertLess(result.text_coverage, 0.98)

    def test_complete_markdown_passes(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<h2>Start section heading</h2>"
            "<p>Middle paragraph with enough text for comparison.</p>"
            "<p>Final required paragraph that must survive conversion.</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "## Start section heading\n\n"
            "Middle paragraph with enough text for comparison.\n\n"
            "Final required paragraph that must survive conversion.\n"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.text_coverage, 1.0)

    def test_missing_source_url_fails(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<p>A complete visible paragraph for validation.</p>",
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(
                snapshot,
                "A complete visible paragraph for validation.",
                assets_dir,
            )
        self.assertIn("missing_source_url", [issue.code for issue in result.issues])

    def test_missing_image_fails_and_existing_nonempty_image_passes(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            '<p>Text body long enough for validation.</p><img src="https://pic.zhimg.com/a.jpg?source=x">',
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "Text body long enough for validation.\n\n![[a.jpg]]\n"
        )
        with workspace_directory() as assets_dir:
            missing = validate_markdown(snapshot, markdown, assets_dir)
            self.assertIn("missing_asset", [issue.code for issue in missing.issues])
            (assets_dir / "a.jpg").write_bytes(b"image")
            present = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(present.valid, present.issues)
        self.assertEqual(present.assets[0]["status"], "verified")

    def test_image_filename_ignores_query_string(self):
        self.assertEqual(
            image_filename_from_url("https://pic.zhimg.com/path/image.jpg?source=abc"),
            "image.jpg",
        )


if __name__ == "__main__":
    unittest.main()
