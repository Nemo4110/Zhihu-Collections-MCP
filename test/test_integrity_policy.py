# -*- coding: utf-8 -*-
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from integrity import (
    ExportMode,
    IntegrityAction,
    SourceMetadata,
    decide_action,
    local_record_is_intact,
    sha256_file,
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


class IntegrityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.metadata = SourceMetadata(
            "https://www.zhihu.com/question/1/answer/2",
            "answer",
            "2",
            200,
        )

    def test_missing_file_fetches_and_writes(self):
        self.assertEqual(
            decide_action(ExportMode.BALANCED, file_exists=False, record=None, local_intact=False, metadata=self.metadata),
            IntegrityAction.FETCH_AND_WRITE,
        )

    def test_existing_file_without_manifest_fetches_and_verifies(self):
        self.assertEqual(
            decide_action(ExportMode.BALANCED, file_exists=True, record=None, local_intact=False, metadata=self.metadata),
            IntegrityAction.FETCH_AND_VERIFY,
        )

    def test_unchanged_verified_file_skips(self):
        record = {"status": "verified", "source_updated_time": 200}
        self.assertEqual(
            decide_action(ExportMode.BALANCED, file_exists=True, record=record, local_intact=True, metadata=self.metadata),
            IntegrityAction.SKIP_VERIFIED,
        )


    def test_unchanged_warning_verified_file_skips(self):
        record = {"status": "verified_with_warnings", "source_updated_time": 200}
        self.assertEqual(
            decide_action(
                ExportMode.BALANCED,
                file_exists=True,
                record=record,
                local_intact=True,
                metadata=self.metadata,
            ),
            IntegrityAction.SKIP_VERIFIED,
        )

    def test_changed_remote_timestamp_fetches_and_writes(self):
        record = {"status": "verified", "source_updated_time": 100}
        self.assertEqual(
            decide_action(ExportMode.BALANCED, file_exists=True, record=record, local_intact=True, metadata=self.metadata),
            IntegrityAction.FETCH_AND_WRITE,
        )

    def test_audit_and_force_modes(self):
        record = {"status": "verified", "source_updated_time": 200}
        self.assertEqual(
            decide_action(ExportMode.AUDIT, file_exists=True, record=record, local_intact=True, metadata=self.metadata),
            IntegrityAction.FETCH_AND_AUDIT,
        )
        self.assertEqual(
            decide_action(ExportMode.AUDIT_REPAIR, file_exists=True, record=record, local_intact=True, metadata=self.metadata),
            IntegrityAction.FETCH_AND_AUDIT,
        )
        self.assertEqual(
            decide_action(ExportMode.FORCE, file_exists=True, record=record, local_intact=True, metadata=self.metadata),
            IntegrityAction.FETCH_AND_WRITE,
        )

    def test_local_record_checks_markdown_hash_and_assets(self):
        with workspace_directory() as directory:
            markdown = directory / "article.md"
            assets = directory / "assets"
            assets.mkdir()
            markdown.write_text("content", encoding="utf-8")
            (assets / "image.jpg").write_bytes(b"image")
            record = {
                "status": "verified",
                "markdown_sha256": sha256_file(markdown),
                "assets": [{"filename": "image.jpg", "status": "verified"}],
            }
            self.assertTrue(local_record_is_intact(record, markdown, assets))
            record["status"] = "verified_with_warnings"
            self.assertTrue(local_record_is_intact(record, markdown, assets))
            markdown.write_text("changed", encoding="utf-8")
            self.assertFalse(local_record_is_intact(record, markdown, assets))


if __name__ == "__main__":
    unittest.main()
