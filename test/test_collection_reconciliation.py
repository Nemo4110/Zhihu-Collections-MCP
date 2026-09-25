# -*- coding: utf-8 -*-
import unittest

import sources
from integrity import CollectionFetchResult


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def answer_item(index):
    return {
        "content": {
            "type": "answer",
            "url": f"https://www.zhihu.com/question/1/answer/{index}",
            "question": {"title": f"Answer {index}"},
            "updated_time": 1700000000 + index,
        }
    }


def article_item(index):
    return {
        "content": {
            "type": "article",
            "url": f"https://zhuanlan.zhihu.com/p/{index}",
            "title": f"Article {index}",
            "updated": 1800000000 + index,
        }
    }


class CollectionReconciliationTests(unittest.TestCase):
    def test_fetches_every_page_and_reconciles_total(self):
        offsets = []

        def request_get(url, **kwargs):
            offset = int(url.split("offset=")[1].split("&")[0])
            offsets.append(offset)
            count = 20 if offset < 40 else 5
            return FakeResponse({"data": [answer_item(offset + i) for i in range(count)]})

        result = sources.fetch_collection_items(
            "123",
            request_get=request_get,
            get_total=lambda _: 45,
            sleep=lambda _: None,
        )
        self.assertEqual(offsets, [0, 20, 40])
        self.assertTrue(result.complete, result)
        self.assertEqual(result.raw_item_count, 45)
        self.assertEqual(len(result.exportable_items), 45)


    def test_api_end_allows_explicit_reported_total_gap(self):
        offsets = []
        pages = {
            0: {
                "data": [answer_item(index) for index in range(19)],
                "paging": {
                    "is_end": False,
                    "next": "https://www.zhihu.com/api/v4/collections/123/items?limit=20&offset=20",
                },
            },
            20: {
                "data": [answer_item(index) for index in range(19, 39)],
                "paging": {
                    "is_end": False,
                    "next": "https://www.zhihu.com/api/v4/collections/123/items?limit=20&offset=40",
                },
            },
            40: {
                "data": [answer_item(index) for index in range(39, 48)],
                "paging": {"is_end": True},
            },
        }

        def request_get(url, **kwargs):
            offset = int(url.split("offset=")[1].split("&")[0])
            offsets.append(offset)
            return FakeResponse(pages[offset])

        result = sources.fetch_collection_items(
            "123",
            request_get=request_get,
            get_total=lambda _: 49,
            sleep=lambda _: None,
        )

        self.assertEqual(offsets, [0, 20, 40])
        self.assertTrue(result.complete, result)
        self.assertTrue(result.reached_end)
        self.assertEqual(result.raw_item_count, 48)
        self.assertEqual(result.total_mismatch, {"expected": 49, "actual": 48})

    def test_pin_is_explicitly_unsupported_without_failing_collection(self):
        payload = {
            "data": [
                answer_item(1),
                {"content": {"type": "pin", "url": "https://www.zhihu.com/pin/9"}},
            ]
        }
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse(payload),
            get_total=lambda _: 2,
            sleep=lambda _: None,
        )
        self.assertTrue(result.complete)
        self.assertEqual(len(result.exportable_items), 1)
        self.assertEqual(len(result.unsupported_items), 1)
        self.assertEqual(result.unsupported_items[0].source_type, "pin")

    def test_middle_page_failure_returns_incomplete_not_partial_success(self):
        attempts = []

        def request_get(url, **kwargs):
            offset = int(url.split("offset=")[1].split("&")[0])
            attempts.append(offset)
            if offset == 20:
                return FakeResponse(error=RuntimeError("network down"))
            return FakeResponse({"data": [answer_item(offset + i) for i in range(20)]})

        result = sources.fetch_collection_items(
            "123",
            request_get=request_get,
            get_total=lambda _: 40,
            sleep=lambda _: None,
            attempts=3,
        )
        self.assertFalse(result.complete)
        self.assertEqual(attempts.count(20), 3)
        self.assertEqual(len(result.page_failures), 1)
        self.assertEqual(len(result.exportable_items), 20)

    def test_duplicates_are_recorded_and_raw_count_still_reconciles(self):
        payload = {"data": [answer_item(1), answer_item(1), article_item(3)]}
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse(payload),
            get_total=lambda _: 3,
            sleep=lambda _: None,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.raw_item_count, 3)
        self.assertEqual(len(result.exportable_items), 2)
        self.assertEqual(len(result.duplicate_urls), 1)


    def test_empty_final_page_cannot_reconcile_nonzero_total(self):
        payload = {"data": [], "paging": {"is_end": True}}
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse(payload),
            get_total=lambda _: 5,
            sleep=lambda _: None,
        )

        self.assertFalse(result.complete)
        self.assertEqual(result.raw_item_count, 0)
        self.assertIsNone(result.total_mismatch)

    def test_short_api_page_is_incomplete(self):
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse({"data": [answer_item(1)]}),
            get_total=lambda _: 2,
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.raw_item_count, 1)


    def test_malformed_supported_item_makes_collection_incomplete(self):
        payload = {
            "data": [
                {"content": {"type": "article", "url": "https://zhuanlan.zhihu.com/p/7"}}
            ]
        }
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse(payload),
            get_total=lambda _: 1,
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.malformed_items), 1)

    def test_total_request_failure_is_incomplete(self):
        def get_total(_):
            raise RuntimeError("total unavailable")

        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse({"data": []}),
            get_total=get_total,
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.page_failures), 1)

    def test_collection_total_retries_and_returns_none_on_failure(self):
        attempts = []

        def request_get(url, **kwargs):
            attempts.append(url)
            return FakeResponse(error=RuntimeError("total down"))

        total = sources.get_article_nums_of_collection(
            "123",
            request_get=request_get,
            sleep=lambda _: None,
            attempts=3,
        )
        self.assertIsNone(total)
        self.assertEqual(len(attempts), 3)

    def test_collection_total_uses_timeout_and_reads_paging_total(self):
        seen = {}

        def request_get(url, **kwargs):
            seen.update(kwargs)
            return FakeResponse({"paging": {"totals": 7}})

        total = sources.get_article_nums_of_collection(
            "123",
            request_get=request_get,
            sleep=lambda _: None,
        )
        self.assertEqual(total, 7)
        self.assertEqual(seen["timeout"], 30)

    def test_none_total_is_incomplete(self):
        result = sources.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse({"data": []}),
            get_total=lambda _: None,
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.page_failures[0]["error"], "collection_total_unavailable")

    def test_add_raw_item_extracts_answer_and_article_updated_time(self):
        result = CollectionFetchResult("123", 2)
        result.add_raw_item(answer_item(5))
        result.add_raw_item(article_item(7))
        answer, article = result.exportable_items
        self.assertEqual(answer.updated_time, 1700000005)
        self.assertEqual(article.updated_time, 1800000007)

    def test_add_raw_item_allows_missing_updated_time(self):
        result = CollectionFetchResult("123", 1)
        result.add_raw_item(
            {"content": {
                "type": "answer",
                "url": "https://www.zhihu.com/question/1/answer/9",
                "question": {"title": "No timestamp"},
            }}
        )
        self.assertIsNone(result.exportable_items[0].updated_time)

    def test_first_page_paging_totals_avoids_extra_total_request(self):
        offsets = []
        pages = {
            0: {
                "data": [answer_item(index) for index in range(20)],
                "paging": {
                    "totals": 25,
                    "is_end": False,
                    "next": "https://www.zhihu.com/api/v4/collections/123/items?limit=20&offset=20",
                },
            },
            20: {
                "data": [answer_item(index) for index in range(20, 25)],
                "paging": {"totals": 25, "is_end": True},
            },
        }

        def request_get(url, **kwargs):
            offset = int(url.split("offset=")[1].split("&")[0])
            offsets.append(offset)
            return FakeResponse(pages[offset])

        def fail_get_total(_):
            raise AssertionError("第一页已含 paging.totals，不应再请求总数")

        result = sources.fetch_collection_items(
            "123",
            request_get=request_get,
            get_total=fail_get_total,
            sleep=lambda _: None,
        )
        self.assertEqual(offsets, [0, 20])
        self.assertTrue(result.complete)
        self.assertEqual(result.raw_item_count, 25)
        self.assertEqual(result.expected_total, 25)

if __name__ == "__main__":
    unittest.main()
