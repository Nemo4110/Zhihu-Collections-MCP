# -*- coding: utf-8 -*-
import json
import unittest

import main
from integrity import (
    SourceContentError,
    SourceMetadata,
    choose_best_snapshot,
    snapshot_from_html,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.ok = 200 <= status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class SourceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.answer_metadata = SourceMetadata(
            "https://www.zhihu.com/question/1/answer/2",
            "answer",
            "2",
            100,
        )
        self.article_metadata = SourceMetadata(
            "https://zhuanlan.zhihu.com/p/9",
            "article",
            "9",
            None,
        )

    def test_longer_candidate_wins(self):
        short = snapshot_from_html(self.answer_metadata, "page", "<p>Short text segment.</p>")
        long = snapshot_from_html(
            self.answer_metadata,
            "answer_api",
            "<p>Short text segment.</p><p>A much longer final segment from authenticated API.</p>",
        )
        self.assertEqual(choose_best_snapshot([short, long]).candidate, "answer_api")

    def test_image_only_candidate_is_valid(self):
        image = snapshot_from_html(
            self.answer_metadata,
            "answer_api",
            '<figure><img src="https://pic.zhimg.com/only.jpg"></figure>',
        )
        self.assertEqual(choose_best_snapshot([image]).candidate, "answer_api")

    def test_empty_candidates_raise(self):
        with self.assertRaises(SourceContentError):
            choose_best_snapshot([])

    def test_answer_page_initial_json_can_beat_short_visible_container(self):
        payload = {
            "initialState": {
                "entities": {
                    "answers": {
                        "2": {
                            "id": 2,
                            "updatedTime": 321,
                            "content": "<p>Long API-like content stored in initial state.</p><p>Required final paragraph.</p>",
                        }
                    }
                }
            }
        }
        html = (
            '<div class="RichContent-inner"><p>Short visible content.</p></div>'
            f'<script id="js-initialData" type="application/json">{json.dumps(payload)}</script>'
        )
        candidates = main.parse_answer_page_candidates(self.answer_metadata, html)
        best = choose_best_snapshot(candidates)
        self.assertEqual(best.candidate, "answer_initial_data")
        self.assertEqual(best.metadata.updated_time, 321)

    def test_article_initial_json_candidate_is_discovered(self):
        payload = {
            "initialState": {
                "entities": {
                    "articles": {
                        "9": {
                            "id": 9,
                            "updated": 456,
                            "content": "<p>Complete article content from initial data.</p><p>Article ending.</p>",
                        }
                    }
                }
            }
        }
        html = (
            '<div class="Post-RichText"><p>Short article.</p></div>'
            f'<script id="js-initialData" type="application/json">{json.dumps(payload)}</script>'
        )
        best = choose_best_snapshot(main.parse_article_page_candidates(self.article_metadata, html))
        self.assertEqual(best.candidate, "article_initial_data")
        self.assertEqual(best.metadata.updated_time, 456)

    def test_login_page_produces_no_article_candidates(self):
        html = "<html><title>登录 - 知乎</title><body>请登录后继续</body></html>"
        self.assertEqual(main.parse_article_page_candidates(self.article_metadata, html), [])

    def test_fetch_answer_metadata_reads_updated_time(self):
        def request_get(url, **kwargs):
            return FakeResponse(payload={"id": 2, "updated_time": 777})

        metadata = main.fetch_answer_metadata(
            "https://www.zhihu.com/question/1/answer/2",
            request_get=request_get,
        )
        self.assertEqual(metadata.updated_time, 777)

    def test_fetch_answer_snapshots_keeps_api_and_page_candidates(self):
        def request_get(url, **kwargs):
            if "include=updated_time" in url:
                return FakeResponse(payload={"id": 2, "updated_time": 777})
            if "/api/v4/answers/" in url:
                return FakeResponse(payload={"content": "<p>Long authenticated API content.</p>"})
            return FakeResponse(text='<div class="RichContent-inner"><p>Short page.</p></div>')

        snapshots = main.fetch_answer_snapshots(
            "https://www.zhihu.com/question/1/answer/2",
            request_get=request_get,
        )
        self.assertEqual({item.candidate for item in snapshots}, {"answer_api", "answer_page"})


    def test_article_parser_does_not_select_outer_footer_container(self):
        html = (
            '<div class="Post-content">'
            '<div class="Post-RichText"><p>Actual article body with required ending.</p></div>'
            '<div class="Footer"><p>Footer navigation repeated repeated repeated repeated.</p></div>'
            '</div>'
        )
        best = choose_best_snapshot(main.parse_article_page_candidates(self.article_metadata, html))
        self.assertIn("Actual article body", best.html)
        self.assertNotIn("Footer navigation", best.html)

if __name__ == "__main__":
    unittest.main()
