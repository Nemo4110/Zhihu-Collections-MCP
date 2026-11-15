# -*- coding: utf-8 -*-
"""远端删除同步（归档）接缝的行为测试。

接缝说明：收藏夹被远端删除时整个输出目录移入 .archived.collections/；
条目被移出收藏夹时（分页完整前提下）Markdown 与图片移入
.archived.items/<收藏夹>/，共享图片复制而非移动；--audit 严格只读
只报告不动文件。临时网络故障绝不触发归档。
"""
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests

import sources
from exporter import (
    archive_collection_dir,
    archive_removed_items,
    determine_exit_code,
    export_collection_with_integrity,
)
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


def make_http_error(status_code):
    """构造带 response.status_code 的 HTTPError（假传输抛出用）。"""
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(f"{status_code} error", response=response)


def make_fetch_result(items):
    result = CollectionFetchResult("777", len(items))
    for item in items:
        result.exportable_items.append(item)
        result.raw_item_count += 1
        result.reached_end = True
    result.reconcile()
    assert result.complete, "测试夹具必须构造完整收藏夹"
    return result


def make_item(url, title, item_id):
    # 收藏夹分页自带 updated_time：整项零网络请求，测试不触网
    return CollectionItem(
        url=url, title=title, source_type="answer", source_id=item_id, updated_time=100
    )


def make_mismatch_fetch_result(items, expected_total):
    """构造分页完整但对账不上的收藏夹（隐藏/失效条目仍计入 totals）。"""
    result = CollectionFetchResult("777", expected_total)
    for item in items:
        result.exportable_items.append(item)
        result.raw_item_count += 1
        result.reached_end = True
    result.reconcile()
    assert result.complete and result.total_mismatch, "夹具必须构造 total_mismatch"
    return result


def make_not_found_result(collection_id="777"):
    result = CollectionFetchResult(collection_id, 0)
    result.page_failures.append(
        {"offset": 0, "error": "404 Client Error: Not Found", "status_code": 404}
    )
    result.reconcile()
    assert result.not_found and not result.complete
    return result


def make_snapshot(url, item_id="2", updated=100):
    return snapshot_from_html(
        SourceMetadata(url, "answer", item_id, updated),
        "answer_api",
        "<p>正文内容</p>",
    )


class NotFoundDeterminationTests(unittest.TestCase):
    """not_found 判定必须严格：只有第一页 404 且从未取到数据才置位。"""

    def _result(self, failures, raw_item_count=0):
        result = CollectionFetchResult("777", 1)
        result.page_failures.extend(failures)
        result.raw_item_count = raw_item_count
        result.reconcile()
        return result

    def test_first_page_404_without_data_is_not_found(self):
        result = self._result([{"offset": 0, "error": "404", "status_code": 404}])
        self.assertTrue(result.not_found)
        self.assertFalse(result.complete)
        self.assertTrue(result.to_dict()["not_found"])

    def test_network_error_is_not_not_found(self):
        result = self._result([{"offset": 0, "error": "timeout", "status_code": None}])
        self.assertFalse(result.not_found)

    def test_server_error_is_not_not_found(self):
        result = self._result([{"offset": 0, "error": "500", "status_code": 500}])
        self.assertFalse(result.not_found)

    def test_mid_pagination_404_with_data_is_not_not_found(self):
        result = self._result(
            [{"offset": 20, "error": "404", "status_code": 404}], raw_item_count=20
        )
        self.assertFalse(result.not_found)

    def test_no_failures_is_not_not_found(self):
        result = self._result([])
        self.assertFalse(result.not_found)


