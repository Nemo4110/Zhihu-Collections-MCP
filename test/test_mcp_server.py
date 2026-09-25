# -*- coding: utf-8 -*-
"""mcp_server 接缝的行为测试：工具调用路由到深模块接口。

接缝说明：MCP 层是 I/O 外壳，可测行为是"参数 → 正确的模块调用 →
文本结果"。导出引擎与抓取层在此处打桩（系统边界）。
"""
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_server
from integrity import CollectionFetchResult, CollectionItem


def make_fetch_result():
    result = CollectionFetchResult("123", 2)
    result.raw_item_count = 2
    result.exportable_items = [
        CollectionItem(
            url="https://www.zhihu.com/question/1/answer/2",
            title="回答一",
            source_type="answer",
            source_id="2",
            updated_time=1,
        ),
        CollectionItem(
            url="https://zhuanlan.zhihu.com/p/3",
            title="文章二",
            source_type="article",
            source_id="3",
            updated_time=2,
        ),
    ]
    result.reconcile()
    assert result.complete
    return result


def run(coro):
    return asyncio.run(coro)


class ExportCollectionHandlerTests(unittest.TestCase):
    def test_routes_to_integrity_pipeline_with_explicit_output_root(self):
        report = {"name": "收藏", "status": "verified", "items": []}
        with patch.object(
            mcp_server.exporter,
            "export_collection_with_integrity",
            return_value=report,
        ) as export:
            text = run(
                mcp_server.export_collection_handler(
                    {
                        "collection_url": "https://www.zhihu.com/collection/123",
                        "collection_name": "收藏",
                        "output_dir": "C:/tmp/导出",
                    }
                )
            )[0].text
        export.assert_called_once()
        args, kwargs = export.call_args
        self.assertEqual(args[0], "收藏")
        self.assertEqual(args[1], "https://www.zhihu.com/collection/123")
        # 输出目录显式传入导出引擎，不再依赖模块全局副作用
        self.assertIsNotNone(kwargs.get("output_root"))
        self.assertIn("✅", text)

    def test_reports_failure_when_pipeline_raises(self):
        with patch.object(
            mcp_server.exporter,
            "export_collection_with_integrity",
            side_effect=RuntimeError("boom"),
        ):
            text = run(
                mcp_server.export_collection_handler(
                    {"collection_url": "https://www.zhihu.com/collection/123"}
                )
            )[0].text
        self.assertIn("boom", text)


class GetCollectionInfoHandlerTests(unittest.TestCase):
    def test_reports_item_count_and_titles_from_fetch_result(self):
        with patch.object(
            mcp_server.sources, "fetch_collection_items", return_value=make_fetch_result()
        ):
            text = run(
                mcp_server.get_collection_info_handler(
                    {"collection_url": "https://www.zhihu.com/collection/123"}
                )
            )[0].text
        self.assertIn("2", text)
        self.assertIn("回答一", text)
        self.assertIn("文章二", text)

    def test_requires_collection_url(self):
        text = run(mcp_server.get_collection_info_handler({}))[0].text
        self.assertIn("collection_url", text)


class CallToolDispatchTests(unittest.TestCase):
    def test_unknown_tool_is_reported(self):
        text = run(mcp_server.call_tool("nope", {}))[0].text
        self.assertIn("未知工具", text)


if __name__ == "__main__":
    unittest.main()
