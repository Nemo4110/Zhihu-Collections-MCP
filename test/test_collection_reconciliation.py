# -*- coding: utf-8 -*-
import unittest

import main


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
        }
    }


def article_item(index):
    return {
        "content": {
            "type": "article",
            "url": f"https://zhuanlan.zhihu.com/p/{index}",
            "title": f"Article {index}",
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

        result = main.fetch_collection_items(
            "123",
            request_get=request_get,
            get_total=lambda _: 45,
            sleep=lambda _: None,
        )
        self.assertEqual(offsets, [0, 20, 40])
        self.assertTrue(result.complete, result)
        self.assertEqual(result.raw_item_count, 45)
        self.assertEqual(len(result.exportable_items), 45)

    def test_pin_is_explicitly_unsupported_without_failing_collection(self):
        payload = {
            "data": [
                answer_item(1),
                {"content": {"type": "pin", "url": "https://www.zhihu.com/pin/9"}},
            ]
        }
        result = main.fetch_collection_items(
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

        result = main.fetch_collection_items(
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
        result = main.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse(payload),
            get_total=lambda _: 3,
            sleep=lambda _: None,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.raw_item_count, 3)
        self.assertEqual(len(result.exportable_items), 2)
        self.assertEqual(len(result.duplicate_urls), 1)

    def test_short_api_page_is_incomplete(self):
        result = main.fetch_collection_items(
            "123",
            request_get=lambda *args, **kwargs: FakeResponse({"data": [answer_item(1)]}),
            get_total=lambda _: 2,
            sleep=lambda _: None,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.raw_item_count, 1)


if __name__ == "__main__":
    unittest.main()