class FetchCollectionNotFoundTests(unittest.TestCase):
    def test_collection_api_404_records_status_and_not_found(self):
        def transport(**kwargs):
            raise make_http_error(404)

        result = sources.fetch_collection_items(
            "777",
            request_get=transport,
            get_total=lambda _: (_ for _ in ()).throw(AssertionError("不应请求总数")),
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertTrue(result.not_found)
        self.assertEqual(result.page_failures[0]["status_code"], 404)

    def test_network_failure_records_none_status(self):
        def transport(**kwargs):
            raise requests.ConnectionError("refused")

        result = sources.fetch_collection_items(
            "777", request_get=transport, sleep=lambda _: None
        )
        self.assertFalse(result.complete)
        self.assertFalse(result.not_found)
        self.assertIsNone(result.page_failures[0]["status_code"])

    def test_totals_404_after_successful_page_is_not_not_found(self):
        """首页成功（收藏夹存在）但 totals 回退请求 404，绝不能误判为已删除。"""

        def transport(url=None, **kwargs):
            class FakeResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    # 首页 200：有 data 但 paging 缺少 totals，触发 get_total 回退
                    return {"data": [], "paging": {"is_end": True}}

            return FakeResponse()

        def get_total_404(collection_id):
            raise make_http_error(404)

        result = sources.fetch_collection_items(
            "777", request_get=transport, get_total=get_total_404, sleep=lambda _: None
        )
        self.assertFalse(result.complete)
        self.assertFalse(result.not_found)
        # 该分支不得携带 404 状态码（首页已证明收藏夹存在）
        self.assertNotIn("status_code", result.page_failures[0])


class EmptyCollectionDeletionTests(unittest.TestCase):
    """知乎删除收藏夹后 items API 仍返回 200+空列表；需元信息 API 二次确认。"""

    def _transport(self, meta_status):
        def transport(url=None, **kwargs):
            class FakeResponse:
                def __init__(self, status, payload):
                    self.status, self._payload = status, payload

                def raise_for_status(self):
                    if self.status >= 400:
                        raise make_http_error(self.status)

                def json(self):
                    return self._payload

            if "/items" in (url or ""):
                # 已删除/空收藏夹：items API 一律 200 + totals=0 + 空列表
                return FakeResponse(200, {"data": [], "paging": {"totals": 0, "is_end": True}})
            return FakeResponse(meta_status, {"error": {"name": "Not Found"}} if meta_status == 404 else {})

        return transport

    def test_empty_items_with_meta_404_confirms_deleted(self):
        result = sources.fetch_collection_items(
            "777", request_get=self._transport(404), sleep=lambda _: None
        )
        self.assertTrue(result.confirmed_deleted)
        self.assertTrue(result.not_found)
        self.assertFalse(result.complete)

    def test_empty_items_with_meta_200_is_live_empty_collection(self):
        result = sources.fetch_collection_items(
            "777", request_get=self._transport(200), sleep=lambda _: None
        )
        self.assertFalse(result.confirmed_deleted)
        self.assertFalse(result.not_found)
        self.assertTrue(result.complete)

    def test_empty_items_with_meta_error_stays_conservative(self):
        """元信息请求网络失败/5xx 时不得判定删除（可能是权限不足或瞬时故障）。"""
        result = sources.fetch_collection_items(
            "777", request_get=self._transport(503), sleep=lambda _: None
        )
        self.assertFalse(result.confirmed_deleted)
        self.assertFalse(result.not_found)
        self.assertTrue(result.complete)

    def test_confirmed_deleted_archives_collection_dir(self):
        """经 exporter 接缝验证：空列表+确认删除 → 收藏夹目录整体归档。"""
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            collection.mkdir()
            (collection / "文章.md").write_text("内容", encoding="utf-8")

            result = CollectionFetchResult("777", 0)
            result.confirmed_deleted = True
            result.reconcile()
            self.assertTrue(result.not_found)

            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=result,
                output_root=directory,
                allow_deletion_sync=True,
            )
            self.assertEqual(report["status"], "collection_archived")
            self.assertFalse(collection.exists())
            self.assertTrue(
                (directory / ".archived.collections" / "我的收藏" / "文章.md").exists()
            )


