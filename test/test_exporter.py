# -*- coding: utf-8 -*-
"""exporter 接缝的行为测试：输出根目录显式注入，无全局路径状态。

接缝说明：exporter 是导出引擎（verify/adopt/repair/refresh + 清单 +
报告 + 退出码）。网络经注入的 fetch/render 函数，磁盘位置经
output_root 参数；测试用假抓取函数 + 临时目录，无真实网络。
"""
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from integrity import (
    CollectionFetchResult,
    CollectionItem,
    ExportMode,
    IntegrityManifestStore,
    SourceMetadata,
    snapshot_from_html,
)
from exporter import (
    collection_output_dir,
    determine_exit_code,
    export_collection_with_integrity,
    save_integrity_report,
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


def make_fetch_result(url="https://www.zhihu.com/question/1/answer/2", title="回答甲"):
    result = CollectionFetchResult("777", 1)
    result.raw_item_count = 1
    result.exportable_items.append(
        CollectionItem(
            url=url,
            title=title,
            source_type="answer",
            source_id="2",
            updated_time=100,
        )
    )
    result.reconcile()
    assert result.complete, "测试夹具必须构造完整收藏夹"
    return result


class OutputRootTests(unittest.TestCase):
    def test_export_writes_under_explicit_output_root(self):
        with workspace_directory() as directory:
            root = directory / "导出根"
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_fetch_result(),
                output_root=root,
                fetch_snapshot_fn=lambda url: snapshot_from_html(
                    SourceMetadata(url, "answer", "2", 100), "answer_api", "<p>正文内容</p>"
                ),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            self.assertEqual(report["status"], "verified")
            markdown_files = list((root / "我的收藏").glob("*.md"))
            self.assertEqual(len(markdown_files), 1)
            self.assertIn("正文内容", markdown_files[0].read_text(encoding="utf-8"))
            manifest = root / "我的收藏" / ".zhihu-integrity.json"
            self.assertTrue(manifest.exists())

    def test_collection_name_is_sanitized_to_single_segment(self):
        # 收藏夹名可能含路径分隔符（如 "C/C++"），必须清理为安全目录名
        path = collection_output_dir(Path("root"), "C/C++:收藏")
        self.assertEqual(len(path.relative_to(Path("root")).parts), 1)

    def test_empty_collection_name_falls_back_to_placeholder(self):
        path = collection_output_dir(Path("root"), "  ")
        self.assertEqual(path.relative_to(Path("root")).as_posix(), "未命名收藏夹")


class ReportTests(unittest.TestCase):
    def test_exit_code_zero_when_all_verified(self):
        reports = [{"status": "verified", "collection": {"complete": True}, "items": []}]
        self.assertEqual(determine_exit_code(reports), 0)

    def test_exit_code_one_when_item_validation_fails(self):
        reports = [
            {
                "status": "invalid",
                "collection": {"complete": True},
                "items": [{"status": "render_invalid"}],
            }
        ]
        self.assertEqual(determine_exit_code(reports), 1)

    def test_exit_code_two_when_collection_incomplete(self):
        reports = [{"status": "collection_incomplete", "collection": {"complete": False}}]
        self.assertEqual(determine_exit_code(reports), 2)

    def test_report_saved_as_json_with_timestamp(self):
        import json

        with workspace_directory() as directory:
            path = save_integrity_report(
                [{"status": "verified"}], ExportMode.BALANCED, logs_dir=directory, timestamp="20240101_000000"
            )
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["collections"][0]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
