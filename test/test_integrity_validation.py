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
            "Text body long enough for validation.\n\n![](assets/a.jpg)\n"
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

    def test_equation_filename_is_unique_and_has_svg_extension(self):
        first = image_filename_from_url("https://www.zhihu.com/equation?tex=x")
        second = image_filename_from_url("https://www.zhihu.com/equation?tex=y")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("equation-"))
        self.assertTrue(first.endswith(".svg"))


    def test_rendered_src_is_preferred_over_data_original_for_asset_compatibility(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_page",
            '<img src="https://pic.zhimg.com/rendered_720w.jpg" data-original="https://pic.zhimg.com/original_r.jpg">',
        )
        self.assertEqual(
            snapshot.image_urls,
            ("https://pic.zhimg.com/rendered_720w.jpg",),
        )

    def test_data_placeholder_uses_data_original(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_page",
            '<img src="data:image/gif;base64,AAAA" data-original="https://pic.zhimg.com/real.jpg">',
        )
        self.assertEqual(snapshot.image_urls, ("https://pic.zhimg.com/real.jpg",))

    def test_inline_markdown_formatting_does_not_reduce_text_coverage(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<p>新品机制算法正在全面演进。</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "新品 **机制算法** 正在全面演进。\n"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.text_coverage, 1.0)

    def test_markdown_math_escaping_does_not_reduce_coverage(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "article_page",
            r"<p>预估 Potential\_score_{T+\Delta T} 并计算 \hat{y}_i 的结果。</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            r"预估 Potential\\_score\_{T+\Delta T} 并计算 \hat{y}\_i 的结果。"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.text_coverage, 1.0)

    def test_author_numeric_prefix_without_space_is_preserved(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "answer_api",
            "<p>1.召回侧：增加实时召回</p><p>2.粗排侧：样本去偏学习</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "1.召回侧：增加实时召回\n\n2.粗排侧：样本去偏学习"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)

    def test_numbered_paragraph_with_space_matches_markdown(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "article_page",
            "<p>1. 流式模型时时刻刻都在学习。</p><p>2. 批次模型按天更新。</p>",
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "1. **流式模型**时时刻刻都在学习。\n\n"
            "2. 批次模型按天更新。"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)

    def test_missing_middle_sentence_in_long_paragraph_is_detected(self):
        source = (
            "第一段句子用于确认开头完整。"
            "第二段句子是必须保留的中间内容，并且长度足够用于完整性检测。"
            "第三段句子用于确认结尾完整。"
        )
        snapshot = snapshot_from_html(self.metadata, "article_page", f"<p>{source}</p>")
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "第一段句子用于确认开头完整。第三段句子用于确认结尾完整。"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertFalse(result.valid)
        self.assertLess(result.text_coverage, 0.98)

    def test_short_heading_separated_by_image_is_validated_independently(self):
        snapshot = snapshot_from_html(
            self.metadata,
            "article_page",
            '<h2>3 实验</h2><img src="https://pic.zhimg.com/chart.jpg"><p>线上效果非常明显。</p>',
        )
        markdown = (
            "> https://www.zhihu.com/question/1/answer/2\n"
            "## 3 实验\n\n![](assets/chart.jpg)\n\n线上效果非常明显。"
        )
        with workspace_directory() as assets_dir:
            (assets_dir / "chart.jpg").write_bytes(b"image")
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)


    def test_inline_equation_image_does_not_reduce_text_coverage(self):
        image_url = "https://www.zhihu.com/equation?tex=P%28O%29"
        snapshot = snapshot_from_html(
            self.metadata,
            "article_api",
            '<p>那么应该如何预估 <img src="%s" alt="P[O](q)"> 呢？</p>' % image_url,
        )
        markdown = (
            f"> {self.metadata.canonical_url}\n"
            "那么应该如何预估 $P[O](q)$ 呢？"
        )
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertEqual(snapshot.image_urls, ())
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.text_coverage, 1.0)

    def test_ordinary_inline_image_splits_source_text_consistently(self):
        image_url = "https://pic.zhimg.com/chart.jpg"
        snapshot = snapshot_from_html(
            self.metadata,
            "article_api",
            '<p>图片前的重要文字<img src="%s" alt="chart">图片后的重要文字。</p>' % image_url,
        )
        markdown = (
            f"> {self.metadata.canonical_url}\n"
            "图片前的重要文字![chart](assets/chart.jpg)图片后的重要文字。"
        )
        with workspace_directory() as assets_dir:
            (assets_dir / "chart.jpg").write_bytes(b"image")
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.text_coverage, 1.0)

    def test_minor_text_difference_is_valid_with_warning(self):
        source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4
        rendered_text = source[:-5] + "12345"
        snapshot = snapshot_from_html(
            self.metadata,
            "article_api",
            f"<p>{source}</p>",
        )
        markdown = f"> {self.metadata.canonical_url}\n{rendered_text}"
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertGreaterEqual(result.text_coverage, 0.90)
        self.assertLess(result.text_coverage, 0.98)
        self.assertIn("low_text_coverage", [warning.code for warning in result.warnings])


    def test_scattered_characters_do_not_count_as_complete_text(self):
        source = "ABCDEFGHIJ" * 10
        scattered = "X".join(source)
        snapshot = snapshot_from_html(
            self.metadata,
            "article_api",
            f"<p>{source}</p>",
        )
        markdown = f"> {self.metadata.canonical_url}\n{scattered}"
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertFalse(result.valid)
        self.assertLess(result.text_coverage, 0.90)

    def test_substantial_text_difference_remains_invalid(self):
        source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4
        rendered_text = source[:60] + "0" * (len(source) - 60)
        snapshot = snapshot_from_html(
            self.metadata,
            "article_api",
            f"<p>{source}</p>",
        )
        markdown = f"> {self.metadata.canonical_url}\n{rendered_text}"
        with workspace_directory() as assets_dir:
            result = validate_markdown(snapshot, markdown, assets_dir)
        self.assertFalse(result.valid)
        self.assertLess(result.text_coverage, 0.90)
        self.assertIn("low_text_coverage", [issue.code for issue in result.issues])

if __name__ == "__main__":
    unittest.main()