class RetryShortCircuitTests(unittest.TestCase):
    """404 是确定性失败，不应消耗重试与退避；其他错误仍按原策略重试。"""

    def test_collection_404_is_not_retried(self):
        calls = []

        def transport(**kwargs):
            calls.append(kwargs.get("url"))
            raise make_http_error(404)

        result = sources.fetch_collection_items(
            "777", request_get=transport, sleep=lambda _: None
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(result.not_found)

    def test_network_error_still_retries(self):
        calls = []

        def transport(**kwargs):
            calls.append(kwargs.get("url"))
            raise requests.ConnectionError("refused")

        sources.fetch_collection_items("777", request_get=transport, sleep=lambda _: None)
        self.assertEqual(len(calls), 3)

    def test_totals_404_is_not_retried(self):
        calls = []

        def transport(url=None, **kwargs):
            calls.append(url)
            raise make_http_error(404)

        total = sources.get_article_nums_of_collection(
            "777", request_get=transport, sleep=lambda _: None
        )
        self.assertIsNone(total)
        self.assertEqual(len(calls), 1)


class ArchiveCollectionDirTests(unittest.TestCase):
    def test_moves_whole_directory_with_manifest(self):
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            (collection / "assets").mkdir(parents=True)
            (collection / "文章.md").write_text("内容", encoding="utf-8")
            (collection / ".zhihu-integrity.json").write_text("{}", encoding="utf-8")

            target = archive_collection_dir(directory, "我的收藏")
            self.assertEqual(target, directory / ".archived.collections" / "我的收藏")
            self.assertFalse(collection.exists())
            self.assertTrue((target / "文章.md").exists())
            self.assertTrue((target / ".zhihu-integrity.json").exists())

    def test_missing_directory_returns_none(self):
        with workspace_directory() as directory:
            self.assertIsNone(archive_collection_dir(directory, "不存在"))

    def test_name_conflict_gets_unique_suffix(self):
        with workspace_directory() as directory:
            for _ in range(2):
                collection = directory / "我的收藏"
                collection.mkdir()
                target = archive_collection_dir(directory, "我的收藏")
                self.assertIsNotNone(target)
            names = sorted(p.name for p in (directory / ".archived.collections").iterdir())
            self.assertEqual(names, ["我的收藏", "我的收藏-2"])


class ExportCollectionArchiveTests(unittest.TestCase):
    def snapshot_fn(self, url):
        return make_snapshot(url)

    def render_fn(self, snapshot, item):
        return f"> {item.url}\n正文内容"

    def test_deleted_collection_archives_local_directory(self):
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            collection.mkdir()
            (collection / "文章.md").write_text("内容", encoding="utf-8")

            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_not_found_result(),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=self.snapshot_fn,
                render_markdown_fn=self.render_fn,
            )
            self.assertEqual(report["status"], "collection_archived")
            self.assertFalse(collection.exists())
            self.assertTrue(
                (directory / ".archived.collections" / "我的收藏" / "文章.md").exists()
            )
            self.assertEqual(determine_exit_code([report]), 0)

    def test_anonymous_not_found_never_archives_local_directory(self):
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            collection.mkdir()
            (collection / "文章.md").write_text("内容", encoding="utf-8")
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                fetch_result=make_not_found_result(),
                output_root=directory,
                allow_deletion_sync=False,
            )
            self.assertEqual(report["status"], "collection_incomplete")
            self.assertEqual(report["issues"][0]["code"], "deletion_unconfirmed")
            self.assertTrue((collection / "文章.md").exists())
            self.assertFalse((directory / ".archived.collections").exists())

    def test_deleted_collection_without_local_dir(self):
        with workspace_directory() as directory:
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_not_found_result(),
                output_root=directory,
                allow_deletion_sync=True,
            )
            self.assertEqual(report["status"], "collection_archived")
            self.assertIsNone(report["archive_path"])
            self.assertEqual(determine_exit_code([report]), 0)

    def test_audit_mode_does_not_move_collection_dir(self):
        """--audit 只读：远端已删除时仅报告，不得移动收藏夹目录。"""
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            collection.mkdir()
            (collection / "文章.md").write_text("内容", encoding="utf-8")

            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.AUDIT,
                fetch_result=make_not_found_result(),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=self.snapshot_fn,
                render_markdown_fn=self.render_fn,
            )
            self.assertEqual(report["status"], "collection_archived")
            self.assertTrue(report["audit_only"])
            self.assertTrue((collection / "文章.md").exists())
            self.assertFalse((directory / ".archived.collections").exists())
            self.assertEqual(determine_exit_code([report]), 0)

    def test_incomplete_collection_keeps_local_directory(self):
        with workspace_directory() as directory:
            collection = directory / "我的收藏"
            collection.mkdir()
            (collection / "文章.md").write_text("内容", encoding="utf-8")

            result = CollectionFetchResult("777", 1)
            result.page_failures.append({"offset": 0, "error": "timeout", "status_code": None})
            result.reconcile()
            self.assertFalse(result.complete)

            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=result,
                output_root=directory,
                allow_deletion_sync=True,
            )
            self.assertEqual(report["status"], "collection_incomplete")
            self.assertTrue((collection / "文章.md").exists())
            self.assertFalse((directory / ".archived.collections").exists())
            self.assertEqual(determine_exit_code([report]), 2)


