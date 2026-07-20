# -*- coding: utf-8 -*-
import shutil
import unittest
from unittest.mock import patch
import uuid
from contextlib import contextmanager
from pathlib import Path

import main
from integrity import (
    CollectionItem,
    ExportMode,
    IntegrityManifestStore,
    SourceMetadata,
    snapshot_from_html,
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


class ExportPipelineTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.zhihu.com/question/1/answer/2"
        self.item = CollectionItem("Example", self.url, "answer", "2")
        self.metadata = SourceMetadata(self.url, "answer", "2", 100)
        self.snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<h2>Start heading</h2><p>Complete middle paragraph.</p><p>Required final paragraph.</p>",
        )
        self.valid_markdown = (
            f"> {self.url}\n"
            "## Start heading\n\nComplete middle paragraph.\n\nRequired final paragraph.\n"
        )

    def make_store(self, directory):
        return IntegrityManifestStore.load(
            directory / ".zhihu-integrity.json",
            expected_collection_id="1",
            collection_url="https://www.zhihu.com/collection/1",
        )

    def call_pipeline(self, directory, store, mode, rendered=None):
        return main.export_item_with_integrity(
            self.item,
            directory,
            store,
            mode,
            fetch_metadata_fn=lambda _: self.metadata,
            fetch_snapshot_fn=lambda _: self.snapshot,
            render_markdown_fn=lambda snapshot, item: rendered or self.valid_markdown,
        )

    def test_valid_existing_file_without_manifest_is_adopted_without_rewrite(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            path.write_text(self.valid_markdown, encoding="utf-8")
            before = path.stat().st_mtime_ns
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.BALANCED)
            self.assertEqual(result["status"], "adopted")
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(store.records[self.url]["status"], "verified")

    def test_invalid_existing_file_is_repaired_in_balanced_mode(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            path.write_text(f"> {self.url}\nOnly the start.", encoding="utf-8")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.BALANCED)
            self.assertEqual(result["status"], "repaired")
            self.assertEqual(path.read_text(encoding="utf-8"), self.valid_markdown)

    def test_audit_only_reports_invalid_without_modifying_file(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            original = f"> {self.url}\nOnly the start."
            path.write_text(original, encoding="utf-8")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.AUDIT)
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertNotIn(self.url, store.records)

    def test_audit_repair_replaces_invalid_file(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            path.write_text(f"> {self.url}\nOnly the start.", encoding="utf-8")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.AUDIT_REPAIR)
            self.assertEqual(result["status"], "repaired")
            self.assertEqual(path.read_text(encoding="utf-8"), self.valid_markdown)

    def test_force_replaces_valid_file(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            old = self.valid_markdown + "\nLocal addition.\n"
            path.write_text(old, encoding="utf-8")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.FORCE)
            self.assertEqual(result["status"], "refreshed")
            self.assertEqual(path.read_text(encoding="utf-8"), self.valid_markdown)

    def test_invalid_generated_markdown_does_not_replace_previous_file(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            original = self.valid_markdown
            path.write_text(original, encoding="utf-8")
            store = self.make_store(directory)
            invalid_render = f"> {self.url}\nStart heading only."
            result = self.call_pipeline(
                directory,
                store,
                ExportMode.FORCE,
                rendered=invalid_render,
            )
            self.assertEqual(result["status"], "render_invalid")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertNotIn(self.url, store.records)


    def test_process_single_collection_routes_to_integrity_pipeline(self):
        report = {"name": "Example Collection", "status": "verified", "items": []}
        main.processing_log = []
        with patch.object(main, "export_collection_with_integrity", return_value=report) as export:
            with patch.object(main, "get_article_urls_in_collection", return_value=([], [])):
                returned = main.process_single_collection(
                    "Example Collection",
                    "https://www.zhihu.com/collection/1",
                )
        export.assert_called_once_with(
            "Example Collection",
            "https://www.zhihu.com/collection/1",
            mode=ExportMode.BALANCED,
        )
        self.assertIs(returned, report)
        self.assertEqual(main.processing_log, [report])

if __name__ == "__main__":
    unittest.main()
