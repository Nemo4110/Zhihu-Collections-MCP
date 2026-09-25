# -*- coding: utf-8 -*-
import shutil
import time
import unittest
from unittest.mock import patch
import uuid
from contextlib import contextmanager
from pathlib import Path

import main
import exporter
from integrity import (
    CollectionFetchResult,
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
        return exporter.export_item_with_integrity(
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
            path.write_text(self.valid_markdown, encoding="utf-8", newline="")
            before = path.stat().st_mtime_ns
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.BALANCED)
            self.assertEqual(result["status"], "adopted")
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(store.records[self.url]["status"], "verified")

    def test_favlist_timestamp_match_skips_metadata_request(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            path.write_text(self.valid_markdown, encoding="utf-8", newline="")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.BALANCED)
            self.assertEqual(result["status"], "adopted")
            self.assertEqual(store.records[self.url]["source_updated_time"], 100)

            # 收藏夹分页自带的时间戳与清单一致时，不应再发起任何网络请求
            item_with_time = CollectionItem("Example", self.url, "answer", "2", 100)
            result = exporter.export_item_with_integrity(
                item_with_time,
                directory,
                store,
                ExportMode.BALANCED,
                fetch_metadata_fn=lambda _: (_ for _ in ()).throw(AssertionError("不应请求元数据")),
                fetch_snapshot_fn=lambda _: (_ for _ in ()).throw(AssertionError("不应抓取内容")),
                render_markdown_fn=lambda snapshot, item: self.valid_markdown,
            )
            self.assertEqual(result["status"], "skipped_verified")

    def test_favlist_timestamp_change_triggers_refetch(self):
        with workspace_directory() as directory:
            path = directory / "Example.md"
            path.write_text(self.valid_markdown, encoding="utf-8", newline="")
            store = self.make_store(directory)
            result = self.call_pipeline(directory, store, ExportMode.BALANCED)
            self.assertEqual(result["status"], "adopted")

            # 时间戳变化应触发重新抓取，而不是跳过
            item_stale = CollectionItem("Example", self.url, "answer", "2", 200)
            result = exporter.export_item_with_integrity(
                item_stale,
                directory,
                store,
                ExportMode.BALANCED,
                fetch_metadata_fn=lambda _: self.metadata,
                fetch_snapshot_fn=lambda _: self.snapshot,
                render_markdown_fn=lambda snapshot, item: self.valid_markdown,
            )
            self.assertNotEqual(result["status"], "skipped_verified")
            # 清单记录的是重新抓取到的快照元数据时间戳
            self.assertEqual(store.records[self.url]["source_updated_time"], 100)

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


    def test_warning_valid_render_is_written_and_persisted(self):
        source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4
        rendered_text = source[:-5] + "12345"
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            f"<p>{source}</p>",
        )
        rendered = f"> {self.url}\n{rendered_text}"
        with workspace_directory() as directory:
            store = self.make_store(directory)
            result = exporter.export_item_with_integrity(
                self.item,
                directory,
                store,
                ExportMode.BALANCED,
                fetch_metadata_fn=lambda _: self.metadata,
                fetch_snapshot_fn=lambda _: snapshot,
                render_markdown_fn=lambda current_snapshot, item: rendered,
            )

            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["warnings"][0]["code"], "low_text_coverage")
            self.assertEqual(store.records[self.url]["status"], "verified_with_warnings")
            self.assertEqual(store.records[self.url]["warnings"][0]["code"], "low_text_coverage")
            self.assertTrue((directory / "Example.md").exists())


    def test_collection_total_mismatch_is_reported_as_warning_status(self):
        fetch_result = CollectionFetchResult("1", 2)
        fetch_result.add_raw_item(
            {
                "content": {
                    "type": "answer",
                    "url": self.url,
                    "question": {"title": "Example"},
                }
            }
        )
        fetch_result.reached_end = True
        fetch_result.reconcile()

        with workspace_directory() as directory:
            with patch.object(
                exporter,
                "export_item_with_integrity",
                return_value={
                    "name": "Example",
                    "url": self.url,
                    "status": "skipped_verified",
                    "warnings": [],
                },
            ):
                report = exporter.export_collection_with_integrity(
                    "Collection",
                    "https://www.zhihu.com/collection/1",
                    fetch_result=fetch_result,
                    output_root=directory,
                )

        self.assertEqual(report["status"], "verified_with_warnings")
        self.assertEqual(
            report["collection"]["total_mismatch"],
            {"expected": 2, "actual": 1},
        )

    def test_concurrent_export_processes_items_in_parallel(self):
        fetch_result = CollectionFetchResult("1", 3)
        for index in range(3):
            fetch_result.add_raw_item(
                {"content": {
                    "type": "answer",
                    "url": f"https://www.zhihu.com/question/1/answer/{index + 10}",
                    "question": {"title": f"Concurrent {index}"},
                    "updated_time": 1700000000 + index,
                }}
            )
        fetch_result.reached_end = True
        fetch_result.reconcile()
        self.assertTrue(fetch_result.complete)

        def slow_export(item, *args, **kwargs):
            time.sleep(0.3)
            return {
                "name": item.title,
                "url": item.url,
                "status": "downloaded",
                "warnings": [],
            }

        started = time.monotonic()
        with patch.object(exporter, "export_item_with_integrity", side_effect=slow_export):
            report = exporter.export_collection_with_integrity(
                "Concurrent",
                "https://www.zhihu.com/collection/1",
                fetch_result=fetch_result,
                output_root=Path.cwd() / ".test-tmp",
            )
        elapsed = time.monotonic() - started

        # 3 项各耗时 0.3s：串行需 >=0.9s，并发（3 workers）应接近 0.3s
        self.assertEqual(len(report["items"]), 3)
        self.assertLess(elapsed, 0.8)


if __name__ == "__main__":
    unittest.main()