class ArchiveRemovedItemsTests(unittest.TestCase):
    def _setup_collection(self, directory, records):
        """构造收藏夹目录：每条记录一个 md + 可选图片，并写入清单。"""
        collection = directory / "我的收藏"
        assets = collection / "assets"
        assets.mkdir(parents=True)
        manifest = IntegrityManifestStore.load(
            collection / ".zhihu-integrity.json",
            expected_collection_id="777",
            collection_url="https://www.zhihu.com/collection/777",
        )
        for record in records:
            (collection / record["markdown_path"]).write_text(
                f"> {record['url']}\n正文", encoding="utf-8"
            )
            for asset in record.get("assets", []):
                (assets / asset["filename"]).write_bytes(b"image-bytes")
            manifest.records[record["url"]] = dict(record)
        manifest.save()
        return collection, manifest

    def test_removed_item_files_move_and_manifest_record_dropped(self):
        with workspace_directory() as directory:
            collection, manifest = self._setup_collection(directory, [
                {
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "title": "回答甲",
                    "markdown_path": "回答甲.md",
                    "assets": [{"filename": "img-a.jpg", "status": "verified"}],
                },
                {
                    "url": "https://www.zhihu.com/question/1/answer/3",
                    "title": "回答乙",
                    "markdown_path": "回答乙.md",
                    "assets": [{"filename": "img-b.jpg", "status": "verified"}],
                },
            ])
            current = {"https://www.zhihu.com/question/1/answer/3"}

            archived = archive_removed_items(directory, "我的收藏", manifest, current)

            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0]["archived"])
            archive_dir = directory / ".archived.items" / "我的收藏"
            self.assertTrue((archive_dir / "回答甲.md").exists())
            self.assertTrue((archive_dir / "assets" / "img-a.jpg").exists())
            self.assertFalse((collection / "回答甲.md").exists())
            self.assertFalse((collection / "assets" / "img-a.jpg").exists())
            # 保留条目不动
            self.assertTrue((collection / "回答乙.md").exists())
            self.assertTrue((collection / "assets" / "img-b.jpg").exists())
            # 清单记录移除并落盘
            self.assertNotIn("https://www.zhihu.com/question/1/answer/2", manifest.records)
            reloaded = IntegrityManifestStore.load(
                collection / ".zhihu-integrity.json", expected_collection_id="777"
            )
            self.assertNotIn("https://www.zhihu.com/question/1/answer/2", reloaded.records)
            self.assertIn("https://www.zhihu.com/question/1/answer/3", reloaded.records)
            # 归档历史清单
            history = (archive_dir / ".archived-manifest.json").read_text(encoding="utf-8")
            self.assertIn("回答甲", history)

    def test_shared_image_is_copied_not_moved(self):
        with workspace_directory() as directory:
            collection, manifest = self._setup_collection(directory, [
                {
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "title": "回答甲",
                    "markdown_path": "回答甲.md",
                    "assets": [{"filename": "shared.jpg", "status": "verified"}],
                },
                {
                    "url": "https://www.zhihu.com/question/1/answer/3",
                    "title": "回答乙",
                    "markdown_path": "回答乙.md",
                    "assets": [{"filename": "shared.jpg", "status": "verified"}],
                },
            ])
            current = {"https://www.zhihu.com/question/1/answer/3"}

            archive_removed_items(directory, "我的收藏", manifest, current)

            # 归档处与原目录各有一份，保留条目不受影响
            self.assertTrue(
                (directory / ".archived.items" / "我的收藏" / "assets" / "shared.jpg").exists()
            )
            self.assertTrue((collection / "assets" / "shared.jpg").exists())

    def test_missing_local_file_only_drops_record(self):
        with workspace_directory() as directory:
            collection, manifest = self._setup_collection(directory, [
                {"url": "https://www.zhihu.com/question/1/answer/2",
                 "title": "回答甲", "markdown_path": "已删.md"},
            ])
            (collection / "已删.md").unlink()

            archived = archive_removed_items(
                directory, "我的收藏", manifest, {"https://www.zhihu.com/question/1/answer/3"}
            )
            self.assertEqual(len(archived), 1)
            self.assertFalse(archived[0]["archived"])
            self.assertEqual(manifest.records, {})

    def test_no_removed_items_is_noop(self):
        with workspace_directory() as directory:
            self._setup_collection(directory, [
                {"url": "https://www.zhihu.com/question/1/answer/2",
                 "title": "回答甲", "markdown_path": "回答甲.md"},
            ])
            manifest = IntegrityManifestStore.load(
                directory / "我的收藏" / ".zhihu-integrity.json", expected_collection_id="777"
            )
            archived = archive_removed_items(
                directory,
                "我的收藏",
                manifest,
                {"https://www.zhihu.com/question/1/answer/2"},
            )
            self.assertEqual(archived, [])
            self.assertFalse((directory / ".archived.items").exists())

    def test_manifest_save_failure_does_not_break_archival(self):
        """同步盘锁定导致清单落盘失败：归档不中断，文件照常移动。"""
        with workspace_directory() as directory:
            _, manifest = self._setup_collection(directory, [
                {"url": "https://www.zhihu.com/question/1/answer/2",
                 "title": "回答甲", "markdown_path": "回答甲.md"},
                {"url": "https://www.zhihu.com/question/1/answer/3",
                 "title": "回答乙", "markdown_path": "回答乙.md"},
            ])
            # 注入接缝：清单落盘抛同步盘锁定异常
            manifest.save = lambda: (_ for _ in ()).throw(PermissionError("locked"))

            archived = archive_removed_items(
                directory,
                "我的收藏",
                manifest,
                {"https://www.zhihu.com/question/1/answer/3"},
            )
            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0]["archived"])
            self.assertTrue(
                (directory / ".archived.items" / "我的收藏" / "回答甲.md").exists()
            )
            # 磁盘上的旧清单还在（下次运行会因文件缺失再次清理对齐）
            reloaded = IntegrityManifestStore.load(
                directory / "我的收藏" / ".zhihu-integrity.json",
                expected_collection_id="777",
            )
            self.assertIn("https://www.zhihu.com/question/1/answer/2", reloaded.records)


class ExportRemovedItemsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.url_removed = "https://www.zhihu.com/question/1/answer/2"
        self.url_kept = "https://www.zhihu.com/question/1/answer/3"

    def _prepare(self, directory):
        """先完整导出两个条目，建立本地文件与清单。"""
        items = [make_item(self.url_removed, "回答甲", "2"), make_item(self.url_kept, "回答乙", "3")]
        snapshots = {
            self.url_removed: make_snapshot(self.url_removed, "2"),
            self.url_kept: make_snapshot(self.url_kept, "3"),
        }
        export_collection_with_integrity(
            "我的收藏",
            "https://www.zhihu.com/collection/777",
            mode=ExportMode.BALANCED,
            fetch_result=make_fetch_result(items),
            output_root=directory,
            fetch_snapshot_fn=lambda url: snapshots[url],
            render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
        )
        collection = directory / "我的收藏"
        assert (collection / "回答甲.md").exists()
        return collection

    def test_removed_item_archived_on_next_sync(self):
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_fetch_result([make_item(self.url_kept, "回答乙", "3")]),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            self.assertFalse((collection / "回答甲.md").exists())
            self.assertTrue((directory / ".archived.items" / "我的收藏" / "回答甲.md").exists())
            self.assertTrue((collection / "回答乙.md").exists())
            self.assertEqual(len(report["archived_items"]), 1)
            self.assertEqual(report["archived_items"][0]["url"], self.url_removed)
            self.assertEqual(determine_exit_code([report]), 0)

    def test_anonymous_removed_item_is_only_reported(self):
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                fetch_result=make_fetch_result([make_item(self.url_kept, "回答乙", "3")]),
                output_root=directory,
                allow_deletion_sync=False,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            self.assertTrue((directory / "我的收藏" / "回答甲.md").exists())
            self.assertFalse((directory / ".archived.items").exists())
            self.assertEqual(len(report["archivable_items"]), 1)

    def test_total_mismatch_anonymous_removed_item_is_only_reported(self):
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                fetch_result=make_mismatch_fetch_result(
                    [make_item(self.url_kept, "回答乙", "3")], expected_total=3
                ),
                output_root=directory,
                allow_deletion_sync=False,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            self.assertTrue((collection / "回答甲.md").exists())
            self.assertFalse((directory / ".archived.items").exists())
            self.assertEqual(
                [entry["url"] for entry in report["archivable_items"]],
                [self.url_removed],
            )
            manifest = IntegrityManifestStore.load(
                collection / ".zhihu-integrity.json",
                expected_collection_id="777",
            )
            self.assertIn(self.url_removed, manifest.records)

    def test_total_mismatch_does_not_block_item_archival(self):
        """完整可见列表外的条目可逆归档，total_mismatch 仍保留为警告。"""
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_mismatch_fetch_result(
                    [make_item(self.url_kept, "回答乙", "3")], expected_total=3
                ),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            self.assertFalse((collection / "回答甲.md").exists())
            self.assertTrue((directory / ".archived.items" / "我的收藏" / "回答甲.md").exists())
            self.assertTrue((collection / "回答乙.md").exists())
            self.assertEqual(
                [entry["url"] for entry in report["archived_items"]],
                [self.url_removed],
            )
            self.assertEqual(
                report["collection"]["total_mismatch"],
                {"expected": 3, "actual": 1},
            )
            self.assertEqual(report["status"], "verified_with_warnings")
            self.assertEqual(determine_exit_code([report]), 0)
            manifest = IntegrityManifestStore.load(
                collection / ".zhihu-integrity.json",
                expected_collection_id="777",
            )
            self.assertNotIn(self.url_removed, manifest.records)
            self.assertIn(self.url_kept, manifest.records)

            history_path = (
                directory / ".archived.items" / "我的收藏" / ".archived-manifest.json"
            )
            history_before = history_path.read_text(encoding="utf-8")
            second_report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.BALANCED,
                fetch_result=make_mismatch_fetch_result(
                    [make_item(self.url_kept, "回答乙", "3")], expected_total=3
                ),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            self.assertNotIn("archived_items", second_report)
            self.assertEqual(history_path.read_text(encoding="utf-8"), history_before)

    def test_audit_mode_reports_without_moving(self):
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.AUDIT,
                fetch_result=make_fetch_result([make_item(self.url_kept, "回答乙", "3")]),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            # 只读审计：文件与清单不动，仅报告可归档条目
            self.assertTrue((collection / "回答甲.md").exists())
            self.assertFalse((directory / ".archived.items").exists())
            self.assertEqual(
                [entry["url"] for entry in report["archivable_items"]],
                [self.url_removed],
            )

    def test_total_mismatch_audit_reports_without_moving(self):
        with workspace_directory() as directory:
            self._prepare(directory)
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                mode=ExportMode.AUDIT,
                fetch_result=make_mismatch_fetch_result(
                    [make_item(self.url_kept, "回答乙", "3")], expected_total=3
                ),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            self.assertTrue((collection / "回答甲.md").exists())
            self.assertFalse((directory / ".archived.items").exists())
            self.assertEqual(
                [entry["url"] for entry in report["archivable_items"]],
                [self.url_removed],
            )
            manifest = IntegrityManifestStore.load(
                collection / ".zhihu-integrity.json",
                expected_collection_id="777",
            )
            self.assertIn(self.url_removed, manifest.records)

    def test_reappearing_item_is_downloaded_after_archival(self):
        """条目重新可见时重新导出活动副本，归档历史保持不动。"""
        with workspace_directory() as directory:
            self._prepare(directory)
            export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                fetch_result=make_fetch_result([make_item(self.url_kept, "回答乙", "3")]),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(url),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            items = [
                make_item(self.url_removed, "回答甲", "2"),
                make_item(self.url_kept, "回答乙", "3"),
            ]
            report = export_collection_with_integrity(
                "我的收藏",
                "https://www.zhihu.com/collection/777",
                fetch_result=make_fetch_result(items),
                output_root=directory,
                allow_deletion_sync=True,
                fetch_snapshot_fn=lambda url: make_snapshot(
                    url, "2" if url == self.url_removed else "3"
                ),
                render_markdown_fn=lambda snapshot, item: f"> {item.url}\n正文内容",
            )
            collection = directory / "我的收藏"
            self.assertTrue((collection / "回答甲.md").exists())
            self.assertTrue(
                (directory / ".archived.items" / "我的收藏" / "回答甲.md").exists()
            )
            item_report = next(
                item for item in report["items"] if item["url"] == self.url_removed
            )
            self.assertEqual(item_report["status"], "downloaded")
            manifest = IntegrityManifestStore.load(
                collection / ".zhihu-integrity.json",
                expected_collection_id="777",
            )
            self.assertIn(self.url_removed, manifest.records)


if __name__ == "__main__":
    unittest.main()
