# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

import main
from migrate_markdown_images import migrate_markdown


class StandardMarkdownImageTests(unittest.TestCase):
    def test_inline_equation_is_rendered_as_latex_without_download(self):
        image = BeautifulSoup(
            '<p>公式 <img src="https://www.zhihu.com/equation?tex=P%28O%29" alt="P(O)"> 内容</p>',
            "lxml",
        ).img
        with patch.object(main.requests, "get") as request_get:
            rendered = main.ObsidianStyleConverter().convert_img(image, "")
        self.assertEqual(rendered, "$P(O)$")
        request_get.assert_not_called()

    def test_standalone_equation_is_rendered_as_display_latex(self):
        image = BeautifulSoup(
            '<p><img src="https://www.zhihu.com/equation?tex=x%2By" alt="x+y"></p>',
            "lxml",
        ).img
        rendered = main.ObsidianStyleConverter().convert_img(image, "")
        self.assertEqual(rendered, "\n\n$$\nx+y\n$$\n\n")

    def test_migration_converts_inline_and_block_legacy_markup(self):
        source = (
            "正文 ![[equation]]\n(P(O))\n\n 后文。\n\n"
            "![[equation]]\n(x+y)\n\n"
            "![[chart.jpg]]\n(chart)\n\n"
        )
        migrated, stats = migrate_markdown(source)
        self.assertEqual(
            migrated,
            "正文 $P(O)$\n\n 后文。\n\n$$\nx+y\n$$\n\n![chart](assets/chart.jpg)\n\n",
        )
        self.assertEqual(stats.equations_changed, 2)
        self.assertEqual(stats.images_changed, 1)
        self.assertEqual(stats.unresolved_equations, 0)

    def test_long_inline_equation_is_promoted_to_display_math(self):
        tex = r"\frac{" + "x" * 80 + r"}{y}"
        source = f"核心公式： ![[equation]]\n({tex})\n\n变量解释。"
        migrated, stats = migrate_markdown(source)
        self.assertEqual(migrated, f"核心公式： \n\n$$\n{tex}\n$$\n\n变量解释。")
        self.assertEqual(stats.equations_changed, 1)



if __name__ == "__main__":
    unittest.main()
